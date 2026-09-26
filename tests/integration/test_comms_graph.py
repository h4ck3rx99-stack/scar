"""Microsoft Graph mail/calendar/contacts through respx (no network)."""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import parse_qs

import httpx
import pytest
import respx
from pydantic import SecretStr

from scar.integrations.calendar import CalendarService
from scar.integrations.common import IntegrationAuthError
from scar.integrations.email import EmailService
from scar.integrations.email_types import EmailAddress, OutgoingEmail
from scar.integrations.ics import Attendee, CalendarEvent

TOKEN_URL = "https://login.microsoftonline.com/common/oauth2/v2.0/token"
GRAPH = "https://graph.microsoft.com/v1.0"


class FakeSecrets:
    def __init__(self, **values: str) -> None:
        self.values = dict(values)

    def get(self, name: str) -> SecretStr | None:
        v = self.values.get(name)
        return SecretStr(v) if v else None

    def get_plain(self, name: str) -> str | None:
        return self.values.get(name)

    def has(self, name: str) -> bool:
        return bool(self.values.get(name))

    def set(self, name: str, value: str) -> None:
        self.values[name] = value


@pytest.fixture
def ms_settings(settings, monkeypatch: pytest.MonkeyPatch):
    settings.ms_client_id = "00000000-1111-2222-3333-444444444444"
    settings.ms_tenant = "common"
    settings.email_provider = "outlook"
    settings.calendar_provider = "outlook"
    settings.google_oauth_client_file = ""
    settings.test_email_to = "carol@contoso.example"
    monkeypatch.setenv("SCAR_LIVE_TESTS", "1")
    return settings


@pytest.fixture
def secrets() -> FakeSecrets:
    return FakeSecrets(SCAR_MS_REFRESH_TOKEN="ms-rt-old")


def _token(mock: respx.MockRouter) -> respx.Route:
    return mock.post(TOKEN_URL).mock(
        return_value=httpx.Response(
            200,
            json={"access_token": "eyJ0.graph-token", "refresh_token": "ms-rt-new", "expires_in": 3600, "token_type": "Bearer"},
        )
    )


async def test_send_creates_message_with_attachment_sends_and_verifies_sent_items(ms_settings, secrets, tmp_path: Path) -> None:
    att = tmp_path / "notes.txt"
    att.write_bytes(b"meeting notes\n")
    with respx.mock(assert_all_mocked=True, assert_all_called=True) as mock:
        token = _token(mock)
        create = mock.post(f"{GRAPH}/me/messages").mock(
            return_value=httpx.Response(
                201, json={"id": "AAMkDraft1", "internetMessageId": "<DM6PR@contoso.example>", "conversationId": "conv-1"}
            )
        )
        send = mock.post(f"{GRAPH}/me/messages/AAMkDraft1/send").mock(return_value=httpx.Response(202))
        sent = mock.get(f"{GRAPH}/me/mailFolders/sentitems/messages").mock(
            return_value=httpx.Response(
                200, json={"value": [{"id": "AAMkSent9", "internetMessageId": "<DM6PR@contoso.example>"}]}
            )
        )
        svc = EmailService(ms_settings, secrets)
        receipt = await svc.send(
            OutgoingEmail(
                to=[EmailAddress("carol@contoso.example", "Carol")], subject="Notes", body="See attached.", attachments=[att]
            )
        )
        verification = await svc.verify(receipt)

    form = parse_qs(token.calls.last.request.content.decode())
    assert form["grant_type"] == ["refresh_token"] and "offline_access" in form["scope"][0]
    assert "client_secret" not in form  # public client
    assert secrets.values["SCAR_MS_REFRESH_TOKEN"] == "ms-rt-new"  # rotated token persisted to the secret store
    body = json.loads(create.calls.last.request.content)
    assert create.calls.last.request.headers["Authorization"] == "Bearer eyJ0.graph-token"
    assert body["toRecipients"] == [{"emailAddress": {"address": "carol@contoso.example", "name": "Carol"}}]
    assert body["body"] == {"contentType": "Text", "content": "See attached."}
    [a] = body["attachments"]
    assert a["@odata.type"] == "#microsoft.graph.fileAttachment" and a["name"] == "notes.txt"
    assert a["contentType"] == "text/plain" and base64.b64decode(a["contentBytes"]) == b"meeting notes\n"
    assert send.called
    assert sent.calls.last.request.url.params["$filter"] == "internetMessageId eq '<DM6PR@contoso.example>'"
    assert receipt.internet_message_id == "<DM6PR@contoso.example>"
    assert verification.verified is True


async def test_verify_reports_false_when_not_in_sent_items(ms_settings, secrets) -> None:
    from scar.integrations.email_types import SendReceipt
    from scar.integrations.microsoft.auth import microsoft_session
    from scar.integrations.microsoft.graph import GraphClient

    with respx.mock(assert_all_mocked=True) as mock:
        _token(mock)
        mock.get(f"{GRAPH}/me/mailFolders/sentitems/messages").mock(return_value=httpx.Response(200, json={"value": []}))
        client = GraphClient(microsoft_session(ms_settings, secrets), ms_settings, verify_delays=(0.0, 0.0))
        ok = await client.verify_sent(SendReceipt("outlook", "x", ["a@b.c"], internet_message_id="<nope@x>"))
    assert ok is False


