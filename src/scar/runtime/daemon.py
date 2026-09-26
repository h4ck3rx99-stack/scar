"""Background daemon: hosts the runtime + IPC server for long-running tasks, reminders and monitors.

Also: Windows Task Scheduler logon registration (``scar daemon install``), which is HIGH risk and needs the
user's explicit confirmation in the CLI.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import signal
import subprocess
import sys
from pathlib import Path
from typing import Any

import structlog

from scar.config.settings import Settings
from scar.core.events import Event
from scar.runtime.ipc import Connection, InstanceLock, IpcServer
from scar.runtime.runtime import Runtime
from scar.security.approval import ApprovalError, ApprovalResponse

log = structlog.get_logger("scar.daemon")

TASK_NAME = "SCAR Runtime"


def _status_payload(rt: Runtime) -> dict[str, Any]:
    from scar.cli.status import collect_status

    return collect_status(rt.services, rt.tasks)


class DaemonHandler:
    """Serves IPC requests against a running Runtime."""

    def __init__(self, rt: Runtime, stop: asyncio.Event) -> None:
        self.rt = rt
        self.stop = stop

    async def __call__(self, first: dict[str, Any], conn: Connection) -> None:
        op = first.get("op")
        s = self.rt.services
        tm = self.rt.tasks
        assert tm is not None
        if op == "ping":
            await conn.send({"type": "pong", "pid": os.getpid()})
        elif op == "status":
            await conn.send({"type": "status", "status": _status_payload(self.rt)})
        elif op == "cancel":
            ids = tm.cancel(first.get("task_id"))
            await conn.send({"type": "ok", "cancelled": ids})
        elif op == "stop":
            await conn.send({"type": "ok"})
            self.stop.set()
        elif op == "killswitch":
            ran = s.killswitch.trigger("ipc")
            await conn.send({"type": "ok", "ran": ran})
        elif op == "submit":
            await self._submit(first, conn)
        elif op == "events":
            await self._events(conn)
        else:
            await conn.send({"type": "error", "error": f"unknown op {op}"})

    async def _events(self, conn: Connection) -> None:
        s = self.rt.services
        s.approvals.attach_channel("ipc-client")
        s.questions.attach_channel()
        try:
            async with s.bus.subscribe() as q:
                reader = asyncio.create_task(self._client_commands(conn, None))
                while not reader.done():
                    try:
                        ev = await asyncio.wait_for(q.get(), timeout=1.0)
                    except TimeoutError:
                        continue
                    await conn.send(self._event_msg(ev))
        finally:
            s.approvals.detach_channel("ipc-client")
            s.questions.detach_channel()

    def _event_msg(self, ev: Event) -> dict[str, Any]:
        msg: dict[str, Any] = {"type": "event", "event": ev.model_dump(mode="json")}
        if ev.kind == "approval_requested":
            req = self.rt.services.approvals.get(getattr(ev, "request_id", ""))
            if req is not None:
                msg["approval"] = req.model_dump(mode="json")
                msg["approval"]["allowed_responses"] = [r.value for r in req.allowed_responses()]
        return msg

    async def _client_commands(self, conn: Connection, task_id: str | None) -> None:
        s = self.rt.services
        while True:
            msg = await conn.recv()
            if msg is None:
                return
            op = msg.get("op")
            try:
                if op == "approve":
                    s.approvals.resolve(str(msg["request_id"]), ApprovalResponse(msg["response"]), "ipc-client",
                                        typed_confirmation=msg.get("typed"))
                    await conn.send({"type": "ack", "op": op})
                elif op == "answer":
                    s.questions.answer(str(msg["question_id"]), str(msg["text"]))
                    await conn.send({"type": "ack", "op": op})
                elif op == "cancel":
                    self.rt.tasks.cancel(msg.get("task_id") or task_id)  # type: ignore[union-attr]
                    await conn.send({"type": "ack", "op": op})
            except (ApprovalError, KeyError, ValueError) as exc:
                await conn.send({"type": "error", "error": str(exc)})

    async def _submit(self, first: dict[str, Any], conn: Connection) -> None:
        s = self.rt.services
        tm = self.rt.tasks
        assert tm is not None
        interactive = bool(first.get("interactive", True))
        if interactive:
            s.approvals.attach_channel("ipc-client")
            s.questions.attach_channel()
        try:
            async with s.bus.subscribe() as q:
                handle = await tm.submit(str(first["objective"]), origin=str(first.get("origin", "text")),
                                         background=bool(first.get("background", False)), autonomy=first.get("autonomy"),
                                         dry_run=first.get("dry_run"))
                await conn.send({"type": "accepted", "task_id": handle.task.task_id})
                if first.get("background"):
                    return
                reader = asyncio.create_task(self._client_commands(conn, handle.task.task_id))
                assert handle.future is not None
                while not handle.future.done():
                    try:
                        ev = await asyncio.wait_for(q.get(), timeout=0.5)
                    except TimeoutError:
                        if reader.done():  # client went away: its task keeps running in the daemon
                            return
                        continue
                    if ev.task_id in (None, handle.task.task_id) or ev.kind in ("approval_requested", "question_asked"):
                        await conn.send(self._event_msg(ev))
                task = handle.future.result()
                while not q.empty():
                    ev = q.get_nowait()
                    if ev.task_id == task.task_id:
                        await conn.send(self._event_msg(ev))
                await conn.send({"type": "result", "task": {"task_id": task.task_id, "status": task.status.value,
                                                            "summary": task.result_summary,
                                                            "verified": task.verification.verified if task.verification else None}})
                reader.cancel()
        finally:
            if interactive:
                s.approvals.detach_channel("ipc-client")
                s.questions.detach_channel()


async def serve(settings: Settings) -> int:
    lock = InstanceLock(settings.data_path / "runtime.lock")
    if not lock.acquire():
        print("Another SCAR runtime is already running for this data folder.")
        return 1
    rt = Runtime(settings)
    stop = asyncio.Event()
    server = IpcServer(settings.data_path, DaemonHandler(rt, stop))
    try:
        await rt.start()
        await server.start()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            with contextlib.suppress(NotImplementedError, RuntimeError):
                loop.add_signal_handler(sig, stop.set)
        if rt.startup_notes and rt.services.notifier is not None:
            await rt.services.notifier.notify("SCAR started", "; ".join(rt.startup_notes)[:240], speak=False)
        await stop.wait()
    finally:
        await server.stop()
        await rt.stop()
        lock.release()
    return 0


def spawn_detached(extra_args: list[str] | None = None) -> int:
    """Start `scar daemon run` in the background without a console window. Returns the PID."""
    exe = Path(sys.executable)
    pythonw = exe.with_name("pythonw.exe") if exe.name.lower() == "python.exe" else exe
    if not pythonw.exists():
        pythonw = exe
    flags = 0x00000008 | 0x00000200 | 0x08000000 if sys.platform == "win32" else 0  # DETACHED | NEW_GROUP | NO_WINDOW
    proc = subprocess.Popen([str(pythonw), "-m", "scar", "daemon", "run", *(extra_args or [])], creationflags=flags,
                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True,
                            cwd=os.getcwd())
    return proc.pid


def logon_task_command() -> str:
    exe = Path(sys.executable)
    pythonw = exe.with_name("pythonw.exe") if exe.with_name("pythonw.exe").exists() else exe
    return f'"{pythonw}" -m scar daemon run'


def install_logon_task() -> tuple[bool, str]:
    cmd = ["schtasks", "/Create", "/F", "/SC", "ONLOGON", "/RL", "LIMITED", "/TN", TASK_NAME, "/TR", logon_task_command()]
    r = subprocess.run(cmd, capture_output=True, text=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return r.returncode == 0, (r.stdout + r.stderr).strip()


def uninstall_logon_task() -> tuple[bool, str]:
    r = subprocess.run(["schtasks", "/Delete", "/F", "/TN", TASK_NAME], capture_output=True, text=True,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return r.returncode == 0, (r.stdout + r.stderr).strip()


def logon_task_installed() -> bool:
    r = subprocess.run(["schtasks", "/Query", "/TN", TASK_NAME], capture_output=True, text=True,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return r.returncode == 0
