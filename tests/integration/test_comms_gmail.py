"""Gmail + Google OAuth refresh through respx (no network): request construction, parsing, error taxonomy."""

from __future__ import annotations

import base64
import email
import email.policy
import json
from pathlib import Path
from urllib.parse import parse_qs

import httpx
import pytest
import respx
from pydantic import SecretStr

from scar.core.errors import CapabilityUnavailable
from scar.integrations.common import IntegrationAuthError, RateLimited
from scar.integrations.email import EmailService
from scar.integrations.email_types import EmailAddress, OutgoingEmail
from scar.integrations.live_guard import LiveSendBlocked

TOKEN_URL = "https://oauth2.googleapis.com/token"
GMAIL = "https://gmail.googleapis.com/gmail/v1/users/me"


class FakeSecrets:
    """In-memory stand-in for SecretStore (tests never touch the real credential manager)."""

    def __init__(self, **values: str) -> None:
        self.values = dict(values)
        self.set_calls: list[tuple[str, str]] = []

    def get(self, name: str) -> SecretStr | None:
        v = self.values.get(name)
        return SecretStr(v) if v else None

    def get_plain(self, name: str) -> str | None:
        return self.values.get(name)

    def has(self, name: str) -> bool:
        return bool(self.values.get(name))

    def set(self, name: str, value: str) -> None:
        self.values[name] = value
        self.set_calls.append((name, value))


@pytest.fixture
def gmail_settings(settings, tmp_path: Path):
    client = tmp_path / "client_secret.json"
    client.write_text(
        json.dumps(
            {
                "installed": {
                    "client_id": "123-abc.apps.googleusercontent.com",
                    "client_secret": "GOCSPX-notreallysecret",
                    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                    "token_uri": TOKEN_URL,
                    "redirect_uris": ["http://localhost"],
                }
            }
        ),
        encoding="utf-8",
    )
    settings.google_oauth_client_file = str(client)
    settings.email_provider = "gmail"
    settings.test_email_to = "alice@example.com"
    return settings


@pytest.fixture
def secrets() -> FakeSecrets:
    return FakeSecrets(SCAR_GOOGLE_REFRESH_TOKEN="1//refresh-token-value")