async def test_read_parses_body_and_attachments(ms_settings, secrets) -> None:
    with respx.mock(assert_all_mocked=True) as mock:
        _token(mock)
        read = mock.get(f"{GRAPH}/me/messages/MSG1").mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": "MSG1",
                    "subject": "Hi",
                    "from": {"emailAddress": {"address": "dan@x.example", "name": "Dan"}},
                    "toRecipients": [{"emailAddress": {"address": "me@x.example"}}],
                    "body": {"contentType": "text", "content": "Plain body"},
                    "hasAttachments": True,
                    "isRead": False,
                    "internetMessageId": "<m1@x>",
                    "conversationId": "c",
                },
            )
        )
        mock.get(f"{GRAPH}/me/messages/MSG1/attachments").mock(
            return_value=httpx.Response(
                200, json={"value": [{"id": "att1", "name": "a.pdf", "size": 1234, "contentType": "application/pdf"}]}
            )
        )
        msg = await EmailService(ms_settings, secrets).read("MSG1")
    assert read.calls.last.request.headers["Prefer"] == 'outlook.body-content-type="text"'
    assert msg.sender.display() == "Dan <dan@x.example>" and msg.body_text == "Plain body" and msg.unread
    assert msg.attachments[0].filename == "a.pdf" and msg.attachments[0].size == 1234


async def test_graph_401_is_auth_error(ms_settings, secrets) -> None:
    with respx.mock(assert_all_mocked=True) as mock:
        _token(mock)
        mock.get(f"{GRAPH}/me/messages").mock(
            return_value=httpx.Response(
                401, json={"error": {"code": "InvalidAuthenticationToken", "message": "Access token has expired."}}
            )
        )
        with pytest.raises(IntegrationAuthError, match="scar auth microsoft"):
            await EmailService(ms_settings, secrets).search("")


async def test_calendar_crud_request_shapes(ms_settings, secrets, services) -> None:
    ev = CalendarEvent(
        title="Design review",
        start=datetime(2026, 10, 1, 15, 0, tzinfo=UTC),
        end=datetime(2026, 10, 1, 16, 0, tzinfo=UTC),
        location="Room 4",
        attendees=[Attendee("erin@contoso.example", "Erin")],
        reminder_minutes=10,
    )
    graph_event = {
        "id": "EV1",
        "subject": "Design review",
        "start": {"dateTime": "2026-10-01T15:00:00.0000000", "timeZone": "UTC"},
        "end": {"dateTime": "2026-10-01T16:00:00.0000000", "timeZone": "UTC"},
        "location": {"displayName": "Room 4"},
        "attendees": [{"emailAddress": {"address": "erin@contoso.example", "name": "Erin"}, "type": "required"}],
        "isReminderOn": True,
        "reminderMinutesBeforeStart": 10,
        "iCalUId": "uid-1",
        "body": {"contentType": "html", "content": "<p>Agenda</p>"},
    }
    with respx.mock(assert_all_mocked=True) as mock:
        _token(mock)
        create = mock.post(f"{GRAPH}/me/events").mock(return_value=httpx.Response(201, json=graph_event))
        view = mock.get(f"{GRAPH}/me/calendarView").mock(return_value=httpx.Response(200, json={"value": [graph_event]}))
        delete = mock.delete(f"{GRAPH}/me/events/EV1").mock(return_value=httpx.Response(204))
        svc = CalendarService(ms_settings, secrets, services.db)
        created = await svc.create_event(ev)
        listed = await svc.list_events(datetime(2026, 10, 1, tzinfo=UTC), datetime(2026, 10, 2, tzinfo=UTC))
        await svc.delete_event("EV1")
    body = json.loads(create.calls.last.request.content)
    assert body["start"] == {"dateTime": "2026-10-01T15:00:00", "timeZone": "UTC"}
    assert body["attendees"][0]["emailAddress"]["address"] == "erin@contoso.example"
    assert body["reminderMinutesBeforeStart"] == 10 and body["isReminderOn"] is True
    assert created.event_id == "EV1" and created.description == "Agenda" and created.provider == "outlook"
    params = view.calls.last.request.url.params
    assert params["startDateTime"] == "2026-10-01T00:00:00Z" and params["endDateTime"] == "2026-10-02T00:00:00Z"
    assert listed[0].start == datetime(2026, 10, 1, 15, 0, tzinfo=UTC)
    assert delete.called


async def test_contacts_search_uses_startswith_filter(ms_settings, secrets) -> None:
    with respx.mock(assert_all_mocked=True) as mock:
        _token(mock)
        route = mock.get(f"{GRAPH}/me/contacts").mock(
            return_value=httpx.Response(
                200,
                json={
                    "value": [
                        {
                            "id": "c1",
                            "displayName": "O'Brien Pat",
                            "emailAddresses": [{"address": "pat@x.example"}],
                            "mobilePhone": "+1 555 0100",
                            "businessPhones": [],
                        }
                    ]
                },
            )
        )
        found = await EmailService(ms_settings, secrets).search_contacts("O'Brien")
    assert "startswith(displayName,'O''Brien')" in route.calls.last.request.url.params["$filter"]
    assert found == [
        {"name": "O'Brien Pat", "emails": ["pat@x.example"], "phones": ["+1 555 0100"], "source": "outlook", "remote_id": "c1"}
    ]
