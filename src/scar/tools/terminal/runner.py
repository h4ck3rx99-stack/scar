"""Process runner shared by terminal, dev and code tools (C9.2).

* argv lists only (PowerShell scripts go through ``-EncodedCommand``; CMD
  command lines through a temporary ``.cmd`` file) — never ``shell=True``
* stdout/stderr captured separately with byte caps; full output streamed to
  an artifact file when it exceeds the cap
* mandatory timeout; idle detection for programs waiting on input
* cancellation and timeouts kill the whole process tree (Job Object)
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import os
import shutil
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from scar.config.settings import SECRET_KEYS
from scar.core.cancel import CancelToken
from scar.core.errors import ToolError
from scar.core.events import TaskProgress
from scar.security.command_guard import powershell_call_form
from scar.security.redaction import global_redactor

Shell = Literal["powershell", "pwsh", "cmd"]


@dataclass
class ProcessOutcome:
    argv: list[str]
    cwd: str
    exit_code: int | None
    duration_s: float
    stdout: str
    stderr: str
    stdout_bytes: int
    stderr_bytes: int
    timed_out: bool = False
    idle_killed: bool = False
    cancelled: bool = False
    stdout_ref: str | None = None
    stderr_ref: str | None = None
    pid: int | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not (self.timed_out or self.cancelled or self.idle_killed)


def child_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    """Inherit the environment minus SCAR-managed secrets; add caller vars (never secrets)."""
    env = {k: v for k, v in os.environ.items() if k.upper() not in SECRET_KEYS and not k.upper().startswith("SCAR_IPC")}
    env["PYTHONIOENCODING"] = "utf-8"
    env.setdefault("NO_COLOR", "1")
    env.setdefault("GIT_TERMINAL_PROMPT", "0")
    # children must not inherit SCAR's own virtualenv (user projects would pick up SCAR's Python)
    venv = env.pop("VIRTUAL_ENV", None) or (sys.prefix if sys.prefix != sys.base_prefix else None)
    if venv:
        scripts = {os.path.normcase(os.path.join(venv, "Scripts")), os.path.normcase(os.path.join(venv, "bin"))}
        key = next((k for k in env if k.upper() == "PATH"), "PATH")
        env[key] = os.pathsep.join(p for p in env.get(key, "").split(os.pathsep) if os.path.normcase(p) not in scripts)
    for k, v in (extra or {}).items():
        if k.upper() in SECRET_KEYS:
            raise ToolError(f"refusing to pass secret {k} to a child process", "SecretInEnv")
        env[k] = v
    return env


def shell_argv(command: str, shell: Shell) -> tuple[list[str], Path | None]:
    """Build an argv (and optional temp file) that runs ``command`` in ``shell`` without shell=True."""
    if shell in ("powershell", "pwsh"):
        exe = shutil.which("pwsh" if shell == "pwsh" else "powershell") or shutil.which("powershell") or shutil.which("pwsh")
        if exe is None:
            raise ToolError("PowerShell is not available on this system", "ShellMissing")
        script = ("$ProgressPreference='SilentlyContinue'; [Console]::OutputEncoding=[Text.Encoding]::UTF8; "
                  + powershell_call_form(command))
        encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
        return [exe, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-EncodedCommand", encoded], None
    if sys.platform != "win32":
        return ["/bin/sh", "-c", command], None
    fd, path = tempfile.mkstemp(suffix=".cmd", prefix="scar_")
    with os.fdopen(fd, "w", encoding="utf-8", newline="\r\n") as fh:
        fh.write("@echo off\nchcp 65001>nul\n" + command + "\n")
    return [os.environ.get("COMSPEC", "cmd.exe"), "/d", "/c", path], Path(path)


class _Capture:
    def __init__(self, cap: int, spill: Path | None) -> None:
        self.cap = cap
        self.head = bytearray()
        self.tail = bytearray()
        self.total = 0
        self.spill_path = spill
        self._spill = None
        self.last_line = ""

    def feed(self, chunk: bytes) -> None:
        self.total += len(chunk)
        if len(self.head) < self.cap:
            take = self.cap - len(self.head)
            self.head += chunk[:take]
            chunk_rest = chunk[take:]
        else:
            chunk_rest = chunk
        if chunk_rest:
            self.tail += chunk_rest
            if len(self.tail) > self.cap // 4:
                del self.tail[: len(self.tail) - self.cap // 4]
        if self.total > self.cap and self.spill_path is not None:
            if self._spill is None:
                self.spill_path.parent.mkdir(parents=True, exist_ok=True)
                self._spill = self.spill_path.open("wb")
                self._spill.write(bytes(self.head))
                self._spill.write(chunk_rest)
            else:
                self._spill.write(chunk)
        text = chunk.decode("utf-8", errors="replace").strip().splitlines()
        if text:
            self.last_line = text[-1][:160]

    def close(self) -> None:
        if self._spill is not None:
            self._spill.close()

    @property
    def spilled(self) -> bool:
        return self._spill is not None

    def text(self) -> str:
        head = bytes(self.head).decode("utf-8", errors="replace")
        if self.total <= self.cap:
            return head
        tail = bytes(self.tail).decode("utf-8", errors="replace")
        return f"{head}\n…[{self.total - len(self.head) - len(self.tail)} bytes omitted]…\n{tail}"


async def run_process(
    argv: list[str],
    *,
    services: object,
    cancel: CancelToken,
    cwd: str | None = None,
    env: dict[str, str] | None = None,
    timeout: float = 120.0,
    idle_timeout: float | None = None,
    output_cap: int = 64 * 1024,
    task_id: str | None = None,
    stdin_text: str | None = None,
    stream_label: str | None = None,
) -> ProcessOutcome:
    svc = services
    pm = svc.processes  # type: ignore[attr-defined]
    artifacts = svc.artifacts  # type: ignore[attr-defined]
    bus = svc.bus  # type: ignore[attr-defined]
    workdir = cwd or os.getcwd()
    if not Path(workdir).is_dir():
        raise ToolError(f"working directory does not exist: {workdir}", "NotFound")
    t0 = time.monotonic()
    stdin = asyncio.subprocess.PIPE if stdin_text is not None else asyncio.subprocess.DEVNULL
    try:
        proc, mp = await pm.spawn(argv, cwd=workdir, env=child_env(env), owner_task=task_id, stdin=stdin)
    except FileNotFoundError as exc:
        raise ToolError(f"program not found: {argv[0]}", "NotFound") from exc
    except OSError as exc:
        raise ToolError(f"cannot start {argv[0]}: {exc}", "StartFailed") from exc
    spill_dir = artifacts.root / (task_id or "adhoc")
    stamp = f"{int(time.time() * 1000)}_{mp.pid}"
    out = _Capture(output_cap, spill_dir / f"stdout_{stamp}.txt")
    err = _Capture(output_cap, spill_dir / f"stderr_{stamp}.txt")
    last_activity = time.monotonic()
    last_progress = 0.0

    async def pump(stream: asyncio.StreamReader | None, cap: _Capture) -> None:
        nonlocal last_activity, last_progress
        if stream is None:
            return
        while True:
            chunk = await stream.read(8192)
            if not chunk:
                return
            cap.feed(chunk)
            last_activity = time.monotonic()
            if stream_label and last_activity - last_progress > 2.0 and cap.last_line:
                last_progress = last_activity
                bus.publish(TaskProgress(task_id=task_id, milestone=False,
                                         message=f"{stream_label}: {global_redactor().redact(cap.last_line)}"))

    if stdin_text is not None and proc.stdin is not None:
        proc.stdin.write(stdin_text.encode("utf-8"))
        with contextlib.suppress(ConnectionError):
            await proc.stdin.drain()
        proc.stdin.close()

    pumps = asyncio.gather(pump(proc.stdout, out), pump(proc.stderr, err))
    waiter = asyncio.ensure_future(proc.wait())
    cancel_wait = asyncio.ensure_future(cancel.wait())
    timed_out = idle_killed = cancelled = False
    deadline = t0 + timeout
    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                timed_out = True
                break
            done, _ = await asyncio.wait({waiter, cancel_wait}, timeout=min(1.0, remaining), return_when=asyncio.FIRST_COMPLETED)
            if waiter in done:
                break
            if cancel_wait in done:
                cancelled = True
                break
            if idle_timeout and time.monotonic() - last_activity > idle_timeout:
                idle_killed = True
                break
    finally:
        if timed_out or cancelled or idle_killed:
            pm.kill(mp.pid)
        with contextlib.suppress(asyncio.TimeoutError, TimeoutError):
            await asyncio.wait_for(waiter, timeout=10)
        with contextlib.suppress(asyncio.TimeoutError, TimeoutError):
            await asyncio.wait_for(pumps, timeout=5)
        cancel_wait.cancel()
        out.close()
        err.close()
        if not (timed_out or cancelled or idle_killed):
            # the root exited; kill any stragglers it left in the job (e.g. detached children) only if requested
            pm.forget(mp.pid)
    outcome = ProcessOutcome(
        argv=argv, cwd=workdir, exit_code=proc.returncode, duration_s=round(time.monotonic() - t0, 3),
        stdout=global_redactor().redact(out.text()), stderr=global_redactor().redact(err.text()),
        stdout_bytes=out.total, stderr_bytes=err.total, timed_out=timed_out, idle_killed=idle_killed,
        cancelled=cancelled, pid=mp.pid,
    )
    for cap, attr in ((out, "stdout_ref"), (err, "stderr_ref")):
        if cap.spilled and cap.spill_path is not None:
            setattr(outcome, attr, f"artifact://{cap.spill_path.parent.name}/{cap.spill_path.stem}{cap.spill_path.suffix}")
    if timed_out:
        outcome.notes.append(f"timed out after {timeout:.0f}s; process tree killed")
    if idle_killed:
        outcome.notes.append(f"no output for {idle_timeout:.0f}s; it may be waiting for input (interactive programs are "
                             "not supported) — process tree killed")
    if cancelled:
        outcome.notes.append("cancelled; process tree killed")
    return outcome
