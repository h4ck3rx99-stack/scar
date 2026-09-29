"""Timers, alarms and reminders. Persisted, so they survive a restart; fired through a callback."""
import asyncio
import datetime
import logging
import re
import time
import uuid

from jarvis.skills import store

log = logging.getLogger("reminders")

_UNITS = {"second": 1, "sec": 1, "minute": 60, "min": 60, "hour": 3600, "hr": 3600, "day": 86400}
_WORDNUM = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
            "nine": 9, "ten": 10, "fifteen": 15, "twenty": 20, "thirty": 30, "forty": 40, "forty five": 45, "fifty": 50,
            "sixty": 60, "ninety": 90, "half an": 0.5, "half a": 0.5}


def parse_duration(text: str):
    """'5 minutes', 'an hour and a half', '1 hour 20 min', 'ninety seconds' -> seconds, or None."""
    t = text.lower().replace("-", " ")
    total = 0.0
    for num, unit in re.findall(r"(\d+(?:\.\d+)?|half an|half a|forty five|[a-z]+)\s+(second|sec|minute|min|hour|hr|day)s?\b", t):
        n = float(num) if num[0].isdigit() else _WORDNUM.get(num)
        if n is None:
            continue
        total += n * _UNITS[unit]
    if "and a half" in t and total:
        unit = re.search(r"(second|minute|hour|day)s?\s+and a half", t)
        if unit:
            total += 0.5 * _UNITS[unit.group(1)]
    return int(total) or None


def parse_clock(text: str):
    """'7', '7:30', '7 30 pm', '19:05', 'noon' -> next datetime at that time, or None."""
    t = text.lower().replace(".", "").strip()
    if "noon" in t:
        h, m, ampm = 12, 0, None
    elif "midnight" in t:
        h, m, ampm = 0, 0, None
    else:
        mt = re.search(r"(\d{1,2})(?:[:\s](\d{2}))?\s*(am|pm|a m|p m)?", t)
        if not mt:
            return None
        h, m, ampm = int(mt.group(1)), int(mt.group(2) or 0), (mt.group(3) or "").replace(" ", "") or None
    if ampm == "pm" and h < 12:
        h += 12
    if ampm == "am" and h == 12:
        h = 0
    if h > 23 or m > 59:
        return None
    now = datetime.datetime.now()
    due = now.replace(hour=h, minute=m, second=0, microsecond=0)
    if due <= now:
        # "at 7" with no am/pm: pick the next 7 o'clock, morning or evening.
        if ampm is None and h < 12 and now.replace(hour=h + 12, minute=m, second=0) > now:
            due = due.replace(hour=h + 12)
        else:
            due += datetime.timedelta(days=1)
    return due


def human_delta(seconds):
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds} seconds"
    m = round(seconds / 60)
    if m < 60:
        return f"{m} minute{'s' * (m != 1)}"
    h, m = divmod(m, 60)
    return f"{h} hour{'s' * (h != 1)}" + (f" {m} minutes" if m else "")


class Scheduler:
    def __init__(self, on_fire):
        self.on_fire = on_fire        # async fn(item)
        self._wake = asyncio.Event()

    def add(self, text, due_ts, kind="reminder"):
        data = store.reminders.load()
        item = {"id": uuid.uuid4().hex[:8], "text": text, "due": due_ts, "kind": kind}
        data.append(item)
        store.reminders.save(data)
        self._wake.set()
        return item

    def pending(self):
        return sorted(store.reminders.load(), key=lambda r: r["due"])

    def cancel(self, kind=None):
        data = store.reminders.load()
        keep = [r for r in data if kind and r["kind"] != kind]
        store.reminders.save(keep)
        self._wake.set()
        return len(data) - len(keep)

    async def run(self):
        while True:
            data = store.reminders.load()
            now = time.time()
            due = [r for r in data if r["due"] <= now]
            if due:
                store.reminders.save([r for r in data if r["due"] > now])
                for r in due:
                    late = now - r["due"]
                    if late < 6 * 3600:  # don't announce things that were due while the PC was off for hours
                        try:
                            await self.on_fire(r)
                        except Exception:  # noqa: BLE001
                            log.exception("reminder callback failed")
                continue
            nxt = min((r["due"] for r in data), default=now + 3600)
            self._wake.clear()
            try:
                await asyncio.wait_for(self._wake.wait(), max(0.2, min(30, nxt - now)))
            except asyncio.TimeoutError:
                pass
