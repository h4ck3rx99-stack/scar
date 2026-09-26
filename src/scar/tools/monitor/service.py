"""Monitors (C9.12): process exit/crash, folder changes, command completion, URL readiness, download completion.

Event-driven where the OS allows it: process-handle waits, ReadDirectoryChangesW (watchdog), stream reads.
Monitors are created only at the user's request, bounded in number, listable and cancellable, and re-armed
after a daemon restart when still meaningful.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import psutil
import structlog

from scar.core.cancel import CancelToken
from scar.core.events import MonitorFired
from scar.core.ids import new_id
from scar.storage.db import now_iso
from scar.tools.monitor.waits import wait_for_pid_exit

log = structlog.get_logger("scar.monitors")

PARTIAL_SUFFIXES = (".crdownload", ".part", ".partial", ".tmp", ".download", ".opdownload")


@dataclass
class Monitor:
    id: str
    kind: str
    target: dict[str, Any]
    task: asyncio.Task[None] | None = None
    cancel: CancelToken = field(default_factory=CancelToken)
    events: list[dict[str, Any]] = field(default_factory=list)
    created: float = field(default_factory=time.time)
    status: str = "active"


class MonitorService:
    def __init__(self, services: Any) -> None:
        self.s = services
        self.monitors: dict[str, Monitor] = {}
        limits = getattr(services.admission, "limits", None)
        self.max_monitors = limits.max_monitors if limits else 20
        self.min_interval = limits.monitor_min_interval_s if limits else 5.0

    # ------------------------------------------------------------------ lifecycle
    def _persist(self, m: Monitor) -> None:
        self.s.db.execute(
            "INSERT INTO monitors(monitor_id, kind, target_json, status, created_at) VALUES (?,?,?,?,?) "
            "ON CONFLICT(monitor_id) DO UPDATE SET status = excluded.status",
            (m.id, m.kind, json.dumps(m.target), m.status, now_iso()),
        )

    def _set_status(self, m: Monitor, status: str, event: dict[str, Any] | None = None) -> None:
        m.status = status
        self.s.db.execute("UPDATE monitors SET status = ?, last_event = ?, last_event_json = ? WHERE monitor_id = ?",
                          (status, now_iso() if event else None, json.dumps(event, default=str) if event else None, m.id))

    def _check_capacity(self) -> None:
        active = [m for m in self.monitors.values() if m.status == "active"]
        if len(active) >= self.max_monitors:
            raise ValueError(f"monitor limit reached ({self.max_monitors}); cancel one first")

    def _start(self, m: Monitor, coro: Any) -> Monitor:
        self.monitors[m.id] = m
        self._persist(m)
        m.task = asyncio.create_task(self._guard(m, coro), name=f"monitor-{m.id}")
        return m

    async def _guard(self, m: Monitor, coro: Any) -> None:
        try:
            await coro
        except asyncio.CancelledError:
            if m.status == "active":
                self._set_status(m, "cancelled")
            raise
        except Exception as exc:  # noqa: BLE001 - a broken monitor is reported, never crashes the runtime
            log.error("monitor_failed", id=m.id, error=str(exc))
            self._set_status(m, "failed", {"error": str(exc)})

    async def _fire(self, m: Monitor, title: str, message: str, detail: dict[str, Any], final: bool = True) -> None:
        event = {"at": now_iso(), "message": message, **detail}
        m.events.append(event)
        del m.events[:-50]
        self.s.bus.publish(MonitorFired(monitor_id=m.id, detail={"kind": m.kind, **detail}, message=message))
        if self.s.notifier is not None:
            await self.s.notifier.notify(title, message)
        self._set_status(m, "fired" if final else "active", event)

    def cancel(self, mid: str) -> bool:
        m = self.monitors.get(mid)
        if m is None:
            return self.s.db.execute("UPDATE monitors SET status = 'cancelled' WHERE monitor_id = ? AND status = 'active'", (mid,)) > 0
        m.cancel.cancel("cancelled by user")
        if m.task is not None:
            m.task.cancel()
        self._set_status(m, "cancelled")
        return True

    async def stop_all(self) -> None:
        for m in list(self.monitors.values()):
            m.cancel.cancel("shutdown")
            if m.task is not None:
                m.task.cancel()
        await asyncio.gather(*(m.task for m in self.monitors.values() if m.task is not None), return_exceptions=True)

    def list(self) -> list[dict[str, Any]]:
        out = []
        for r in self.s.db.query("SELECT * FROM monitors ORDER BY created_at DESC LIMIT 100"):
            live = self.monitors.get(r["monitor_id"])
            out.append({"id": r["monitor_id"], "kind": r["kind"], "target": json.loads(r["target_json"]),
                        "status": live.status if live else r["status"], "last_event": r["last_event"],
                        "events": len(live.events) if live else None})
        return out

    # ------------------------------------------------------------------ process
    def watch_process(self, pid: int, *, crash_only: bool = True, name: str = "") -> Monitor:
        self._check_capacity()
        try:
            p = psutil.Process(pid)
            name = name or p.name()
            created = p.create_time()
        except psutil.NoSuchProcess as exc:
            raise ValueError(f"no process with PID {pid}") from exc
        m = Monitor(new_id("mon"), "process", {"pid": pid, "name": name, "crash_only": crash_only, "create_time": created})
        return self._start(m, self._process_loop(m))

    async def _process_loop(self, m: Monitor) -> None:
        pid = int(m.target["pid"])
        started = time.monotonic()
        code = await wait_for_pid_exit(pid, 7 * 86400, m.cancel)
        if m.cancel.cancelled:
            return
        waited = round(time.monotonic() - started, 1)
        name = m.target.get("name", str(pid))
        crashed = code is None or code != 0
        detail = {"pid": pid, "exit_code": code, "waited_s": waited, "wait_method": "WaitForMultipleObjects(process handle)"}
        if crashed:
            await self._fire(m, "Process crashed", f"{name} (PID {pid}) exited with code {code}", {**detail, "crashed": True})
        elif not m.target.get("crash_only"):
            await self._fire(m, "Process finished", f"{name} (PID {pid}) exited normally", {**detail, "crashed": False})
        else:
            self._set_status(m, "done", {**detail, "crashed": False})

    # ------------------------------------------------------------------ folder
    def watch_folder(self, path: str, *, pattern: str = "*", recursive: bool = False, debounce_s: float = 2.0) -> Monitor:
        self._check_capacity()
        if not Path(path).is_dir():
            raise ValueError(f"not a folder: {path}")
        m = Monitor(new_id("mon"), "folder", {"path": path, "pattern": pattern, "recursive": recursive, "debounce_s": debounce_s})
        return self._start(m, self._folder_loop(m, final=False))

    def watch_download(self, folder: str, *, pattern: str = "*", timeout_s: float = 3600) -> Monitor:
        self._check_capacity()
        if not Path(folder).is_dir():
            raise ValueError(f"not a folder: {folder}")
        m = Monitor(new_id("mon"), "download", {"path": folder, "pattern": pattern, "recursive": False, "debounce_s": 2.0,
                                                "timeout_s": timeout_s})
        return self._start(m, self._folder_loop(m, final=True))

    async def _folder_loop(self, m: Monitor, final: bool) -> None:
        import fnmatch

        from watchdog.events import FileSystemEvent, FileSystemEventHandler
        from watchdog.observers import Observer

        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[tuple[str, str]] = asyncio.Queue(maxsize=1000)
        pattern = m.target.get("pattern", "*")

        class Handler(FileSystemEventHandler):
            def on_any_event(self, event: FileSystemEvent) -> None:
                if event.is_directory:
                    return
                path = str(getattr(event, "dest_path", "") or event.src_path)
                if not fnmatch.fnmatch(Path(path).name.lower(), pattern.lower()):
                    return
                with contextlib.suppress(asyncio.QueueFull, RuntimeError):
                    loop.call_soon_threadsafe(queue.put_nowait, (event.event_type, path))

        observer = Observer()
        observer.schedule(Handler(), m.target["path"], recursive=bool(m.target.get("recursive")))
        observer.daemon = True
        observer.start()
        deadline = time.monotonic() + float(m.target.get("timeout_s", 10 * 365 * 86400))
        try:
            while not m.cancel.cancelled:
                try:
                    etype, path = await asyncio.wait_for(queue.get(), timeout=max(1.0, deadline - time.monotonic()))
                except TimeoutError:
                    if time.monotonic() >= deadline:
                        self._set_status(m, "timeout")
                        return
                    continue
                changes = {path: etype}
                # debounce: gather the burst
                end = time.monotonic() + float(m.target.get("debounce_s", 2.0))
                while time.monotonic() < end:
                    try:
                        etype, path = await asyncio.wait_for(queue.get(), timeout=max(0.05, end - time.monotonic()))
                        changes[path] = etype
                    except TimeoutError:
                        break
                if m.kind == "download":
                    done = [p for p in changes if not p.lower().endswith(PARTIAL_SUFFIXES) and await _stable(Path(p))]
                    if not done:
                        continue
                    names = ", ".join(Path(p).name for p in done[:3])
                    await self._fire(m, "Download finished", f"Downloaded {names}", {"files": done}, final=True)
                    return
                summary = ", ".join(f"{t} {Path(p).name}" for p, t in list(changes.items())[:5])
                await self._fire(m, "Folder changed", f"{Path(m.target['path']).name}: {summary}",
                                 {"changes": [{"path": p, "event": t} for p, t in changes.items()]}, final=final)
                if final:
                    return
        finally:
            observer.stop()
            await asyncio.to_thread(observer.join, 5)

    # ------------------------------------------------------------------ command
    def watch_command(self, argv: list[str], cwd: str, *, timeout_s: float = 3600, label: str = "") -> Monitor:
        self._check_capacity()
        m = Monitor(new_id("mon"), "command", {"argv": argv, "cwd": cwd, "timeout_s": timeout_s, "label": label or " ".join(argv)[:60]})
        return self._start(m, self._command_loop(m))

    async def _command_loop(self, m: Monitor) -> None:
        from scar.tools.terminal.runner import run_process

        o = await run_process(list(m.target["argv"]), services=self.s, cancel=m.cancel, cwd=m.target["cwd"],
                              timeout=float(m.target["timeout_s"]), task_id=m.id)
        if o.cancelled:
            return
        ok = o.exit_code == 0
        tail = (o.stdout + o.stderr).strip().splitlines()[-5:]
        await self._fire(m, "Command finished" if ok else "Command failed",
                         f"{m.target['label']} {'finished' if ok else 'failed'} (exit {o.exit_code}, {o.duration_s:.0f}s)",
                         {"exit_code": o.exit_code, "duration_s": o.duration_s, "tail": tail, "timed_out": o.timed_out})

    # ------------------------------------------------------------------ url
    def watch_url(self, url: str, *, timeout_s: float = 600, expect_status_below: int = 400) -> Monitor:
        self._check_capacity()
        m = Monitor(new_id("mon"), "url", {"url": url, "timeout_s": timeout_s, "expect_below": expect_status_below})
        return self._start(m, self._url_loop(m))

    async def _url_loop(self, m: Monitor) -> None:
        import httpx

        deadline = time.monotonic() + float(m.target["timeout_s"])
        interval = max(1.0, self.min_interval / 2)
        async with httpx.AsyncClient(timeout=5.0, follow_redirects=True) as c:
            while time.monotonic() < deadline and not m.cancel.cancelled:
                try:
                    r = await c.get(m.target["url"])
                    if r.status_code < int(m.target["expect_below"]):
                        await self._fire(m, "Site is up", f"{m.target['url']} answered {r.status_code}", {"status": r.status_code})
                        return
                except httpx.HTTPError:
                    pass
                await asyncio.sleep(interval)
                interval = min(interval * 1.5, 30.0)
        if not m.cancel.cancelled:
            self._set_status(m, "timeout")

    # ------------------------------------------------------------------ restart
    def rearm(self) -> list[str]:
        """Re-arm persisted monitors after a restart; report ones that can no longer be meaningful."""
        notes: list[str] = []
        for r in self.s.db.query("SELECT * FROM monitors WHERE status = 'active'"):
            target = json.loads(r["target_json"])
            mid = r["monitor_id"]
            try:
                if r["kind"] == "process":
                    pid = int(target["pid"])
                    alive = psutil.pid_exists(pid) and abs(psutil.Process(pid).create_time() - float(target.get("create_time", 0))) < 1
                    if not alive:
                        self.s.db.execute("UPDATE monitors SET status = 'interrupted' WHERE monitor_id = ?", (mid,))
                        notes.append(f"process monitor {mid} ({target.get('name')}): process ended while SCAR was off")
                        continue
                    m = Monitor(mid, "process", target)
                    self._start(m, self._process_loop(m))
                elif r["kind"] in ("folder", "download"):
                    m = Monitor(mid, r["kind"], target)
                    self._start(m, self._folder_loop(m, final=r["kind"] == "download"))
                elif r["kind"] == "url":
                    m = Monitor(mid, "url", target)
                    self._start(m, self._url_loop(m))
                else:
                    self.s.db.execute("UPDATE monitors SET status = 'interrupted' WHERE monitor_id = ?", (mid,))
                    notes.append(f"{r['kind']} monitor {mid} was interrupted by the restart")
            except (psutil.Error, ValueError, OSError) as exc:
                self.s.db.execute("UPDATE monitors SET status = 'interrupted' WHERE monitor_id = ?", (mid,))
                notes.append(f"monitor {mid} could not be re-armed: {exc}")
        return notes


async def _stable(p: Path, wait: float = 1.5) -> bool:
    try:
        s1 = p.stat().st_size
        await asyncio.sleep(wait)
        return p.exists() and p.stat().st_size == s1 and s1 > 0
    except OSError:
        return False
