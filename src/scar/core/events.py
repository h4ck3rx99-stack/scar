"""Typed event bus.

Events carry short summaries only, never model reasoning. Subscribers get a
bounded queue; when a slow subscriber falls behind, its oldest events are
dropped (and counted) rather than growing memory without bound.
"""

from __future__ import annotations

import asyncio
import contextlib
import threading
from collections.abc import AsyncGenerator, Callable
from datetime import datetime
from typing import Any, Literal

import structlog
from pydantic import BaseModel, Field

from scar.core.types import utcnow

log = structlog.get_logger("scar.events")


class Event(BaseModel):
    kind: str
    task_id: str | None = None
    at: datetime = Field(default_factory=utcnow)
    message: str = ""


class TaskStarted(Event):
    kind: Literal["task_started"] = "task_started"
    objective: str = ""
    background: bool = False


class PlanCreated(Event):
    kind: Literal["plan_created"] = "plan_created"
    steps: list[str] = Field(default_factory=list)


class StepStarted(Event):
    kind: Literal["step_started"] = "step_started"
    step: int = 0
    intent: str = ""


class ToolCalled(Event):
    kind: Literal["tool_called"] = "tool_called"
    tool: str = ""
    action_id: str = ""
    risk: str = ""


class ToolCompleted(Event):
    kind: Literal["tool_completed"] = "tool_completed"
    tool: str = ""
    action_id: str = ""
    status: str = ""
    duration_ms: int = 0
    verified: bool | None = None


class ApprovalRequested(Event):
    kind: Literal["approval_requested"] = "approval_requested"
    request_id: str = ""
    risk: str = ""


class ApprovalResolved(Event):
    kind: Literal["approval_resolved"] = "approval_resolved"
    request_id: str = ""
    decision: str = ""


class ProviderFallback(Event):
    kind: Literal["provider_fallback"] = "provider_fallback"
    category: str = ""
    from_provider: str = ""
    to_provider: str = ""
    reason: str = ""


class VerificationResultEvent(Event):
    kind: Literal["verification_result"] = "verification_result"
    verified: bool | None = None


class TaskProgress(Event):
    kind: Literal["task_progress"] = "task_progress"
    milestone: bool = True


class TaskCompleted(Event):
    kind: Literal["task_completed"] = "task_completed"
    verified: bool | None = None
    background: bool = False


class TaskFailed(Event):
    kind: Literal["task_failed"] = "task_failed"
    error: str = ""
    background: bool = False


class ResourceWarning(Event):
    kind: Literal["resource_warning"] = "resource_warning"
    resource: str = ""


class MonitorFired(Event):
    kind: Literal["monitor_fired"] = "monitor_fired"
    monitor_id: str = ""
    detail: dict[str, Any] = Field(default_factory=dict)


class ReminderDue(Event):
    kind: Literal["reminder_due"] = "reminder_due"
    schedule_id: str = ""


class AssistantMessage(Event):
    """A user-facing reply chunk (conversation text, final answers)."""

    kind: Literal["assistant_message"] = "assistant_message"
    final: bool = True


Listener = Callable[[Event], None]


class _Subscription:
    def __init__(self, loop: asyncio.AbstractEventLoop, maxsize: int, kinds: frozenset[str] | None) -> None:
        self.loop = loop
        self.queue: asyncio.Queue[Event] = asyncio.Queue(maxsize=maxsize)
        self.kinds = kinds
        self.dropped = 0

    def offer(self, event: Event) -> None:
        if self.kinds is not None and event.kind not in self.kinds:
            return
        while True:
            try:
                self.queue.put_nowait(event)
                return
            except asyncio.QueueFull:
                with contextlib.suppress(asyncio.QueueEmpty):
                    self.queue.get_nowait()
                    self.dropped += 1


class EventBus:
    """Fan-out event bus, callable from the loop thread or worker threads."""

    def __init__(self, queue_size: int = 256) -> None:
        self._queue_size = queue_size
        self._subs: list[_Subscription] = []
        self._listeners: list[Listener] = []
        self._failed_listeners: set[int] = set()
        self._lock = threading.Lock()

    def add_listener(self, listener: Listener) -> None:
        """Synchronous listener (logging, tracing). Must be fast and non-blocking."""
        with self._lock:
            self._listeners.append(listener)

    def remove_listener(self, listener: Listener) -> None:
        with self._lock:
            if listener in self._listeners:
                self._listeners.remove(listener)

    def publish(self, event: Event) -> None:
        with self._lock:
            listeners = list(self._listeners)
            subs = list(self._subs)
        for listener in listeners:
            try:
                listener(event)
            except Exception as exc:  # noqa: BLE001 - a broken listener must not break publishing
                if id(listener) not in self._failed_listeners:  # report once per listener, not on every event
                    self._failed_listeners.add(id(listener))
                    log.warning("event_listener_failed", listener=getattr(listener, "__qualname__", repr(listener)),
                                event_kind=event.kind, error=repr(exc)[:300])
                continue
        for sub in subs:
            if sub.loop.is_closed():
                continue
            try:
                running = asyncio.get_running_loop()
            except RuntimeError:
                running = None
            if running is sub.loop:
                sub.offer(event)
            else:
                sub.loop.call_soon_threadsafe(sub.offer, event)

    @contextlib.asynccontextmanager
    async def subscribe(self, kinds: set[str] | None = None) -> AsyncGenerator[asyncio.Queue[Event], None]:
        sub = _Subscription(asyncio.get_running_loop(), self._queue_size, frozenset(kinds) if kinds else None)
        with self._lock:
            self._subs.append(sub)
        try:
            yield sub.queue
        finally:
            with self._lock:
                if sub in self._subs:
                    self._subs.remove(sub)

    @property
    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subs)
