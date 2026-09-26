"""ICS serializer/parser round trip, the local SQLite calendar, and calendar tools through the pipeline."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from pydantic import SecretStr

from scar.core.errors import ToolError
from scar.core.types import RiskLevel, ToolStatus
from scar.integrations.calendar import CalendarService
from scar.integrations.ics import Attendee, CalendarEvent, fold_line, parse_ics, to_ics
from scar.security.approval import ApprovalResponse

MODULES = ["scar.tools.internal", "scar.tools.calendar.tools", "scar.tools.contacts.tools"]


class FakeSecrets:
    def get(self, name: str) -> SecretStr | None:
        return None

    def has(self, name: str) -> bool:
        return False


@pytest.fixture
def cal(services) -> CalendarService:
    services.settings.calendar_provider = "local"
    services.settings.timezone = "Europe/Berlin"
    return CalendarService(services.settings, FakeSecrets(), services.db)


# ------------------------------------------------------------------ ICS
def test_ics_round_trip_preserves_fields_and_folds_lines() -> None:
    berlin = ZoneInfo("Europe/Berlin")
    long_desc = "Agenda: " + "discuss the budget, timeline; risks \\ owners. " * 6 + "Überprüfung – ✓"
    events = [
        CalendarEvent(
            title="Review; budget, Q3",
            start=datetime(2026, 11, 3, 9, 30, tzinfo=berlin),
            end=datetime(2026, 11, 3, 10, 15, tzinfo=berlin),
            location="Room 1, Building B",
            description=long_desc + "\nsecond line",
            attendees=[Attendee("ann@example.com", "Smith, Ann"), Attendee("bo@example.com")],
            reminder_minutes=90,
            uid="evt-1@scar.test",
        ),
        CalendarEvent(
            title="Holiday",
            start=datetime(2026, 12, 24, tzinfo=berlin),
            end=datetime(2026, 12, 26, tzinfo=berlin),
            all_day=True,
            uid="evt-2@scar.test",
        ),
    ]
    text = to_ics(events, now=datetime(2026, 9, 1, tzinfo=UTC))
    assert text.startswith("BEGIN:VCALENDAR\r\nVERSION:2.0\r\n") and text.endswith("END:VCALENDAR\r\n")
    for physical in text.split("\r\n"):
        assert len(physical.encode("utf-8")) <= 75
    assert "DTSTART:20261103T083000Z" in text and "DTSTART;VALUE=DATE:20261224" in text
    assert "TRIGGER:-PT1H30M" in text and 'ATTENDEE;CN="Smith, Ann"' in text
    assert "SUMMARY:Review\\; budget\\, Q3" in text

    parsed = parse_ics(text, berlin)
    assert parsed.warnings == []
    a, b = parsed.events
    assert a.title == "Review; budget, Q3" and a.location == "Room 1, Building B"
    assert a.description == long_desc + "\nsecond line"
    assert a.start == events[0].start and a.end == events[0].end and a.start.tzinfo == UTC
    assert [(x.address, x.name) for x in a.attendees] == [("ann@example.com", "Smith, Ann"), ("bo@example.com", "")]
    assert a.reminder_minutes == 90 and a.uid == "evt-1@scar.test"
    assert b.all_day and b.start == datetime(2026, 12, 24, tzinfo=berlin) and b.end - b.start == timedelta(days=2)


def test_parse_foreign_ics_with_tzid_duration_and_alarms() -> None:
    ics = "\r\n".join(
        [
            "BEGIN:VCALENDAR",
            "VERSION:2.0",
            "PRODID:-//Other//EN",
            "BEGIN:VTIMEZONE",
            "TZID:W. Europe Standard Time",
            "BEGIN:STANDARD",
            "DTSTART:16010101T030000",
            "TZOFFSETFROM:+0200",
            "TZOFFSETTO:+0100",
            "END:STANDARD",
            "END:VTIMEZONE",
            "BEGIN:VEVENT",
            "UID:a1",
            "DTSTART;TZID=W. Europe Standard Time:20260715T140000",
            "DTEND;TZID=W. Europe Standard Time:20260715T150000",
            "SUMMARY:Outlook meeting",
            "DESCRIPTION:Line one\\nLine two with a very long text that is folded ov",
            " er two lines",
            "BEGIN:VALARM",
            "ACTION:DISPLAY",
            "TRIGGER;RELATED=START:-PT15M",
            "END:VALARM",
            "END:VEVENT",
            "BEGIN:VEVENT",
            "UID:a2",
            "DTSTART;TZID=/mozilla.org/20050126_1/America/New_York:20260301T090000",
            "DURATION:PT45M",
            "SUMMARY:Thunderbird",
            "BEGIN:VALARM",
            "ACTION:DISPLAY",
            "TRIGGER;RELATED=END:-PT1H",
            "END:VALARM",
            "END:VEVENT",
            "BEGIN:VEVENT",
            "UID:a3",
            "DTSTART:20260401T120000Z",
            "DTEND:20260401T130000Z",
            "SUMMARY:UTC",
            "BEGIN:VALARM",
            "ACTION:DISPLAY",
            "TRIGGER;VALUE=DATE-TIME:20260401T113000Z",
            "END:VALARM",
            "END:VEVENT",
            "BEGIN:VEVENT",
            "UID:a4",
            "DTSTART;TZID=Mars/Olympus:20260501T100000",
            "SUMMARY:Unknown tz",
            "END:VEVENT",
            "END:VCALENDAR",
            "",
        ]
    )
    parsed = parse_ics(ics, UTC)
    e1, e2, e3, e4 = parsed.events
    assert e1.start == datetime(2026, 7, 15, 14, 0, tzinfo=ZoneInfo("Europe/Berlin"))
    assert e1.description == "Line one\nLine two with a very long text that is folded over two lines"
    assert e1.reminder_minutes == 15
    assert e2.start.utcoffset() == timedelta(hours=-5) and e2.end - e2.start == timedelta(minutes=45)
    assert e2.reminder_minutes == 15  # 1h before the END of a 45-minute event = 15 min before its start
    assert e3.reminder_minutes == 30 and e3.start == datetime(2026, 4, 1, 12, tzinfo=UTC)
    assert e4.start.tzinfo == UTC and any("Mars/Olympus" in w for w in parsed.warnings)


def test_parse_rejects_garbage() -> None:
    with pytest.raises(ToolError):
        parse_ics("hello world")
    with pytest.raises(ToolError):
        parse_ics("BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nDTSTART:20260101T000000Z\r\nEND:VCALENDAR\r\n")


def test_fold_never_splits_utf8() -> None:
    line = "DESCRIPTION:" + "é" * 100
    folded = fold_line(line)
    for part in folded.split("\r\n"):
        assert len(part.encode("utf-8")) <= 75
    assert folded.replace("\r\n ", "") == line


# ------------------------------------------------------------------ local calendar CRUD
async def test_local_calendar_crud(cal: CalendarService) -> None:
    tz = ZoneInfo("Europe/Berlin")
    created = await cal.create_event(
        CalendarEvent(
            title="Dentist",
            start=cal.aware(datetime(2026, 10, 5, 9, 0)),
            end=cal.aware(datetime(2026, 10, 5, 9, 45)),
            location="Main St",
            reminder_minutes=30,
            attendees=[Attendee("dr@example.com", "Dr. Who")],
        )
    )
    assert created.event_id.startswith("evt_") and created.provider == "local"
    assert created.start == datetime(2026, 10, 5, 9, 0, tzinfo=tz)
    listed = await cal.list_events(datetime(2026, 10, 5), datetime(2026, 10, 6))
    assert [e.title for e in listed] == ["Dentist"] and listed[0].attendees[0].name == "Dr. Who"
    assert await cal.list_events(datetime(2026, 10, 6), datetime(2026, 10, 7)) == []
    moved = await cal.update_event(created.event_id, {"start": datetime(2026, 10, 6, 14, 0), "title": "Dentist (moved)"})
    assert moved.start == datetime(2026, 10, 6, 14, 0, tzinfo=tz) and moved.end - moved.start == timedelta(minutes=45)
    assert moved.title == "Dentist (moved)" and moved.reminder_minutes == 30
    await cal.delete_event(created.event_id)
    assert await cal.get_event(created.event_id) is None
    with pytest.raises(ToolError):
        await cal.delete_event(created.event_id)


async def test_local_calendar_ics_export_import_dedupes(cal: CalendarService, sandbox: Path) -> None:
    await cal.create_event(
        CalendarEvent(
            title="Standup",
            start=cal.aware(datetime(2026, 10, 7, 9)),
            end=cal.aware(datetime(2026, 10, 7, 9, 15)),
            uid="standup-1@scar",
        )
    )
    out = sandbox / "week.ics"
    info = await cal.export_ics(out, datetime(2026, 10, 5), datetime(2026, 10, 12))
    assert info["count"] == 1 and out.read_bytes().count(b"\r\n") > 5
    again = await cal.import_ics(out)
    assert again["created"] == [] and again["skipped_existing_uids"] == ["standup-1@scar"]


# ------------------------------------------------------------------ tools through the pipeline
@pytest.fixture
async def pipe(services):
    from scar.runtime.registration import build_registry
    from scar.tools.pipeline import ToolPipeline

    services.settings.calendar_provider = "local"
    services.settings.timezone = "Europe/Berlin"
    registry = build_registry(services, modules=MODULES)
    return ToolPipeline(services, registry)


async def test_calendar_tools_create_export_import_delete(pipe, services, ctx_factory, sandbox: Path) -> None:
    ctx = ctx_factory("add a calendar event for the dentist next monday and export my calendar to week.ics")
    obs = await pipe.execute("calendar.create", {"title": "Dentist", "start": "2026-10-05T09:00:00", "reminder_minutes": 15}, ctx)
    assert obs.result.ok, obs.result.summary
    assert obs.result.verification.verified is True
    event_id = obs.result.data["event_id"]
    assert obs.result.data["end"].startswith("2026-10-05T10:00:00")

    obs = await pipe.execute("calendar.list", {"start": "2026-10-05T00:00:00", "end": "2026-10-06T00:00:00"}, ctx)
    assert obs.result.ok and obs.result.data["count"] == 1 and "Dentist" in obs.result.model_view

    target = sandbox / "week.ics"
    obs = await pipe.execute(
        "calendar.export_ics", {"path": str(target), "start": "2026-10-01T00:00:00", "end": "2026-10-31T00:00:00"}, ctx
    )
    assert obs.result.ok and obs.result.verification.verified is True
    assert "SUMMARY:Dentist" in target.read_text(encoding="utf-8")

    # delete is HIGH -> needs approval
    services.approvals.attach_channel("cli")
    task = asyncio.create_task(pipe.execute("calendar.delete", {"event_id": event_id}, ctx))
    for _ in range(100):
        await asyncio.sleep(0.02)
        if services.approvals.pending():
            break
    req = services.approvals.pending()[0]
    assert req.risk == RiskLevel.HIGH and "Dentist" in req.summary
    services.approvals.resolve(req.request_id, ApprovalResponse.ALLOW_ONCE, "cli")
    obs = await task
    assert obs.result.ok and obs.result.verification.verified is True

    obs = await pipe.execute("calendar.import_ics", {"path": str(target)}, ctx)
    assert obs.result.ok and len(obs.result.data["created"]) == 1 and obs.result.verification.verified is True


async def test_calendar_export_to_secret_path_denied(pipe, ctx_factory) -> None:
    obs = await pipe.execute(
        "calendar.export_ics",
        {"path": "~/.ssh/cal.ics", "start": "2026-10-01T00:00:00", "end": "2026-10-02T00:00:00"},
        ctx_factory("export calendar"),
    )
    assert obs.result.status == ToolStatus.DENIED


async def test_calendar_invite_on_cloud_is_high(services, ctx_factory) -> None:
    from scar.tools.calendar.tools import CalendarCreate, CreateInput

    services.settings.calendar_provider = "google"
    tool = CalendarCreate(services)
    args = CreateInput(title="Sync", start=datetime(2026, 10, 5, 9), attendees=["Ann <ann@example.com>"])
    a = tool.assess(args, ctx_factory("schedule a sync meeting with ann"))
    assert a.level == RiskLevel.HIGH and a.facts.recipients == ["ann@example.com"] and a.facts.external_destination
    services.settings.calendar_provider = "local"
    local = tool.assess(args, ctx_factory("schedule a sync meeting with ann"))
    assert local.level == RiskLevel.MEDIUM  # local calendar sends no invitations
