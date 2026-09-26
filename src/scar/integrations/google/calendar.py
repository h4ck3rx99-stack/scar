"""Google Calendar API v3: events list/get/create/update/delete on the primary calendar."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import quote

import httpx

from scar.core.errors import ToolError
from scar.integrations.common import ApiClient, ServiceInfo, TokenSource
from scar.integrations.google.auth import CALENDAR_DOC, LOGIN_COMMAND
from scar.integrations.ics import Attendee, CalendarEvent

CAL_BASE = "https://www.googleapis.com/calendar/v3"


def _parse_when(obj: dict[str, Any], tz: Any) -> tuple[datetime, bool]:
    if obj.get("dateTime"):
        return datetime.fromisoformat(str(obj["dateTime"]).replace("Z", "+00:00")), False
    if obj.get("date"):
        d = datetime.strptime(str(obj["date"]), "%Y-%m-%d")
        return d.replace(tzinfo=tz), True
    raise ToolError("Google Calendar event has no start/end", "ProviderError")


def event_from_google(raw: dict[str, Any], tz: Any = UTC) -> CalendarEvent:
    start, all_day = _parse_when(raw.get("start") or {}, tz)
    end, _ = _parse_when(raw.get("end") or {}, tz)
    reminder: int | None = None
    overrides = (raw.get("reminders") or {}).get("overrides") or []
    if overrides:
        reminder = max(int(o.get("minutes", 0)) for o in overrides)
    return CalendarEvent(
        title=str(raw.get("summary") or "(no title)"),
        start=start,
        end=end,
        location=str(raw.get("location") or ""),
        description=str(raw.get("description") or ""),
        attendees=[
            Attendee(address=str(a.get("email")), name=str(a.get("displayName") or ""))
            for a in raw.get("attendees") or []
            if a.get("email") and not a.get("self")
        ],
        reminder_minutes=reminder,
        uid=str(raw.get("iCalUID") or ""),
        event_id=str(raw.get("id") or ""),
        provider="google",
        all_day=all_day,
        link=str(raw.get("htmlLink") or ""),
    )


def event_to_google(ev: CalendarEvent) -> dict[str, Any]:
    body: dict[str, Any] = {"summary": ev.title, "location": ev.location, "description": ev.description}
    if ev.all_day:
        body["start"] = {"date": ev.start.date().isoformat()}
        end_day = ev.end.date() if ev.end.date() > ev.start.date() else ev.start.date() + timedelta(days=1)
        body["end"] = {"date": end_day.isoformat()}
    else:
        body["start"] = {"dateTime": ev.start.isoformat()}
        body["end"] = {"dateTime": ev.end.isoformat()}
    if ev.attendees:
        body["attendees"] = [{"email": a.address, **({"displayName": a.name} if a.name else {})} for a in ev.attendees]
    if ev.reminder_minutes is not None:
        body["reminders"] = {"useDefault": False, "overrides": [{"method": "popup", "minutes": ev.reminder_minutes}]}
    if ev.uid and not ev.uid.endswith("@scar"):
        body["iCalUID"] = ev.uid
    return body


class GoogleCalendarClient:
    name = "google"

    def __init__(self, tokens: TokenSource, http: httpx.AsyncClient | None = None, calendar_id: str = "primary") -> None:
        self.api = ApiClient(
            ServiceInfo("Google Calendar", CALENDAR_DOC, f"run `{LOGIN_COMMAND}`"), tokens=tokens, http=http, base_url=CAL_BASE
        )
        self.calendar_id = calendar_id

    def _events(self, suffix: str = "") -> str:
        return f"calendars/{quote(self.calendar_id, safe='')}/events{suffix}"

    async def list_events(self, start: datetime, end: datetime, limit: int = 250) -> list[CalendarEvent]:
        out: list[CalendarEvent] = []
        page: str | None = None
        while True:
            params: dict[str, Any] = {
                "timeMin": start.isoformat(),
                "timeMax": end.isoformat(),
                "singleEvents": "true",
                "orderBy": "startTime",
                "maxResults": min(250, limit),
            }
            if page:
                params["pageToken"] = page
            body = await self.api.get_json(self._events(), params=params) or {}
            out += [event_from_google(e, start.tzinfo or UTC) for e in body.get("items") or [] if e.get("status") != "cancelled"]
            page = body.get("nextPageToken")
            if not page or len(out) >= limit:
                return out[:limit]

    async def get_event(self, event_id: str) -> CalendarEvent | None:
        try:
            raw = await self.api.get_json(self._events(f"/{quote(event_id, safe='')}"))
        except ToolError as exc:
            if exc.error_type == "NotFound":
                return None
            raise
        if not raw or raw.get("status") == "cancelled":
            return None
        return event_from_google(raw)

    async def create_event(self, ev: CalendarEvent) -> CalendarEvent:
        params = {"sendUpdates": "all" if ev.attendees else "none"}
        raw = await self.api.post_json(self._events(), event_to_google(ev), params=params)
        return event_from_google(raw or {})

    async def update_event(self, event_id: str, ev: CalendarEvent) -> CalendarEvent:
        params = {"sendUpdates": "all" if ev.attendees else "none"}
        body = event_to_google(ev)
        body.pop("iCalUID", None)
        resp = await self.api.request("PATCH", self._events(f"/{quote(event_id, safe='')}"), json=body, params=params)
        return event_from_google(resp.json())

    async def delete_event(self, event_id: str, notify: bool = True) -> None:
        await self.api.request(
            "DELETE", self._events(f"/{quote(event_id, safe='')}"), params={"sendUpdates": "all" if notify else "none"}
        )
