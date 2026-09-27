"""Stream reply text to clients (desktop app, CLI) as the model writes it.

The executor wraps model calls in ``streaming(bus, task_id)``; OpenAI-compatible clients then stream and hand visible
text to the sink, which publishes it as ``AssistantDelta`` events in small batches (so a fast model cannot flood the
bounded event queues). The final ``AssistantMessage`` stays authoritative: it may add verification notes.
"""

from __future__ import annotations

import contextlib
import time
from collections.abc import Generator
from typing import Any

from scar.core.events import AssistantDelta
from scar.providers.base import STREAM_SINK

FLUSH_CHARS = 24
FLUSH_SECONDS = 0.06


class BusSink:
    def __init__(self, bus: Any, task_id: str | None) -> None:
        self.bus = bus
        self.task_id = task_id
        self._buf: list[str] = []
        self._last = time.monotonic()
        self.chars = 0

    def begin(self) -> None:
        self._buf.clear()
        if self.chars:
            self.bus.publish(AssistantDelta(task_id=self.task_id, reset=True))
        self.chars = 0

    def delta(self, text: str) -> None:
        self._buf.append(text)
        self.chars += len(text)
        now = time.monotonic()
        if sum(len(b) for b in self._buf) >= FLUSH_CHARS or now - self._last >= FLUSH_SECONDS:
            self.flush()

    def flush(self) -> None:
        if self._buf:
            self.bus.publish(AssistantDelta(task_id=self.task_id, text="".join(self._buf)))
            self._buf.clear()
        self._last = time.monotonic()


@contextlib.contextmanager
def streaming(bus: Any, task_id: str | None) -> Generator[BusSink, None, None]:
    sink = BusSink(bus, task_id)
    token = STREAM_SINK.set(sink)
    try:
        yield sink
    finally:
        sink.flush()
        STREAM_SINK.reset(token)