@pytest.fixture
def live(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SCAR_LIVE_TESTS", "1")


def _token_route(mock: respx.MockRouter, token: str = "ya29.access-1") -> respx.Route:
    return mock.post(TOKEN_URL).mock(
        return_value=httpx.Response(200, json={"access_token": token, "expires_in": 3599, "token_type": "Bearer"})
    )


async def test_send_builds_multipart_mime_with_attachment_and_verifies_sent_label(
    gmail_settings, secrets, live, tmp_path: Path
) -> None:
    att = tmp_path / "report.pdf"
    payload = bytes(range(256)) * 4
    att.write_bytes(payload)
    with respx.mock(assert_all_mocked=True, assert_all_called=True) as mock:
        token = _token_route(mock)
        send = mock.post(f"{GMAIL}/messages/send").mock(
            return_value=httpx.Response(200, json={"id": "18c0ffee", "threadId": "t-1", "labelIds": ["SENT"]})
        )
        check = mock.get(f"{GMAIL}/messages/18c0ffee").mock(
            return_value=httpx.Response(200, json={"id": "18c0ffee", "labelIds": ["SENT"]})
        )
        svc = EmailService(gmail_settings, secrets)
        receipt = await svc.send(
            OutgoingEmail(
                to=[EmailAddress("alice@example.com", "Alice Smith")],
                subject="Quarterly report",
                body="Hi Alice,\nattached.\n",
                attachments=[att],
            )
        )
        verification = await svc.verify(receipt)

    assert receipt.message_id == "18c0ffee" and receipt.thread_id == "t-1"
    form = parse_qs(token.calls.last.request.content.decode())
    assert form["grant_type"] == ["refresh_token"] and form["refresh_token"] == ["1//refresh-token-value"]
    assert form["client_secret"] == ["GOCSPX-notreallysecret"] and "scope" not in form
    req = send.calls.last.request
    assert req.headers["Authorization"] == "Bearer ya29.access-1"
    raw = json.loads(req.content)["raw"]
    mime = email.message_from_bytes(base64.urlsafe_b64decode(raw), policy=email.policy.default)
    assert mime["To"] == "Alice Smith <alice@example.com>"
    assert mime["Subject"] == "Quarterly report"
    assert mime.get_content_type() == "multipart/mixed"
    parts = list(mime.iter_attachments())
    assert [p.get_filename() for p in parts] == ["report.pdf"]
    assert parts[0].get_content_type() == "application/pdf"
    assert parts[0]["Content-Transfer-Encoding"] == "base64"
    assert parts[0].get_payload(decode=True) == payload
    assert mime.get_body(("plain",)).get_content().replace("\r\n", "\n") == "Hi Alice,\nattached.\n"  # CRLF on the wire
    assert check.calls.last.request.url.params["format"] == "minimal"
    assert verification.verified is True
    assert [c.name for c in verification.checks] == ["provider returned a message id", "message is in the Sent folder"]


async def test_live_guard_blocks_send_without_live_flag(gmail_settings, secrets, monkeypatch) -> None:
    monkeypatch.delenv("SCAR_LIVE_TESTS", raising=False)
    with respx.mock(assert_all_mocked=True, assert_all_called=False) as mock:
        send = mock.post(f"{GMAIL}/messages/send")
        svc = EmailService(gmail_settings, secrets)
        with pytest.raises(LiveSendBlocked):
            await svc.send(OutgoingEmail(to=[EmailAddress("alice@example.com")], subject="x", body="y"))
    assert not send.called


async def test_live_guard_blocks_non_test_recipient(gmail_settings, secrets, live) -> None:
    with respx.mock(assert_all_mocked=True, assert_all_called=False) as mock:
        send = mock.post(f"{GMAIL}/messages/send")
        svc = EmailService(gmail_settings, secrets)
        with pytest.raises(LiveSendBlocked, match="not the configured test recipient"):
            await svc.send(OutgoingEmail(to=[EmailAddress("bob@example.com")], subject="x", body="y"))
    assert not send.called


async def test_search_and_read_parse_messages(gmail_settings, secrets) -> None:
    body_b64 = base64.urlsafe_b64encode(b"Hello from Bob.\nSecond line.").decode().rstrip("=")
    full = {
        "id": "m1",
        "threadId": "t9",
        "snippet": "Hello from Bob",
        "labelIds": ["INBOX", "UNREAD"],
        "payload": {
            "mimeType": "multipart/mixed",
            "headers": [
                {"name": "From", "value": "Bob Jones <bob@example.com>"},
                {"name": "To", "value": "me@example.com"},
                {"name": "Subject", "value": "Lunch?"},
                {"name": "Date", "value": "Mon, 1 Sep 2026 10:00:00 +0000"},
                {"name": "Message-ID", "value": "<abc@mail.example.com>"},
            ],
            "parts": [
                {"mimeType": "text/plain", "body": {"size": 28, "data": body_b64}},
                {"mimeType": "image/png", "filename": "pic.png", "body": {"size": 2048, "attachmentId": "A1"}},
            ],
        },
    }
    with respx.mock(assert_all_mocked=True, assert_all_called=False) as mock:
        _token_route(mock)
        lst = mock.get(f"{GMAIL}/messages").mock(return_value=httpx.Response(200, json={"messages": [{"id": "m1"}]}))
        mock.get(f"{GMAIL}/messages/m1").mock(return_value=httpx.Response(200, json=full))
        svc = EmailService(gmail_settings, secrets)
        results = await svc.search("from:bob newer_than:7d", 5)
        msg = await svc.read("m1")
    assert lst.calls[0].request.url.params["q"] == "from:bob newer_than:7d"
    assert results[0].subject == "Lunch?" and results[0].sender.address == "bob@example.com" and results[0].unread
    assert msg.body_text == "Hello from Bob.\nSecond line."
    assert msg.attachments[0].filename == "pic.png" and msg.attachments[0].size == 2048
    assert msg.message_id_header == "<abc@mail.example.com>" and msg.thread_id == "t9"


async def test_401_refreshes_once_then_raises_capability_unavailable(gmail_settings, secrets) -> None:
    with respx.mock(assert_all_mocked=True, assert_all_called=False) as mock:
        token = _token_route(mock)
        mock.get(f"{GMAIL}/messages").mock(
            return_value=httpx.Response(
                401, json={"error": {"code": 401, "message": "Request had invalid authentication credentials."}}
            )
        )
        svc = EmailService(gmail_settings, secrets)
        with pytest.raises(IntegrationAuthError) as info:
            await svc.search("x")
    assert token.call_count == 2  # the cached token was invalidated and refreshed before giving up
    assert isinstance(info.value, CapabilityUnavailable)
    assert "scar auth google" in info.value.prerequisite and info.value.setup_doc == "docs/integrations/gmail.md"


async def test_revoked_refresh_token_mentions_testing_status_expiry(gmail_settings, secrets) -> None:
    with respx.mock(assert_all_mocked=True, assert_all_called=False) as mock:
        mock.post(TOKEN_URL).mock(
            return_value=httpx.Response(
                400, json={"error": "invalid_grant", "error_description": "Token has been expired or revoked."}
            )
        )
        with pytest.raises(IntegrationAuthError) as info:
            await EmailService(gmail_settings, secrets).search("x")
    assert "7 days" in info.value.prerequisite and "scar auth google" in info.value.prerequisite


async def test_429_retry_after_short_is_retried_long_is_surfaced(gmail_settings, secrets) -> None:
    with respx.mock(assert_all_mocked=True, assert_all_called=False) as mock:
        _token_route(mock)
        route = mock.get(f"{GMAIL}/messages")
        route.side_effect = [
            httpx.Response(429, headers={"Retry-After": "0"}, json={"error": {"message": "slow"}}),
            httpx.Response(200, json={"messages": []}),
        ]
        svc = EmailService(gmail_settings, secrets)
        assert await svc.search("x") == []
        assert route.call_count == 2
        route.side_effect = None
        route.mock(
            return_value=httpx.Response(429, headers={"Retry-After": "120"}, json={"error": {"message": "Quota exceeded"}})
        )
        with pytest.raises(RateLimited) as info:
            await svc.search("x")
    assert info.value.retry_after == 120.0 and info.value.error_type == "RateLimited"


async def test_unconfigured_gmail_names_prerequisite(settings) -> None:
    settings.email_provider = "gmail"
    settings.google_oauth_client_file = ""
    with pytest.raises(CapabilityUnavailable) as info:
        await EmailService(settings, FakeSecrets()).search("x")
    assert info.value.prerequisite == ("GMAIL not configured: set SCAR_GOOGLE_OAUTH_CLIENT_FILE and run `scar auth google`")
    assert info.value.setup_doc == "docs/integrations/gmail.md"


async def test_auto_with_nothing_configured_is_unavailable(settings) -> None:
    settings.email_provider = "auto"
    for attr in ("google_oauth_client_file", "ms_client_id", "imap_host"):
        setattr(settings, attr, "")
    with pytest.raises(CapabilityUnavailable, match="EMAIL not configured"):
        EmailService(settings, FakeSecrets()).provider()


async def test_signed_out_gmail_says_run_auth(gmail_settings) -> None:
    with pytest.raises(CapabilityUnavailable, match="scar auth google"):
        await EmailService(gmail_settings, FakeSecrets()).search("x")


# ------------------------------------------------------------------ IMAP/SMTP alternative (no sockets)
def test_imap_search_criteria_translation() -> None:
    from scar.integrations.imap_smtp import build_search_criteria

    crit, literal = build_search_criteria('from:bob subject:"q3 report" since:2026-09-01 unread invoice')
    assert crit == ["FROM", '"bob"', "SUBJECT", '"q3 report"', "SINCE", "01-Sep-2026", "UNSEEN", "TEXT", '"invoice"']
    assert literal is None
    crit, literal = build_search_criteria("Überweisung")
    assert crit == ["TEXT"] and literal == "Überweisung".encode()
    assert build_search_criteria("") == (["ALL"], None)


async def test_smtp_send_uses_starttls_login_and_message_id(settings, monkeypatch, live) -> None:
    import smtplib

    from scar.integrations.imap_smtp import ImapSmtpClient

    sent: dict = {}

    class FakeSMTP:
        def __init__(self, host: str, port: int, timeout: float) -> None:
            sent["connect"] = (host, port)
            sent["steps"] = []

        def __enter__(self):
            return self

        def __exit__(self, *exc) -> None:
            sent["steps"].append("quit")

        def ehlo(self) -> None:
            sent["steps"].append("ehlo")

        def starttls(self, context) -> None:
            sent["steps"].append("starttls")

        def login(self, user: str, pw: str) -> None:
            sent["steps"].append(f"login:{user}:{pw}")

        def send_message(self, msg) -> dict:
            sent["msg"] = msg
            return {}

    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    settings.imap_host = "imap.mail.example"
    settings.imap_user = "me@mail.example"
    settings.smtp_host = "smtp.mail.example"
    settings.smtp_port = 587
    settings.test_email_to = "alice@example.com"
    client = ImapSmtpClient(settings, FakeSecrets(SCAR_IMAP_PASSWORD="app-pass"))
    receipt = await client.send(OutgoingEmail(to=[EmailAddress("alice@example.com")], subject="Hi", body="Body"))
    assert sent["connect"] == ("smtp.mail.example", 587)
    assert sent["steps"] == ["ehlo", "starttls", "ehlo", "login:me@mail.example:app-pass", "quit"]
    assert sent["msg"]["From"] == "me@mail.example" and sent["msg"]["Message-ID"] == receipt.message_id
    assert receipt.message_id.endswith("@mail.example>") and receipt.accepted


async def test_imap_unconfigured_names_missing_parts(settings) -> None:
    from scar.integrations.imap_smtp import ImapSmtpClient

    settings.imap_host = "imap.mail.example"
    settings.imap_user = ""
    with pytest.raises(CapabilityUnavailable) as info:
        await ImapSmtpClient(settings, FakeSecrets()).search("x")
    assert "SCAR_IMAP_USER" in info.value.detail and "SCAR_IMAP_PASSWORD" in info.value.detail
