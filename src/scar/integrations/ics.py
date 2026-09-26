"""Calendar event model plus a small RFC 5545 (iCalendar) serializer and parser.

Supported: VCALENDAR/VEVENT with UID, DTSTAMP, DTSTART/DTEND (UTC ``Z``,
``TZID=`` local times, floating times, ``VALUE=DATE`` all-day), DURATION,
SUMMARY, LOCATION, DESCRIPTION, ATTENDEE (``CN`` + ``mailto:``) and VALARM
``TRIGGER`` (relative durations, ``RELATED=END``, absolute date-times).
Output uses CRLF line endings, TEXT escaping and 75-octet line folding.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta, tzinfo
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from scar.core.errors import ToolError

PRODID = "-//SCAR//SCAR Calendar 1.0//EN"

# Common Windows time-zone names Outlook writes into TZID, mapped to IANA.
WINDOWS_TZ: dict[str, str] = {
    "UTC": "UTC",
    "GMT Standard Time": "Europe/London",
    "Greenwich Standard Time": "Atlantic/Reykjavik",
    "W. Europe Standard Time": "Europe/Berlin",
    "Romance Standard Time": "Europe/Paris",
    "Central Europe Standard Time": "Europe/Budapest",
    "Central European Standard Time": "Europe/Warsaw",
    "E. Europe Standard Time": "Europe/Chisinau",
    "FLE Standard Time": "Europe/Kiev",
    "GTB Standard Time": "Europe/Bucharest",
    "Russian Standard Time": "Europe/Moscow",
    "Eastern Standard Time": "America/New_York",
    "Central Standard Time": "America/Chicago",
    "Mountain Standard Time": "America/Denver",
    "US Mountain Standard Time": "America/Phoenix",
    "Pacific Standard Time": "America/Los_Angeles",
    "Alaskan Standard Time": "America/Anchorage",
    "Hawaiian Standard Time": "Pacific/Honolulu",
    "Atlantic Standard Time": "America/Halifax",
    "E. South America Standard Time": "America/Sao_Paulo",
    "India Standard Time": "Asia/Kolkata",
    "China Standard Time": "Asia/Shanghai",
    "Tokyo Standard Time": "Asia/Tokyo",
    "Korea Standard Time": "Asia/Seoul",
    "Singapore Standard Time": "Asia/Singapore",
    "Arabian Standard Time": "Asia/Dubai",
    "AUS Eastern Standard Time": "Australia/Sydney",
    "New Zealand Standard Time": "Pacific/Auckland",
    "South Africa Standard Time": "Africa/Johannesburg",
}


@dataclass
class Attendee:
    address: str
    name: str = ""

    def display(self) -> str:
        return f"{self.name} <{self.address}>" if self.name else self.address


@dataclass
class CalendarEvent:
    title: str
    start: datetime
    end: datetime
    location: str = ""
    description: str = ""
    attendees: list[Attendee] = field(default_factory=list)
    reminder_minutes: int | None = None
    uid: str = ""
    event_id: str = ""
    provider: str = "local"
    all_day: bool = False
    link: str = ""

    def __post_init__(self) -> None:
        if self.start.tzinfo is None or self.end.tzinfo is None:
            raise ValueError("CalendarEvent times must be timezone-aware")
        if self.end < self.start:
            raise ToolError("event end is before its start", "InvalidInput")
        if not self.uid:
            self.uid = f"{uuid.uuid4()}@scar"

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "provider": self.provider,
            "title": self.title,
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "all_day": self.all_day,
            "location": self.location,
            "description": self.description,
            "attendees": [{"address": a.address, "name": a.name} for a in self.attendees],
            "reminder_minutes": self.reminder_minutes,
            "uid": self.uid,
            "link": self.link,
        }


@dataclass
class ParsedCalendar:
    events: list[CalendarEvent]
    warnings: list[str] = field(default_factory=list)


# ------------------------------------------------------------------ serializer
def escape_text(value: str) -> str:
    return (
        value.replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\r\n", "\\n")
        .replace("\n", "\\n")
        .replace("\r", "\\n")
    )


def fold_line(line: str) -> str:
    """Fold to <=75 octets per physical line (RFC 5545 3.1), never splitting a UTF-8 sequence."""
    out: list[str] = []
    current = ""
    limit = 75
    for ch in line:
        if len((current + ch).encode("utf-8")) > limit:
            out.append(current)
            current = " " + ch
            limit = 75
        else:
            current += ch
    out.append(current)
    return "\r\n".join(out)


def _param_value(value: str) -> str:
    clean = value.replace('"', "'")
    return f'"{clean}"' if re.search(r"[:;,]", clean) else clean


def format_utc(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")


def _trigger(minutes: int) -> str:
    sign = "-" if minutes >= 0 else ""
    m = abs(minutes)
    days, rem = divmod(m, 1440)
    hours, mins = divmod(rem, 60)
    out = f"{sign}P"
    if days:
        out += f"{days}D"
    if hours or mins or not days:
        out += "T"
        if hours:
            out += f"{hours}H"
        if mins or not hours:
            out += f"{mins}M"
    return out


def to_ics(events: list[CalendarEvent], *, now: datetime | None = None) -> str:
    stamp = format_utc(now or datetime.now(UTC))
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", f"PRODID:{PRODID}", "CALSCALE:GREGORIAN", "METHOD:PUBLISH"]
    for ev in events:
        lines += ["BEGIN:VEVENT", f"UID:{escape_text(ev.uid)}", f"DTSTAMP:{stamp}"]
        if ev.all_day:
            lines.append(f"DTSTART;VALUE=DATE:{ev.start.strftime('%Y%m%d')}")
            lines.append(f"DTEND;VALUE=DATE:{ev.end.strftime('%Y%m%d')}")
        else:
            lines.append(f"DTSTART:{format_utc(ev.start)}")
            lines.append(f"DTEND:{format_utc(ev.end)}")
        lines.append(f"SUMMARY:{escape_text(ev.title)}")
        if ev.location:
            lines.append(f"LOCATION:{escape_text(ev.location)}")
        if ev.description:
            lines.append(f"DESCRIPTION:{escape_text(ev.description)}")
        for att in ev.attendees:
            cn = f";CN={_param_value(att.name)}" if att.name else ""
            lines.append(f"ATTENDEE{cn};ROLE=REQ-PARTICIPANT;RSVP=TRUE:mailto:{att.address}")
        if ev.reminder_minutes is not None:
            lines += [
                "BEGIN:VALARM",
                "ACTION:DISPLAY",
                f"DESCRIPTION:{escape_text(ev.title)}",
                f"TRIGGER:{_trigger(ev.reminder_minutes)}",
                "END:VALARM",
            ]
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    return "\r\n".join(fold_line(line) for line in lines) + "\r\n"


# ------------------------------------------------------------------ parser
@dataclass
class _Prop:
    name: str
    params: dict[str, str]
    value: str


def unfold(text: str) -> list[str]:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"\n[ \t]", "", text)
    return [line for line in text.split("\n") if line.strip()]


def _parse_line(line: str) -> _Prop:
    in_quotes = False
    colon = -1
    for i, ch in enumerate(line):
        if ch == '"':
            in_quotes = not in_quotes
        elif ch == ":" and not in_quotes:
            colon = i
            break
    if colon < 0:
        raise ToolError(f"malformed iCalendar line: {line[:80]!r}", "InvalidICS")
    head, value = line[:colon], line[colon + 1 :]
    parts: list[str] = []
    buf = ""
    in_quotes = False
    for ch in head:
        if ch == '"':
            in_quotes = not in_quotes
            buf += ch
        elif ch == ";" and not in_quotes:
            parts.append(buf)
            buf = ""
        else:
            buf += ch
    parts.append(buf)
    params: dict[str, str] = {}
    for p in parts[1:]:
        k, _, v = p.partition("=")
        params[k.strip().upper()] = v.strip().strip('"')
    return _Prop(parts[0].strip().upper(), params, value)


def unescape_text(value: str) -> str:
    out: list[str] = []
    i = 0
    while i < len(value):
        ch = value[i]
        if ch == "\\" and i + 1 < len(value):
            nxt = value[i + 1]
            out.append("\n" if nxt in "nN" else nxt)
            i += 2
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def resolve_tz(tzid: str) -> tzinfo | None:
    name = tzid.strip().strip('"')
    name = WINDOWS_TZ.get(name, name)
    segments = [s for s in name.split("/") if s]
    # try the full name, then trailing segments ("/mozilla.org/20050126_1/Europe/Berlin" -> "Europe/Berlin")
    for i in range(len(segments)):
        candidate = "/".join(segments[i:])
        try:
            return ZoneInfo(candidate)
        except (ZoneInfoNotFoundError, ValueError, OSError):
            continue
    return None


_DURATION = re.compile(r"^([+-])?P(?:(\d+)W)?(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?$")


def parse_duration(value: str) -> timedelta:
    m = _DURATION.match(value.strip().upper())
    if not m or value.strip().upper() in ("P", "+P", "-P", "PT"):
        raise ToolError(f"bad iCalendar duration {value!r}", "InvalidICS")
    sign, w, d, h, mi, s = m.groups()
    delta = timedelta(weeks=int(w or 0), days=int(d or 0), hours=int(h or 0), minutes=int(mi or 0), seconds=int(s or 0))
    return -delta if sign == "-" else delta


def parse_datetime(prop: _Prop, default_tz: tzinfo, warnings: list[str]) -> tuple[datetime, bool]:
    """(aware datetime, is_all_day)."""
    value = prop.value.strip()
    if prop.params.get("VALUE", "").upper() == "DATE" or re.fullmatch(r"\d{8}", value):
        d = datetime.strptime(value[:8], "%Y%m%d").date()
        return datetime(d.year, d.month, d.day, tzinfo=default_tz), True
    if value.endswith(("Z", "z")):
        return datetime.strptime(value[:-1], "%Y%m%dT%H%M%S").replace(tzinfo=UTC), False
    naive = datetime.strptime(value[:15], "%Y%m%dT%H%M%S")
    tzid = prop.params.get("TZID")
    if tzid:
        tz = resolve_tz(tzid)
        if tz is None:
            warnings.append(f"unknown TZID {tzid!r}; interpreted as local time")
            tz = default_tz
        return naive.replace(tzinfo=tz), False
    return naive.replace(tzinfo=default_tz), False


def parse_ics(text: str, default_tz: tzinfo | None = None) -> ParsedCalendar:
    tz = default_tz or datetime.now().astimezone().tzinfo or UTC
    lines = unfold(text)
    if not lines or lines[0].strip().upper() != "BEGIN:VCALENDAR":
        raise ToolError("not an iCalendar file (missing BEGIN:VCALENDAR)", "InvalidICS")
    events: list[CalendarEvent] = []
    warnings: list[str] = []
    stack: list[str] = []
    ev: dict[str, Any] = {}
    alarm: dict[str, _Prop] = {}
    for raw in lines:
        prop = _parse_line(raw)
        if prop.name == "BEGIN":
            comp = prop.value.strip().upper()
            stack.append(comp)
            if comp == "VEVENT":
                ev = {"attendees": [], "alarms": []}
            elif comp == "VALARM":
                alarm = {}
            continue
        if prop.name == "END":
            comp = prop.value.strip().upper()
            if not stack or stack[-1] != comp:
                raise ToolError(f"unbalanced END:{comp}", "InvalidICS")
            stack.pop()
            if comp == "VALARM" and stack and stack[-1] == "VEVENT":
                ev["alarms"].append(alarm)
            elif comp == "VEVENT":
                built = _build_event(ev, tz, warnings)
                if built is not None:
                    events.append(built)
            continue
        if not stack:
            continue
        current = stack[-1]
        if current == "VEVENT":
            if prop.name == "ATTENDEE":
                addr = re.sub(r"(?i)^mailto:", "", prop.value.strip())
                ev["attendees"].append(Attendee(address=addr, name=prop.params.get("CN", "")))
            else:
                ev.setdefault(prop.name, prop)
        elif current == "VALARM" and len(stack) >= 2 and stack[-2] == "VEVENT":
            alarm.setdefault(prop.name, prop)
    if stack:
        raise ToolError(f"unterminated component {stack[-1]}", "InvalidICS")
    return ParsedCalendar(events, warnings)


def _build_event(ev: dict[str, Any], tz: tzinfo, warnings: list[str]) -> CalendarEvent | None:
    if "DTSTART" not in ev:
        warnings.append("skipped VEVENT without DTSTART")
        return None
    start, all_day = parse_datetime(ev["DTSTART"], tz, warnings)
    if "DTEND" in ev:
        end, _ = parse_datetime(ev["DTEND"], tz, warnings)
    elif "DURATION" in ev:
        end = start + parse_duration(ev["DURATION"].value)
    else:
        end = start + (timedelta(days=1) if all_day else timedelta(0))
    reminder: int | None = None
    for alarm in ev["alarms"]:
        trig = alarm.get("TRIGGER")
        if trig is None:
            continue
        if trig.params.get("VALUE", "").upper() == "DATE-TIME":
            at, _ = parse_datetime(trig, tz, warnings)
            minutes = int((start - at).total_seconds() // 60)
        else:
            delta = parse_duration(trig.value)
            anchor = end if trig.params.get("RELATED", "").upper() == "END" else start
            minutes = int((start - (anchor + delta)).total_seconds() // 60)
        reminder = minutes if reminder is None else max(reminder, minutes)
    uid_prop = ev.get("UID")
    text = {k: unescape_text(ev[k].value) if k in ev else "" for k in ("SUMMARY", "LOCATION", "DESCRIPTION")}
    return CalendarEvent(
        title=text["SUMMARY"] or "(no title)",
        start=start,
        end=end,
        location=text["LOCATION"],
        description=text["DESCRIPTION"],
        attendees=ev["attendees"],
        reminder_minutes=reminder,
        uid=unescape_text(uid_prop.value) if uid_prop else "",
        all_day=all_day,
    )
