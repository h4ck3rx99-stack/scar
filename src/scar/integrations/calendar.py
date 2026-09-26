"""CalendarService: Google Calendar, Outlook (Graph) and an always-available local SQLite calendar.

The local calendar uses the ``calendar_events`` table and needs no
credentials. ``calendar_provider=auto`` uses Google or Outlook when signed in
and falls back to the local calendar otherwise. ICS import/export works with
any provider (see :mod:`scar.integrations.ics`). Callers parse natural
language times (dateparser); this API takes timezone-aware datetimes (naive
ones are interpreted in the configured/local time zone).
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, time, timedelta, tzinfo
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx

from scar.core.errors import CapabilityUnavailable, ToolError
from scar.core.ids import new_id
from scar.integrations.ics import Attendee, CalendarEvent, ParsedCalendar, parse_ics, to_ics
from scar.storage.db import now_iso

CALENDAR_DOC = "docs/integrations/calendar.md"
PROVIDERS = ("google", "outlook", "local")
MAX_ICS_BYTES = 10 * 1024 * 1024
UPDATABLE = ("title", "start", "end", "location", "description", "attendees", "reminder_minutes", "all_day")


def local_timezone(settings: Any) -> tzinfo:
    name = str(getattr(settings, "timezone", "") or "")
    if name:
        try:
            return ZoneInfo(name)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ToolError(f"SCAR_TIMEZONE={name!r} is not a valid IANA time zone", "ConfigError") from exc
    return datetime.now().astimezone().tzinfo or UTC


def ensure_aware(dt: datetime, tz: tzinfo) -> datetime:
    return dt.replace(tzinfo=tz) if dt.tzinfo is None else dt


def _utc_key(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _from_key(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)


class LocalCalendar:
    """SQLite-backed calendar (table ``calendar_events``); always available."""

    name = "local"

    def __init__(self, db: Any, tz: tzinfo) -> None:
        self.db = db
        self.tz = tz

    def _is_all_day(self, start: datetime, end: datetime) -> bool:
        s, e = start.astimezone(self.tz), end.astimezone(self.tz)
        span = e - s
        return s.time() == time(0) and e.time() == time(0) and span >= timedelta(days=1) and span.seconds == 0

    def _row_to_event(self, row: dict[str, Any]) -> CalendarEvent:
        start, end = _from_key(row["start"]), _from_key(row["end"])
        attendees = [Attendee(address=a["address"], name=a.get("name", "")) for a in json.loads(row["attendees_json"])]
        return CalendarEvent(
            title=row["title"],
            start=start.astimezone(self.tz),
            end=end.astimezone(self.tz),
            location=row["location"],
            description=row["description"],
            attendees=attendees,
            reminder_minutes=row["reminder_minutes"],
            uid=row["uid"],
            event_id=row["event_id"],
            provider=self.name,
            all_day=self._is_all_day(start, end),
        )

    @staticmethod
    def _attendees_json(ev: CalendarEvent) -> str:
        return json.dumps([{"address": a.address, "name": a.name} for a in ev.attendees], ensure_ascii=False)

    async def list_events(self, start: datetime, end: datetime, limit: int = 250) -> list[CalendarEvent]:
        rows = await self.db.aquery(
            "SELECT * FROM calendar_events WHERE start < ? AND end > ? ORDER BY start LIMIT ?",
            (_utc_key(end), _utc_key(start), limit),
        )
        # zero-length events exactly at the window start are still "in" the window
        rows += [
            r
            for r in await self.db.aquery("SELECT * FROM calendar_events WHERE start = end AND start = ?", (_utc_key(start),))
            if r["event_id"] not in {x["event_id"] for x in rows}
        ]
        return [self._row_to_event(r) for r in rows]

    async def get_event(self, event_id: str) -> CalendarEvent | None:
        rows = await self.db.aquery("SELECT * FROM calendar_events WHERE event_id = ?", (event_id,))
        return self._row_to_event(rows[0]) if rows else None

    async def find_by_uid(self, uid: str) -> CalendarEvent | None:
        rows = await self.db.aquery("SELECT * FROM calendar_events WHERE uid = ?", (uid,))
        return self._row_to_event(rows[0]) if rows else None

    async def create_event(self, ev: CalendarEvent) -> CalendarEvent:
        event_id = new_id("evt")
        now = now_iso()
        await self.db.aexecute(
            "INSERT INTO calendar_events(event_id, title, start, end, location, description, attendees_json, "
            "reminder_minutes, uid, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                event_id,
                ev.title,
                _utc_key(ev.start),
                _utc_key(ev.end),
                ev.location,
                ev.description,
                self._attendees_json(ev),
                ev.reminder_minutes,
                ev.uid,
                now,
                now,
            ),
        )
        created = await self.get_event(event_id)
        if created is None:
            raise ToolError("local calendar insert did not persist", "StorageError")
        return created

    async def update_event(self, event_id: str, ev: CalendarEvent) -> CalendarEvent:
        changed = await self.db.aexecute(
            "UPDATE calendar_events SET title = ?, start = ?, end = ?, location = ?, description = ?, "
            "attendees_json = ?, reminder_minutes = ?, updated_at = ? WHERE event_id = ?",
            (
                ev.title,
                _utc_key(ev.start),
                _utc_key(ev.end),
                ev.location,
                ev.description,
                self._attendees_json(ev),
                ev.reminder_minutes,
                now_iso(),
                event_id,
            ),
        )
        if not changed:
            raise ToolError(f"event {event_id} not found", "NotFound")
        updated = await self.get_event(event_id)
        if updated is None:
            raise ToolError(f"event {event_id} vanished during update", "StorageError")
        return updated

    async def delete_event(self, event_id: str, notify: bool = True) -> None:
        changed = await self.db.aexecute("DELETE FROM calendar_events WHERE event_id = ?", (event_id,))
        if not changed:
            raise ToolError(f"event {event_id} not found", "NotFound")


class CalendarService:
    def __init__(self, settings: Any, secrets: Any, db: Any, *, http: httpx.AsyncClient | None = None) -> None:
        self.settings = settings
        self.secrets = secrets
        self.db = db
        self._http = http
        self._providers: dict[str, Any] = {}

    @property
    def tz(self) -> tzinfo:
        return local_timezone(self.settings)

    def aware(self, dt: datetime) -> datetime:
        return ensure_aware(dt, self.tz)

    # ------------------------------------------------------------ providers
    def provider_name(self, override: str | None = None) -> str:
        from scar.integrations.google.auth import google_signed_in
        from scar.integrations.microsoft.auth import microsoft_signed_in

        choice = (override or str(getattr(self.settings, "calendar_provider", "auto") or "auto")).lower()
        if choice == "auto":
            if google_signed_in(self.settings, self.secrets):
                return "google"
            if microsoft_signed_in(self.settings, self.secrets):
                return "outlook"
            return "local"
        if choice not in PROVIDERS:
            raise ToolError(f"unknown calendar provider {choice!r}; use one of {', '.join(PROVIDERS)}", "InvalidInput")
        return choice

    def provider(self, override: str | None = None) -> Any:
        name = self.provider_name(override)
        if name in self._providers:
            return self._providers[name]
        if name == "google":
            from scar.integrations.google.auth import google_configured, google_session
            from scar.integrations.google.calendar import GoogleCalendarClient

            if not google_configured(self.settings):
                raise CapabilityUnavailable(
                    "GOOGLE CALENDAR not configured: set SCAR_GOOGLE_OAUTH_CLIENT_FILE and run `scar auth google`", CALENDAR_DOC
                )
            prov: Any = GoogleCalendarClient(google_session(self.settings, self.secrets, self._http), self._http)
        elif name == "outlook":
            from scar.integrations.microsoft.auth import microsoft_configured, microsoft_session
            from scar.integrations.microsoft.graph import GraphClient

            if not microsoft_configured(self.settings):
                raise CapabilityUnavailable(
                    "OUTLOOK CALENDAR not configured: set SCAR_MS_CLIENT_ID and run `scar auth microsoft`", CALENDAR_DOC
                )
            prov = GraphClient(microsoft_session(self.settings, self.secrets, self._http), self.settings, self._http)
        else:
            prov = LocalCalendar(self.db, self.tz)
        self._providers[name] = prov
        return prov

    # ------------------------------------------------------------ CRUD
    async def list_events(
        self, start: datetime, end: datetime, *, provider: str | None = None, limit: int = 250
    ) -> list[CalendarEvent]:
        start, end = self.aware(start), self.aware(end)
        if end <= start:
            raise ToolError("the end of the range must be after its start", "InvalidInput")
        events = await self.provider(provider).list_events(start, end, limit)
        return sorted(events, key=lambda e: e.start)

    async def get_event(self, event_id: str, *, provider: str | None = None) -> CalendarEvent | None:
        return await self.provider(provider).get_event(event_id)

    async def create_event(self, ev: CalendarEvent, *, provider: str | None = None) -> CalendarEvent:
        return await self.provider(provider).create_event(ev)

    async def update_event(self, event_id: str, changes: dict[str, Any], *, provider: str | None = None) -> CalendarEvent:
        prov = self.provider(provider)
        current = await prov.get_event(event_id)
        if current is None:
            raise ToolError(f"event {event_id} not found", "NotFound")
        unknown = set(changes) - set(UPDATABLE)
        if unknown:
            raise ToolError(f"cannot update {sorted(unknown)}", "InvalidInput")
        values = dict(changes)
        for key in ("start", "end"):
            if key in values and isinstance(values[key], datetime):
                values[key] = self.aware(values[key])
        if "start" in values and "end" not in values:
            values["end"] = values["start"] + (current.end - current.start)  # keep the duration when moving
        if "attendees" in values:
            values["attendees"] = [a if isinstance(a, Attendee) else Attendee(**a) for a in values["attendees"]]
        merged = replace(current, **values)
        return await prov.update_event(event_id, merged)

    async def delete_event(self, event_id: str, *, provider: str | None = None, notify: bool = True) -> None:
        await self.provider(provider).delete_event(event_id, notify)

    # ------------------------------------------------------------ ICS
    async def import_ics(self, path: Path, *, provider: str | None = None) -> dict[str, Any]:
        if not path.is_file():
            raise ToolError(f"file not found: {path}", "NotFound")
        if path.stat().st_size > MAX_ICS_BYTES:
            raise ToolError(f"{path.name} is larger than {MAX_ICS_BYTES} bytes", "TooLarge")
        parsed: ParsedCalendar = parse_ics(path.read_text(encoding="utf-8-sig", errors="replace"), self.tz)
        name = self.provider_name(provider)
        prov = self.provider(name)
        created: list[CalendarEvent] = []
        skipped: list[str] = []
        for ev in parsed.events:
            if isinstance(prov, LocalCalendar) and ev.uid and await prov.find_by_uid(ev.uid) is not None:
                skipped.append(ev.uid)
                continue
            created.append(await prov.create_event(ev))
        return {
            "provider": name,
            "created": [e.to_dict() for e in created],
            "skipped_existing_uids": skipped,
            "warnings": parsed.warnings,
        }

    async def export_ics(self, path: Path, start: datetime, end: datetime, *, provider: str | None = None) -> dict[str, Any]:
        events = await self.list_events(start, end, provider=provider, limit=5000)
        text = to_ics(events)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{path.name}.scar-tmp")
        tmp.write_bytes(text.encode("utf-8"))
        tmp.replace(path)
        return {
            "path": str(path),
            "count": len(events),
            "uids": [e.uid for e in events],
            "provider": self.provider_name(provider),
        }
