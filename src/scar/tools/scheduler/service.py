"""Persistent scheduler (C9.12): one-shot and recurring reminders and tasks, backed by SQLite.

Event-driven: sleeps until the earliest due item (woken early when items are
added). Firings missed while SCAR was not running are reported on start.
"""

from __future__ import annotations

import asyncio
import contextlib
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog

from scar.core.events import ReminderDue
from scar.core.ids import new_id
from scar.storage.db import now_iso

log = structlog.get_logger("scar.scheduler")

GRACE = timedelta(seconds=90)


class Scheduler:
    def __init__(self, services: Any, max_items: int = 500) -> None:
        self.s = services
        self.max_items = max_items
        self._wake = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self.missed_on_start: list[dict[str, Any]] = []
        self.fired: list[dict[str, Any]] = []

    # ------------------------------------------------------------------ CRUD
    def add(self, kind: str, text: str, when: datetime, *, interval_s: float | None = None, objective: str | None = None,
            tz: str = "") -> dict[str, Any]:
        active = self.s.db.query_one("SELECT count(*) AS n FROM schedules WHERE status = 'active'")
        if active and active["n"] >= self.max_items:
            raise ValueError(f"too many active schedules ({active['n']})")
        if kind not in ("reminder", "task"):
            raise ValueError("kind must be reminder or task")
        sid = new_id("sch")
        self.s.db.execute(
            "INSERT INTO schedules(schedule_id, kind, text, objective, next_run, interval_seconds, tz, status, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (sid, kind, text, objective, when.astimezone(UTC).isoformat(), interval_s, tz or str(when.tzinfo), "active", now_iso()),
        )
        self._wake.set()
        return self.get(sid) or {}

    def get(self, sid: str) -> dict[str, Any] | None:
        return self.s.db.query_one("SELECT * FROM schedules WHERE schedule_id = ?", (sid,))

    def list(self, include_done: bool = False) -> list[dict[str, Any]]:
        if include_done:
            return self.s.db.query("SELECT * FROM schedules ORDER BY next_run")
        return self.s.db.query("SELECT * FROM schedules WHERE status = 'active' ORDER BY next_run")

    def cancel(self, sid: str) -> bool:
        n = self.s.db.execute("UPDATE schedules SET status = 'cancelled' WHERE schedule_id = ? AND status = 'active'", (sid,))
        self._wake.set()
        return n > 0

    # ------------------------------------------------------------------ loop
    def start(self) -> None:
        if self._task is None or self._task.done():
            self._report_missed()
            self._task = asyncio.create_task(self._loop(), name="scar-scheduler")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    def _report_missed(self) -> None:
        cutoff = (datetime.now(UTC) - GRACE).isoformat()
        rows = self.s.db.query("SELECT * FROM schedules WHERE status = 'active' AND next_run < ?", (cutoff,))
        self.missed_on_start = rows
        for r in rows:
            self.s.db.execute("UPDATE schedules SET missed_count = missed_count + 1 WHERE schedule_id = ?", (r["schedule_id"],))
            log.warning("schedule_missed", id=r["schedule_id"], due=r["next_run"])

    async def _loop(self) -> None:
        if self.missed_on_start and self.s.notifier is not None:
            for r in self.missed_on_start:
                due = datetime.fromisoformat(r["next_run"]).astimezone().strftime("%Y-%m-%d %H:%M")
                await self.s.notifier.notify("Missed while SCAR was off", f"{r['text']} (was due {due})")
                await self._fire(r, missed=True)
        while True:
            row = self.s.db.query_one("SELECT * FROM schedules WHERE status = 'active' ORDER BY next_run LIMIT 1")
            self._wake.clear()
            if row is None:
                await self._wake.wait()
                continue
            due = datetime.fromisoformat(row["next_run"])
            delay = (due - datetime.now(UTC)).total_seconds()
            if delay > 0:
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(self._wake.wait(), timeout=min(delay, 3600))
                continue
            await self._fire(row)

    async def _fire(self, row: dict[str, Any], missed: bool = False) -> None:
        sid = row["schedule_id"]
        now = datetime.now(UTC)
        if row["interval_seconds"]:
            nxt = datetime.fromisoformat(row["next_run"])
            step = timedelta(seconds=float(row["interval_seconds"]))
            while nxt <= now:
                nxt += step
            self.s.db.execute("UPDATE schedules SET next_run = ?, last_run = ?, fired_count = fired_count + 1 WHERE schedule_id = ?",
                              (nxt.isoformat(), now.isoformat(), sid))
        else:
            self.s.db.execute("UPDATE schedules SET status = 'done', last_run = ?, fired_count = fired_count + 1 WHERE schedule_id = ?",
                              (now.isoformat(), sid))
        self.fired.append({"id": sid, "at": now.isoformat(), "missed": missed})
        del self.fired[:-100]
        if missed:
            return
        if row["kind"] == "reminder":
            self.s.bus.publish(ReminderDue(schedule_id=sid, message=row["text"]))
            if self.s.notifier is not None:
                await self.s.notifier.notify("Reminder", row["text"])
        elif self.s.tasks is not None:
            try:
                await self.s.tasks.submit(row["objective"] or row["text"], background=True, origin="scheduler")
            except Exception as exc:  # noqa: BLE001 - a failing scheduled task must not stop the scheduler
                log.error("scheduled_task_failed", id=sid, error=str(exc))
