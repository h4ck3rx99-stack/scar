"""Calendar tools: list, create, update, delete events and ICS import/export.

Times are ISO 8601 datetimes (the agent converts natural language with
dateparser first). Naive times are interpreted in the user's time zone.
Creating an event on your own calendar is MEDIUM; inviting attendees on a
cloud calendar sends invitations and is HIGH.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, field_validator

from scar.core.errors import CapabilityUnavailable, PathViolation, ToolError
from scar.core.types import Check, RiskLevel, SideEffect, ToolResult, TrustLevel, VerificationResult
from scar.integrations.email_types import EmailAddress
from scar.integrations.ics import Attendee, CalendarEvent, parse_ics
from scar.security.path_guard import PathOp
from scar.security.risk import RiskAssessment
from scar.tools.base import Tool, ToolContext, ToolInput

Provider = Literal["google", "outlook", "local"]


def calendar_service(services: Any) -> Any:
    svc = getattr(services, "calendar", None)
    if svc is None:
        from scar.integrations.calendar import CalendarService

        svc = CalendarService(services.settings, services.secrets, services.db)
        services.calendar = svc
    return svc


def _attendees(values: list[str]) -> list[Attendee]:
    out: list[Attendee] = []
    for v in values:
        addr = EmailAddress.parse(v)
        out.append(Attendee(address=addr.address, name=addr.name))
    return out


def _check_attendees(v: list[str]) -> list[str]:
    try:
        _attendees(v)
    except ToolError as exc:
        raise ValueError(str(exc)) from exc
    return v


def _event_line(e: CalendarEvent) -> str:
    when = e.start.strftime("%a %Y-%m-%d") + (
        " (all day)" if e.all_day else f" {e.start.strftime('%H:%M')}-{e.end.strftime('%H:%M %Z')}"
    )
    extra = f" @ {e.location}" if e.location else ""
    people = f" with {', '.join(a.display() for a in e.attendees)}" if e.attendees else ""
    return f"- [{e.event_id}] {when}: {e.title}{extra}{people}"


def _provider_is_cloud(services: Any, provider: str | None) -> bool:
    try:
        return calendar_service(services).provider_name(provider) != "local"
    except (CapabilityUnavailable, ToolError):
        return True  # unknown -> assume invitations would go out


def _assess_attendees(a: RiskAssessment, ctx: ToolContext, attendees: list[str], provider: str | None) -> None:
    if not attendees:
        return
    for att in _attendees(attendees):
        a.facts.recipients.append(att.address.casefold())
        if att.name:
            a.facts.recipient_names.append(att.name)
    if _provider_is_cloud(ctx.services, provider):
        a.raise_to(RiskLevel.HIGH, f"sends calendar invitations to {len(attendees)} attendee(s)")
        a.facts.external_destination = True
        if ctx.scope is not None and not ctx.scope.capability_in_scope("calendar.invite").in_scope:
            a.raise_to(RiskLevel.HIGH, "the request did not mention inviting anyone")


# ------------------------------------------------------------------ list
class ListInput(ToolInput):
    start: datetime | None = Field(None, description="ISO start (default: now)")
    end: datetime | None = Field(None, description="ISO end (default: start + 7 days)")
    provider: Provider | None = None
    limit: int = Field(100, ge=1, le=1000)


class CalendarList(Tool):
    name = "calendar.list"
    description = "List calendar events in a time range (Google, Outlook or the local calendar)."
    input_model = ListInput
    capabilities = ("calendar.read",)
    output_trust = TrustLevel.UNTRUSTED_EXTERNAL  # invitations carry text written by other people
    data_class = "calendar"
    categories = ("calendar",)

    def describe(self, args: ListInput) -> str:
        return "list calendar events"

    async def run(self, args: ListInput, ctx: ToolContext) -> ToolResult:
        svc = calendar_service(ctx.services)
        start = svc.aware(args.start) if args.start else datetime.now(svc.tz)
        end = svc.aware(args.end) if args.end else start + timedelta(days=7)
        events = await svc.list_events(start, end, provider=args.provider, limit=args.limit)
        view = "\n".join(_event_line(e) for e in events) or "No events in that range."
        return self.ok(
            f"{len(events)} events between {start:%Y-%m-%d %H:%M} and {end:%Y-%m-%d %H:%M}",
            {"events": [e.to_dict() for e in events], "count": len(events), "provider": svc.provider_name(args.provider)},
            model_view=view,
            source="calendar",
        )


# ------------------------------------------------------------------ create
class CreateInput(ToolInput):
    title: str = Field(min_length=1, max_length=500)
    start: datetime
    end: datetime | None = Field(None, description="default: start + 1 hour (all-day: + 1 day)")
    all_day: bool = False
    location: str = ""
    description: str = Field("", max_length=20_000)
    attendees: list[str] = Field(default_factory=list, max_length=100, description="attendee email addresses")
    reminder_minutes: int | None = Field(None, ge=0, le=40320)
    provider: Provider | None = None

    @field_validator("attendees")
    @classmethod
    def _att(cls, v: list[str]) -> list[str]:
        return _check_attendees(v)


class CalendarCreate(Tool):
    name = "calendar.create"
    description = "Create a calendar event. Adding attendees on Google/Outlook sends them invitations (needs approval)."
    input_model = CreateInput
    capabilities = ("calendar.write",)
    base_risk = RiskLevel.MEDIUM
    side_effects = SideEffect.LOCAL
    sensitive_args = {"attendees": "recipient"}
    data_class = "calendar"
    categories = ("calendar",)

    def assess(self, args: CreateInput, ctx: ToolContext) -> RiskAssessment:
        a = RiskAssessment(RiskLevel.MEDIUM)
        _assess_attendees(a, ctx, args.attendees, args.provider)
        return a

    def describe(self, args: CreateInput) -> str:
        who = f", inviting {', '.join(args.attendees)}" if args.attendees else ""
        return f"create event {args.title!r} at {args.start.isoformat()}{who}"

    def _event(self, args: CreateInput, svc: Any) -> CalendarEvent:
        start = svc.aware(args.start)
        if args.all_day:
            start = start.replace(hour=0, minute=0, second=0, microsecond=0)
        end = svc.aware(args.end) if args.end else start + (timedelta(days=1) if args.all_day else timedelta(hours=1))
        return CalendarEvent(
            title=args.title,
            start=start,
            end=end,
            location=args.location,
            description=args.description,
            attendees=_attendees(args.attendees),
            reminder_minutes=args.reminder_minutes,
            all_day=args.all_day,
        )

    async def run(self, args: CreateInput, ctx: ToolContext) -> ToolResult:
        svc = calendar_service(ctx.services)
        created = await svc.create_event(self._event(args, svc), provider=args.provider)
        return self.ok(f"Created {created.title!r} on {created.start:%a %Y-%m-%d %H:%M} ({created.provider})", created.to_dict())

    async def verify(self, args: CreateInput, result: ToolResult, ctx: ToolContext) -> VerificationResult:
        svc = calendar_service(ctx.services)
        ev = await svc.get_event(result.data["event_id"], provider=result.data["provider"])
        checks = [Check(name="event exists", passed=ev is not None, detail=result.data["event_id"])]
        if ev is not None:
            checks.append(Check(name="title matches", passed=ev.title == args.title))
            expected = datetime.fromisoformat(result.data["start"])
            checks.append(Check(name="start matches", passed=abs((ev.start - expected).total_seconds()) < 60))
        return VerificationResult.from_checks(checks, {"event_id": result.data["event_id"]})


# ------------------------------------------------------------------ update
class UpdateInput(ToolInput):
    event_id: str = Field(min_length=1)
    title: str | None = None
    start: datetime | None = None
    end: datetime | None = None
    location: str | None = None
    description: str | None = None
    attendees: list[str] | None = None
    reminder_minutes: int | None = Field(None, ge=0, le=40320)
    provider: Provider | None = None

    @field_validator("attendees")
    @classmethod
    def _att(cls, v: list[str] | None) -> list[str] | None:
        return None if v is None else _check_attendees(v)


def _changes(args: UpdateInput) -> dict[str, Any]:
    fields = ("title", "start", "end", "location", "description", "reminder_minutes")
    out: dict[str, Any] = {f: getattr(args, f) for f in fields if getattr(args, f) is not None}
    if args.attendees is not None:
        out["attendees"] = _attendees(args.attendees)
    return out


class CalendarUpdate(Tool):
    name = "calendar.update"
    description = "Change an existing event (moving it keeps its duration unless `end` is given)."
    input_model = UpdateInput
    capabilities = ("calendar.write",)
    base_risk = RiskLevel.HIGH
    side_effects = SideEffect.LOCAL
    sensitive_args = {"attendees": "recipient"}
    data_class = "calendar"
    categories = ("calendar",)

    def assess(self, args: UpdateInput, ctx: ToolContext) -> RiskAssessment:
        a = RiskAssessment(RiskLevel.HIGH, ["changes an existing event (attendees may be notified)"])
        _assess_attendees(a, ctx, args.attendees or [], args.provider)
        return a

    def describe(self, args: UpdateInput) -> str:
        changed = ", ".join(
            f"{k}={v.isoformat() if isinstance(v, datetime) else v}" for k, v in _changes(args).items() if k != "attendees"
        )
        if args.attendees is not None:
            changed += f"{', ' if changed else ''}attendees={', '.join(args.attendees)}"
        return f"update event {args.event_id}: {changed or 'no changes'}"

    async def run(self, args: UpdateInput, ctx: ToolContext) -> ToolResult:
        changes = _changes(args)
        if not changes:
            raise ToolError("nothing to change", "InvalidInput")
        updated = await calendar_service(ctx.services).update_event(args.event_id, changes, provider=args.provider)
        return self.ok(f"Updated {updated.title!r} ({updated.start:%a %Y-%m-%d %H:%M})", updated.to_dict())

    async def verify(self, args: UpdateInput, result: ToolResult, ctx: ToolContext) -> VerificationResult:
        svc = calendar_service(ctx.services)
        ev = await svc.get_event(args.event_id, provider=result.data.get("provider"))
        checks = [Check(name="event exists", passed=ev is not None)]
        if ev is not None:
            if args.title is not None:
                checks.append(Check(name="title updated", passed=ev.title == args.title))
            if args.start is not None:
                checks.append(Check(name="start updated", passed=abs((ev.start - svc.aware(args.start)).total_seconds()) < 60))
            if args.location is not None:
                checks.append(Check(name="location updated", passed=ev.location == args.location))
        return VerificationResult.from_checks(checks)


# ------------------------------------------------------------------ delete
class DeleteInput(ToolInput):
    event_id: str = Field(min_length=1)
    provider: Provider | None = None
    notify_attendees: bool = True


class CalendarDelete(Tool):
    name = "calendar.delete"
    description = "Delete (cancel) an event. On Google/Outlook attendees receive a cancellation."
    input_model = DeleteInput
    capabilities = ("calendar.delete",)
    base_risk = RiskLevel.HIGH
    side_effects = SideEffect.LOCAL
    data_class = "calendar"
    categories = ("calendar",)

    def _local_title(self, event_id: str) -> str:
        db = getattr(self.services, "db", None)
        if db is None:
            return ""
        row = db.query_one("SELECT title, start FROM calendar_events WHERE event_id = ?", (event_id,))
        return f"{row['title']!r} ({row['start']})" if row else ""

    def describe(self, args: DeleteInput) -> str:
        title = self._local_title(args.event_id)
        return f"delete calendar event {title or args.event_id}"

    def approval_details(self, args: DeleteInput) -> dict[str, Any]:
        return {**args.model_dump(mode="json"), "event": self._local_title(args.event_id) or "(cloud event)"}

    async def run(self, args: DeleteInput, ctx: ToolContext) -> ToolResult:
        svc = calendar_service(ctx.services)
        name = svc.provider_name(args.provider)
        existing = await svc.get_event(args.event_id, provider=name)
        if existing is None:
            raise ToolError(f"event {args.event_id} not found", "NotFound")
        await svc.delete_event(args.event_id, provider=name, notify=args.notify_attendees)
        return self.ok(f"Deleted {existing.title!r}", {"event_id": args.event_id, "provider": name, "title": existing.title})

    async def verify(self, args: DeleteInput, result: ToolResult, ctx: ToolContext) -> VerificationResult:
        ev = await calendar_service(ctx.services).get_event(args.event_id, provider=result.data["provider"])
        return VerificationResult.from_checks([Check(name="event no longer exists", passed=ev is None)])


# ------------------------------------------------------------------ ICS
class ImportInput(ToolInput):
    path: str
    provider: Provider | None = None


class CalendarImportIcs(Tool):
    name = "calendar.import_ics"
    description = "Import events from an .ics file into the calendar (duplicates by UID are skipped locally)."
    input_model = ImportInput
    capabilities = ("calendar.write",)
    base_risk = RiskLevel.MEDIUM
    side_effects = SideEffect.LOCAL
    sensitive_args = {"path": "path"}
    data_class = "calendar"
    categories = ("calendar",)

    def assess(self, args: ImportInput, ctx: ToolContext) -> RiskAssessment:
        a = RiskAssessment(RiskLevel.MEDIUM)
        chk = ctx.services.path_guard.check(args.path, PathOp.READ, base=ctx.services.extras.get("cwd"))
        if chk.denied:
            a.deny("; ".join(chk.reasons), "secret_paths")
        a.raise_to(chk.risk, "; ".join(chk.reasons) if chk.risk > RiskLevel.MEDIUM else "")
        a.facts.paths.append(chk.canonical)
        return a

    def describe(self, args: ImportInput) -> str:
        return f"import events from {args.path}"

    async def run(self, args: ImportInput, ctx: ToolContext) -> ToolResult:
        chk = ctx.services.path_guard.check(args.path, PathOp.READ, base=ctx.services.extras.get("cwd"))
        if chk.denied:
            raise PathViolation(chk.canonical, "; ".join(chk.reasons))
        info = await calendar_service(ctx.services).import_ics(chk.path, provider=args.provider)
        info["path"] = str(chk.path)
        return self.ok(
            f"Imported {len(info['created'])} events from {chk.path.name}"
            + (f" ({len(info['skipped_existing_uids'])} already present)" if info["skipped_existing_uids"] else ""),
            info,
        )

    async def verify(self, args: ImportInput, result: ToolResult, ctx: ToolContext) -> VerificationResult:
        svc = calendar_service(ctx.services)
        checks: list[Check] = []
        for ev in result.data["created"][:50]:
            got = await svc.get_event(ev["event_id"], provider=result.data["provider"])
            checks.append(Check(name=f"imported {ev['title'][:40]}", passed=got is not None))
        if not checks:
            return VerificationResult(verified=None, note="no new events to verify", evidence=result.data)
        return VerificationResult.from_checks(checks)


class ExportInput(ToolInput):
    path: str = Field(description="destination .ics file")
    start: datetime
    end: datetime
    provider: Provider | None = None


class CalendarExportIcs(Tool):
    name = "calendar.export_ics"
    description = "Export events in a time range to an .ics file (RFC 5545)."
    input_model = ExportInput
    capabilities = ("calendar.read", "fs.write")
    base_risk = RiskLevel.MEDIUM
    side_effects = SideEffect.LOCAL
    sensitive_args = {"path": "path"}
    data_class = "calendar"
    categories = ("calendar",)

    def assess(self, args: ExportInput, ctx: ToolContext) -> RiskAssessment:
        a = RiskAssessment(RiskLevel.MEDIUM)
        chk = ctx.services.path_guard.check(args.path, PathOp.WRITE, base=ctx.services.extras.get("cwd"))
        if chk.denied:
            a.deny("; ".join(chk.reasons), "secret_paths")
        a.raise_to(chk.risk, "; ".join(chk.reasons) if chk.risk > RiskLevel.MEDIUM else "")
        if chk.path.exists():
            a.raise_to(RiskLevel.HIGH, "overwrites an existing file")
        a.facts.paths.append(chk.canonical)
        return a

    def describe(self, args: ExportInput) -> str:
        return f"export events {args.start:%Y-%m-%d}..{args.end:%Y-%m-%d} to {args.path}"

    async def run(self, args: ExportInput, ctx: ToolContext) -> ToolResult:
        chk = ctx.services.path_guard.check(args.path, PathOp.WRITE, base=ctx.services.extras.get("cwd"))
        if chk.denied:
            raise PathViolation(chk.canonical, "; ".join(chk.reasons))
        path = chk.path if chk.path.suffix.lower() == ".ics" else chk.path.with_suffix(chk.path.suffix + ".ics")
        svc = calendar_service(ctx.services)
        info = await svc.export_ics(path, svc.aware(args.start), svc.aware(args.end), provider=args.provider)
        return self.ok(f"Exported {info['count']} events to {path.name}", info)

    async def verify(self, args: ExportInput, result: ToolResult, ctx: ToolContext) -> VerificationResult:
        p = Path(result.data["path"])
        checks = [Check(name="file exists", passed=p.is_file(), detail=str(p))]
        if p.is_file():
            parsed = parse_ics(p.read_text(encoding="utf-8"))
            checks.append(
                Check(
                    name="event count matches",
                    passed=len(parsed.events) == result.data["count"],
                    detail=f"{len(parsed.events)} events",
                )
            )
        return VerificationResult.from_checks(checks)


TOOLS: list[type[Tool]] = [CalendarList, CalendarCreate, CalendarUpdate, CalendarDelete, CalendarImportIcs, CalendarExportIcs]
