"""Class timetable and events: one-off or weekly, announced shortly before they start.

"add maths class every monday at 9 am"   "add dentist appointment on friday at 4:30 pm"
"what's my schedule today / tomorrow"    "remove maths class"
"""
import datetime
import re
import uuid

from rapidfuzz import fuzz

from jarvis.skills.reminders import parse_clock
from jarvis.skills.store import Store

_store = Store("schedule", [])   # [{id, title, weekday (0-6) or None, date (YYYY-MM-DD) or None, time "HH:MM"}]
DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
LEAD_MINUTES = 10


def _parse_when(when: str):
    """-> (weekdays list or None, date or None, 'HH:MM') or None."""
    w = when.lower()
    dt = parse_clock(re.sub(r"\b(every|on|each|this|next|tomorrow|today|weekday|weekdays|" + "|".join(DAYS) + r")s?\b", " ", w))
    if not dt:
        return None
    hhmm = dt.strftime("%H:%M")
    days = [i for i, d in enumerate(DAYS) if re.search(rf"\b{d}s?\b", w)]
    if "weekday" in w:
        days = [0, 1, 2, 3, 4]
    if ("every" in w or "each" in w or re.search(r"\b(" + "|".join(DAYS) + r")s\b", w)) and days:
        return days, None, hhmm
    today = datetime.date.today()
    if "tomorrow" in w:
        return None, (today + datetime.timedelta(days=1)).isoformat(), hhmm
    if days:
        delta = (days[0] - today.weekday()) % 7
        return None, (today + datetime.timedelta(days=delta)).isoformat(), hhmm
    return None, today.isoformat(), hhmm


def add(title: str, when: str):
    parsed = _parse_when(when)
    if not parsed:
        return None
    weekdays, date, hhmm = parsed
    data = _store.load()
    items = []
    for wd in (weekdays or [None]):
        item = {"id": uuid.uuid4().hex[:8], "title": title.strip(), "weekday": wd, "date": date, "time": hhmm}
        data.append(item)
        items.append(item)
    _store.save(data)
    return items


def remove(title: str):
    data = _store.load()
    keep = [e for e in data if fuzz.partial_ratio(title.lower(), e["title"].lower()) < 80]
    _store.save(keep)
    return len(data) - len(keep)


def on_day(day: datetime.date):
    out = []
    for e in _store.load():
        if (e["weekday"] is not None and e["weekday"] == day.weekday()) or e["date"] == day.isoformat():
            out.append(e)
    return sorted(out, key=lambda e: e["time"])


def describe_day(which: str = "today"):
    day = datetime.date.today() + datetime.timedelta(days=1 if "tomorrow" in (which or "") else 0)
    for i, d in enumerate(DAYS):
        if d in (which or "").lower():
            day = datetime.date.today() + datetime.timedelta(days=(i - datetime.date.today().weekday()) % 7)
    items = on_day(day)
    label = "Today" if day == datetime.date.today() else day.strftime("%A")
    if not items:
        return f"{label}: nothing on the schedule."
    fmt = lambda hhmm: datetime.datetime.strptime(hhmm, "%H:%M").strftime("%I:%M %p").lstrip("0")  # noqa: E731
    return f"{label}: " + "; ".join(f"{e['title']} at {fmt(e['time'])}" for e in items)


def due_soon(now: datetime.datetime, announced: set):
    """Events starting in the next LEAD_MINUTES that haven't been announced yet (announced holds id+date keys)."""
    out = []
    for e in on_day(now.date()):
        start = datetime.datetime.combine(now.date(), datetime.datetime.strptime(e["time"], "%H:%M").time())
        key = f"{e['id']}:{now.date().isoformat()}"
        if key not in announced and datetime.timedelta(0) <= start - now <= datetime.timedelta(minutes=LEAD_MINUTES):
            announced.add(key)
            out.append((e, int((start - now).total_seconds() // 60)))
    return out
