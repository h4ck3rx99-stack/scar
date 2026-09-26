"""Natural-language time and recurrence parsing (dateparser + small grammar), in the user's timezone."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, tzinfo
from zoneinfo import ZoneInfo

_UNITS = {"second": 1, "minute": 60, "hour": 3600, "day": 86400, "week": 604800}
_DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


def local_tz(name: str = "") -> tzinfo:
    if name:
        return ZoneInfo(name)
    return datetime.now().astimezone().tzinfo or UTC


@dataclass
class ParsedTime:
    when: datetime  # timezone-aware
    interval_s: float | None = None
    description: str = ""


def parse_when(text: str, tz_name: str = "", now: datetime | None = None) -> ParsedTime:
    """Parse 'in 10 minutes', 'tomorrow at 9am', 'at 17:30', 'every day at 9', 'every 2 hours', 'every monday 10am'."""
    tz = local_tz(tz_name)
    now = (now or datetime.now(tz)).astimezone(tz)
    t = text.strip().lower().rstrip(".")
    interval: float | None = None
    anchor = t
    m = re.match(r"^(?:every|each)\s+(\d+)?\s*(second|minute|hour|day|week)s?\b(.*)$", t)
    if m:
        interval = int(m.group(1) or 1) * _UNITS[m.group(2)]
        anchor = m.group(3).strip()
        if not anchor:
            return ParsedTime(now + timedelta(seconds=interval), interval, f"every {m.group(1) or ''} {m.group(2)}".replace("  ", " "))
    m = re.match(r"^(?:daily|every day|each day|every morning|every evening|every night)\b(.*)$", t)
    if m:
        interval = 86400
        rest = m.group(1).strip()
        default = "9am" if "morning" in t else "7pm" if "evening" in t else "9pm" if "night" in t else "9am"
        anchor = rest or f"at {default}"
    m = re.match(r"^(?:every|each)\s+(monday|tuesday|wednesday|thursday|friday|saturday|sunday)s?\b(.*)$", t)
    if m:
        interval = 7 * 86400
        day = _DAYS.index(m.group(1))
        at = _parse_clock(m.group(2).strip() or "9am")
        cand = now.replace(hour=at[0], minute=at[1], second=0, microsecond=0) + timedelta(days=(day - now.weekday()) % 7)
        if cand <= now:
            cand += timedelta(days=7)
        return ParsedTime(cand, interval, f"every {m.group(1)} at {at[0]:02d}:{at[1]:02d}")
    if re.match(r"^weekly\b", t):
        interval = 7 * 86400
        anchor = t.replace("weekly", "").strip() or "in 1 week"
    when = _parse_single(anchor, tz, now)
    if interval and when <= now:
        while when <= now:
            when += timedelta(seconds=interval)
    return ParsedTime(when, interval, text.strip())


def _parse_clock(s: str) -> tuple[int, int]:
    m = re.search(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", s)
    if not m:
        raise ValueError(f"cannot read a time of day from {s!r}")
    h, mi = int(m.group(1)), int(m.group(2) or 0)
    if m.group(3) == "pm" and h < 12:
        h += 12
    if m.group(3) == "am" and h == 12:
        h = 0
    if not (0 <= h < 24 and 0 <= mi < 60):
        raise ValueError(f"invalid time {s!r}")
    return h, mi


def _parse_single(text: str, tz: tzinfo, now: datetime) -> datetime:
    t = text.strip()
    m = re.match(r"^in\s+(\d+(?:\.\d+)?|a|an|one|two|three|five|ten|fifteen|twenty|thirty)\s*(second|sec|minute|min|hour|hr|day|week)s?$", t)
    if m:
        words = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "five": 5, "ten": 10, "fifteen": 15, "twenty": 20, "thirty": 30}
        n = float(words.get(m.group(1), m.group(1)))
        unit = {"sec": "second", "min": "minute", "hr": "hour"}.get(m.group(2), m.group(2))
        return now + timedelta(seconds=n * _UNITS[unit])
    m = re.match(r"^(?:at\s+)?(\d{1,2}(?::\d{2})?\s*(?:am|pm)?)$", t)
    if m and (":" in m.group(1) or "am" in m.group(1) or "pm" in m.group(1) or t.startswith("at")):
        h, mi = _parse_clock(m.group(1))
        cand = now.replace(hour=h, minute=mi, second=0, microsecond=0)
        if cand <= now:
            cand += timedelta(days=1)
        return cand
    import dateparser

    tz_key = getattr(tz, "key", None) or now.strftime("%z")
    settings = {"PREFER_DATES_FROM": "future", "RETURN_AS_TIMEZONE_AWARE": True, "TIMEZONE": str(tz_key),
                "TO_TIMEZONE": str(tz_key), "RELATIVE_BASE": now.replace(tzinfo=None)}
    parsed = dateparser.parse(t.removeprefix("at ").removeprefix("on "), settings=settings)  # type: ignore[arg-type]
    if parsed is None:
        raise ValueError(f"cannot understand the time {text!r}")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=tz)
    if parsed <= now and not re.search(r"\d{4}", t):
        parsed += timedelta(days=1)
    return parsed
