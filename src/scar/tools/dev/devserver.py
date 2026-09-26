"""Managed development servers: start, readiness (output pattern + HTTP probe), logs, crash detection, stop."""

from __future__ import annotations

import asyncio
import contextlib
import re
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any

import httpx
import structlog

from scar.core.events import MonitorFired, TaskProgress
from scar.core.ids import new_id
from scar.runtime.jobs import ManagedProcess
from scar.security.redaction import global_redactor
from scar.tools.terminal.runner import child_env

log = structlog.get_logger("scar.devserver")

DEFAULT_READY = r"(?i)(ready|listening|started server|server started|running at|running on|compiled successfully|local:\s+http|serving)"
URL_RE = re.compile(r"https?://(?:localhost|127\.0\.0\.1|0\.0\.0\.0|\[::1\])(?::\d+)?[^\s'\"]*")


@dataclass
class DevServer:
    id: str
    argv: list[str]
    cwd: str
    ready_pattern: str
    url: str | None
    proc: asyncio.subprocess.Process
    managed: ManagedProcess
    started: float = field(default_factory=time.time)
    lines: deque[str] = field(default_factory=lambda: deque(maxlen=2000))
    ready_line: str | None = None
    ready_at: float | None = None
    http_status: int | None = None
    exit_code: int | None = None
    stopped_by_user: bool = False
    ready_event: asyncio.Event = field(default_factory=asyncio.Event)
    exited_event: asyncio.Event = field(default_factory=asyncio.Event)
    task_id: str | None = None
    label: str = ""

    def status(self) -> str:
        if self.exit_code is not None:
            return "stopped" if self.stopped_by_user else f"crashed (exit {self.exit_code})"
        return "ready" if self.ready_at else "starting"

    def info(self) -> dict[str, Any]:
        return {"id": self.id, "label": self.label, "pid": self.managed.pid, "cwd": self.cwd, "status": self.status(),
                "url": self.url, "ready_line": self.ready_line, "http_status": self.http_status,
                "ready_after_s": round(self.ready_at - self.started, 1) if self.ready_at else None,
                "exit_code": self.exit_code, "uptime_s": round(time.time() - self.started)}


class DevServerManager:
    def __init__(self, services: Any, max_servers: int = 6) -> None:
        self.s = services
        self.max_servers = max_servers
        self.servers: dict[str, DevServer] = {}

    async def start(self, argv: list[str], cwd: str, *, ready_pattern: str | None = None, url: str | None = None,
                    task_id: str | None = None, label: str = "") -> DevServer:
        alive = [d for d in self.servers.values() if d.exit_code is None]
        if len(alive) >= self.max_servers:
            raise RuntimeError(f"too many dev servers running ({len(alive)})")
        proc, mp = await self.s.processes.spawn(argv, cwd=cwd, env=child_env(), name=label or argv[0], kind="devserver",
                                                owner_task=None)
        ds = DevServer(id=new_id("dev"), argv=argv, cwd=cwd, ready_pattern=ready_pattern or DEFAULT_READY, url=url,
                       proc=proc, managed=mp, task_id=task_id, label=label or " ".join(argv)[:60])
        self.servers[ds.id] = ds
        asyncio.create_task(self._pump(ds, proc.stdout), name=f"devserver-out-{ds.id}")
        asyncio.create_task(self._pump(ds, proc.stderr), name=f"devserver-err-{ds.id}")
        asyncio.create_task(self._watch_exit(ds), name=f"devserver-exit-{ds.id}")
        return ds

    async def _pump(self, ds: DevServer, stream: asyncio.StreamReader | None) -> None:
        if stream is None:
            return
        pattern = re.compile(ds.ready_pattern)
        while True:
            try:
                raw = await stream.readline()
            except (ValueError, ConnectionError):
                continue
            if not raw:
                return
            line = global_redactor().redact(raw.decode("utf-8", errors="replace").rstrip())
            clean = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", line)
            ds.lines.append(clean)
            if ds.url is None:
                m = URL_RE.search(clean)
                if m:
                    ds.url = m.group(0).replace("0.0.0.0", "127.0.0.1").rstrip("/.,)")
            if ds.ready_line is None and pattern.search(clean):
                ds.ready_line = clean[:200]
                asyncio.create_task(self._probe(ds))

    async def _probe(self, ds: DevServer, attempts: int = 40) -> None:
        """Readiness = output pattern AND an HTTP response from the server."""
        for _ in range(attempts):
            if ds.exit_code is not None:
                return
            if ds.url:
                try:
                    async with httpx.AsyncClient(timeout=3.0, follow_redirects=True) as c:
                        r = await c.get(ds.url)
                    if r.status_code < 500:
                        ds.http_status = r.status_code
                        ds.ready_at = time.time()
                        ds.ready_event.set()
                        self.s.bus.publish(MonitorFired(task_id=ds.task_id, monitor_id=ds.id, detail={"kind": "devserver_ready",
                                                        "url": ds.url, "status": r.status_code},
                                                        message=f"Dev server ready at {ds.url}"))
                        return
                except httpx.HTTPError:
                    pass
            await asyncio.sleep(0.5)
        if ds.url is None:
            # no URL known: the output pattern alone is the best available signal; record that honestly
            ds.ready_at = time.time()
            ds.ready_event.set()

    async def _watch_exit(self, ds: DevServer) -> None:
        code = await ds.proc.wait()
        ds.exit_code = code
        ds.exited_event.set()
        self.s.processes.forget(ds.managed.pid)
        if not ds.stopped_by_user:
            msg = f"Dev server '{ds.label}' stopped unexpectedly (exit {code})"
            log.warning("devserver_crashed", id=ds.id, exit_code=code)
            self.s.bus.publish(MonitorFired(task_id=ds.task_id, monitor_id=ds.id, detail={"kind": "devserver_crashed",
                                            "exit_code": code, "last_lines": list(ds.lines)[-10:]}, message=msg))
            self.s.bus.publish(TaskProgress(task_id=ds.task_id, message=msg))
            if self.s.notifier is not None:
                with contextlib.suppress(Exception):
                    await self.s.notifier.notify("Dev server stopped", msg)

    async def wait_ready(self, ds: DevServer, timeout: float) -> bool:
        waiters = {asyncio.ensure_future(ds.ready_event.wait()), asyncio.ensure_future(ds.exited_event.wait())}
        done, pending = await asyncio.wait(waiters, timeout=timeout, return_when=asyncio.FIRST_COMPLETED)
        for p in pending:
            p.cancel()
        return ds.ready_event.is_set()

    async def stop(self, sid: str) -> DevServer:
        ds = self.servers.get(sid)
        if ds is None:
            raise KeyError(sid)
        ds.stopped_by_user = True
        self.s.processes.kill(ds.managed.pid)
        with contextlib.suppress(asyncio.TimeoutError, TimeoutError):
            await asyncio.wait_for(ds.exited_event.wait(), 10)
        return ds

    async def stop_all(self) -> int:
        n = 0
        for sid, ds in list(self.servers.items()):
            if ds.exit_code is None:
                await self.stop(sid)
                n += 1
        return n
