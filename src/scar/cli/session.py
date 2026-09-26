"""Front-end sessions: run objectives against an embedded runtime or an attached daemon, with the same UX."""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any

from scar.cli.render import Renderer, console
from scar.config.settings import Settings
from scar.runtime.ipc import InstanceLock, IpcClient, daemon_alive
from scar.runtime.runtime import Runtime
from scar.security.approval import ApprovalError, ApprovalResponse


class EmbeddedSession:
    """Runs the runtime in-process (no daemon)."""

    def __init__(self, settings: Settings, renderer: Renderer) -> None:
        self.settings = settings
        self.r = renderer
        self.rt = Runtime(settings)
        self.lock = InstanceLock(settings.data_path / "runtime.lock")
        self._pump: asyncio.Task[None] | None = None
        self._queue: Any = None
        self._sub: Any = None

    async def start(self, **kwargs: Any) -> bool:
        if not self.lock.acquire():
            return False
        await self.rt.start(**kwargs)
        s = self.rt.services
        if self.r.interactive:
            s.approvals.attach_channel("cli")
            s.questions.attach_channel()
        self._sub = s.bus.subscribe()
        self._queue = await self._sub.__aenter__()
        self._pump = asyncio.create_task(self._events())
        for note in self.rt.startup_notes:
            console.print(f"[yellow]•[/yellow] {note}")
        return True

    async def _events(self) -> None:
        s = self.rt.services
        while True:
            ev = await self._queue.get()
            d = ev.model_dump(mode="json")
            if ev.kind == "approval_requested":
                req = s.approvals.get(d.get("request_id", ""))
                if req is None:
                    continue
                payload = req.model_dump(mode="json")
                payload["risk"] = req.risk.name
                payload["allowed_responses"] = [r.value for r in req.allowed_responses()]
                response, typed = await self.r.ask_approval(payload)
                with contextlib.suppress(ApprovalError):
                    s.approvals.resolve(req.request_id, ApprovalResponse(response), "cli", typed_confirmation=typed)
            elif ev.kind == "question_asked":
                answer = await self.r.ask_question(d.get("message", ""), d.get("options") or [])
                s.questions.answer(d.get("question_id", ""), answer)
            else:
                self.r.event(d)

    async def run(self, objective: str, **kwargs: Any) -> dict[str, Any]:
        assert self.rt.tasks is not None
        task = await self.rt.tasks.run(objective, **kwargs)
        await asyncio.sleep(0.05)
        return {"task_id": task.task_id, "status": task.status.value, "summary": task.result_summary,
                "verified": task.verification.verified if task.verification else None}

    def cancel_running(self) -> list[str]:
        return self.rt.tasks.cancel_all("cancelled by user") if self.rt.tasks else []

    async def stop(self) -> None:
        if self._pump is not None:
            self._pump.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._pump
        if self._sub is not None:
            with contextlib.suppress(Exception):
                await self._sub.__aexit__(None, None, None)
        await self.rt.stop()
        self.lock.release()


class DaemonSession:
    """Talks to a running daemon over authenticated local IPC."""

    def __init__(self, settings: Settings, renderer: Renderer) -> None:
        self.settings = settings
        self.r = renderer

    async def start(self) -> bool:
        return await daemon_alive(self.settings.data_path)

    async def run(self, objective: str, *, background: bool = False, autonomy: int | None = None,
                  dry_run: bool | None = None, origin: str = "text") -> dict[str, Any]:
        client = IpcClient(self.settings.data_path)
        if not await client.connect():
            raise ConnectionError("the SCAR daemon is not reachable")
        try:
            first = await client.request("submit", objective=objective, background=background, autonomy=autonomy,
                                         dry_run=dry_run, origin=origin, interactive=self.r.interactive)
            if not first or first.get("type") != "accepted":
                raise ConnectionError(str(first))
            if background:
                return {"task_id": first["task_id"], "status": "running", "summary": "Started in the background.", "verified": None}
            async for msg in client.stream():
                if msg.get("type") == "result":
                    return dict(msg["task"])
                if msg.get("type") != "event":
                    continue
                ev = msg["event"]
                if ev.get("kind") == "approval_requested" and msg.get("approval"):
                    response, typed = await self.r.ask_approval(msg["approval"])
                    await client.send({"op": "approve", "request_id": ev["request_id"], "response": response, "typed": typed})
                elif ev.get("kind") == "question_asked":
                    answer = await self.r.ask_question(ev.get("message", ""), ev.get("options") or [])
                    await client.send({"op": "answer", "question_id": ev["question_id"], "text": answer})
                else:
                    self.r.event(ev)
            raise ConnectionError("the daemon closed the connection")
        finally:
            await client.close()

    async def simple(self, op: str, **payload: Any) -> dict[str, Any] | None:
        client = IpcClient(self.settings.data_path)
        if not await client.connect():
            return None
        try:
            return await client.request(op, **payload)
        finally:
            await client.close()

    async def stop(self) -> None:
        return None


async def open_session(settings: Settings, renderer: Renderer, *, prefer_daemon: bool = True,
                       **start_kwargs: Any) -> EmbeddedSession | DaemonSession:
    if prefer_daemon and await daemon_alive(settings.data_path):
        return DaemonSession(settings, renderer)
    emb = EmbeddedSession(settings, renderer)
    if await emb.start(**start_kwargs):
        return emb
    if await daemon_alive(settings.data_path):
        return DaemonSession(settings, renderer)
    raise RuntimeError("another SCAR runtime holds the lock but is not answering; check `scar daemon status`")
