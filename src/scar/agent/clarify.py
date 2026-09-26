"""Clarification questions to the user (C8.8) — same channel model as approvals."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from pydantic import BaseModel, Field

from scar.core.events import Event, EventBus
from scar.core.ids import new_id


class QuestionAsked(Event):
    kind: str = "question_asked"
    question_id: str = ""
    options: list[str] = Field(default_factory=list)


class Question(BaseModel):
    question_id: str = Field(default_factory=lambda: new_id("q"))
    task_id: str | None = None
    text: str
    options: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class QuestionBroker:
    def __init__(self, bus: EventBus, timeout: float = 600.0) -> None:
        self.bus = bus
        self.timeout = timeout
        self._pending: dict[str, tuple[Question, asyncio.Future[str | None]]] = {}
        self._channels = 0

    def attach_channel(self) -> None:
        self._channels += 1

    def detach_channel(self) -> None:
        self._channels = max(0, self._channels - 1)

    @property
    def has_channel(self) -> bool:
        return self._channels > 0

    def pending(self) -> list[Question]:
        return [q for q, _ in self._pending.values()]

    async def ask(self, question: Question) -> str | None:
        """Return the user's answer, or None on timeout / no channel."""
        if not self.has_channel:
            return None
        fut: asyncio.Future[str | None] = asyncio.get_running_loop().create_future()
        self._pending[question.question_id] = (question, fut)
        self.bus.publish(QuestionAsked(task_id=question.task_id, question_id=question.question_id,
                                       message=question.text, options=question.options))
        try:
            return await asyncio.wait_for(asyncio.shield(fut), timeout=self.timeout)
        except TimeoutError:
            return None
        finally:
            self._pending.pop(question.question_id, None)

    def answer(self, question_id: str, text: str) -> bool:
        item = self._pending.get(question_id)
        if item is None or item[1].done():
            return False
        item[1].set_result(text)
        return True

    def cancel_task(self, task_id: str) -> None:
        for q, fut in list(self._pending.values()):
            if q.task_id == task_id and not fut.done():
                fut.set_result(None)
