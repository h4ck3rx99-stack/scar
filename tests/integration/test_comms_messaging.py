"""Telegram Bot API, Discord REST v10 and WhatsApp Cloud API through respx (no network)."""

from __future__ import annotations

import json

import httpx
import pytest
import respx
from pydantic import SecretStr

from scar.core.errors import CapabilityUnavailable, ToolError
from scar.integrations.common import IntegrationAuthError, RateLimited
from scar.integrations.live_guard import LiveSendBlocked
from scar.integrations.messaging import MessagingService

BOT_TOKEN = "123456789:AAtest-token-value-abcdefghijklmnopqrstuv"
DISCORD_CHANNEL = "112233445566778899"
DISCORD_USER = "998877665544332211"
WA_PHONE_ID = "106540352242922"


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
def msg_settings(settings, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("SCAR_LIVE_TESTS", "1")
    settings.test_telegram_chat = "424242"
    settings.test_discord_channel = DISCORD_CHANNEL
    settings.whatsapp_phone_number_id = WA_PHONE_ID
    return settings


@pytest.fixture
def svc(msg_settings) -> MessagingService:
    return MessagingService(
        msg_settings,
        FakeSecrets(TELEGRAM_BOT_TOKEN=BOT_TOKEN, DISCORD_BOT_TOKEN="discord-bot-tok", WHATSAPP_CLOUD_TOKEN="EAAG-wa-token"),
    )


# ------------------------------------------------------------------ Telegram
async def test_telegram_send_message(svc: MessagingService) -> None:
    with respx.mock(assert_all_mocked=True) as mock:
        route = mock.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage").mock(
            return_value=httpx.Response(
                200, json={"ok": True, "result": {"message_id": 77, "chat": {"id": 424242}, "date": 1, "text": "hi"}}
            )
        )
        result = await svc.send("telegram", "424242", "hi")
        verification = await svc.verify(result)
    body = json.loads(route.calls.last.request.content)
    assert body == {"chat_id": 424242, "text": "hi", "disable_web_page_preview": True}
    assert result["message_id"] == "77" and result["chat_id"] == "424242" and result["provider"] == "bot"
    assert verification.verified is True


async def test_telegram_get_me_and_updates(svc: MessagingService) -> None:
    with respx.mock(assert_all_mocked=True) as mock:
        mock.post(f"https://api.telegram.org/bot{BOT_TOKEN}/getMe").mock(
            return_value=httpx.Response(200, json={"ok": True, "result": {"id": 1, "is_bot": True, "username": "scar_bot"}})
        )
        upd = mock.post(f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates").mock(
            return_value=httpx.Response(
                200,
                json={
                    "ok": True,
                    "result": [
                        {
                            "update_id": 5,
                            "message": {
                                "message_id": 9,
                                "chat": {"id": 424242, "first_name": "Ann"},
                                "from": {"first_name": "Ann"},
                                "date": 2,
                                "text": "ping",
                            },
                        },
                        {"update_id": 6, "message": {"message_id": 10, "chat": {"id": 1}, "date": 3, "text": "other"}},
                    ],
                },
            )
        )
        me = await svc.telegram.get_me()
        recent = await svc.recent("telegram", "424242")
    assert me["username"] == "scar_bot"
    assert json.loads(upd.calls.last.request.content)["allowed_updates"] == ["message", "channel_post"]
    assert [m["text"] for m in recent] == ["ping"]


async def test_telegram_errors(svc: MessagingService) -> None:
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    with respx.mock(assert_all_mocked=True) as mock:
        mock.post(url).mock(
            return_value=httpx.Response(
                429,
                json={
                    "ok": False,
                    "error_code": 429,
                    "description": "Too Many Requests: retry after 31",
                    "parameters": {"retry_after": 31},
                },
            )
        )
        with pytest.raises(RateLimited) as rl:
            await svc.send("telegram", "424242", "hi")
        mock.post(url).mock(
            return_value=httpx.Response(401, json={"ok": False, "error_code": 401, "description": "Unauthorized"})
        )
        with pytest.raises(IntegrationAuthError):
            await svc.send("telegram", "424242", "hi")
    assert rl.value.retry_after == 31.0


async def test_telegram_unconfigured(msg_settings) -> None:
    with pytest.raises(CapabilityUnavailable, match="TELEGRAM bot not configured"):
        await MessagingService(msg_settings, FakeSecrets()).send("telegram", "424242", "hi")


async def test_telegram_live_guard(svc: MessagingService, monkeypatch: pytest.MonkeyPatch) -> None:
    with respx.mock(assert_all_mocked=True, assert_all_called=False) as mock:
        route = mock.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage")
        with pytest.raises(LiveSendBlocked):
            await svc.send("telegram", "999", "hi")  # not the configured test chat
        monkeypatch.delenv("SCAR_LIVE_TESTS")
        with pytest.raises(LiveSendBlocked):
            await svc.send("telegram", "424242", "hi")
    assert not route.called


# ------------------------------------------------------------------ Discord
async def test_discord_channel_send_and_verify(svc: MessagingService) -> None:
    base = "https://discord.com/api/v10"
    with respx.mock(assert_all_mocked=True) as mock:
        post = mock.post(f"{base}/channels/{DISCORD_CHANNEL}/messages").mock(
            return_value=httpx.Response(
                200, json={"id": "1234567890123456789", "channel_id": DISCORD_CHANNEL, "content": "deploy done"}
            )
        )
        get = mock.get(f"{base}/channels/{DISCORD_CHANNEL}/messages/1234567890123456789").mock(
            return_value=httpx.Response(200, json={"id": "1234567890123456789"})
        )
        result = await svc.send("discord", f"channel:{DISCORD_CHANNEL}", "deploy done")
        verification = await svc.verify(result)
    req = post.calls.last.request
    assert req.headers["Authorization"] == "Bot discord-bot-tok"
    assert req.headers["User-Agent"].startswith("DiscordBot (")
    assert json.loads(req.content) == {"content": "deploy done", "allowed_mentions": {"parse": []}}
    assert result["message_id"] == "1234567890123456789" and get.called and verification.verified is True


async def test_discord_dm_opens_channel_first(svc: MessagingService, msg_settings) -> None:
    msg_settings.test_discord_channel = DISCORD_USER
    base = "https://discord.com/api/v10"
    with respx.mock(assert_all_mocked=True) as mock:
        dm = mock.post(f"{base}/users/@me/channels").mock(
            return_value=httpx.Response(200, json={"id": "555555555555555555", "type": 1})
        )
        post = mock.post(f"{base}/channels/555555555555555555/messages").mock(
            return_value=httpx.Response(200, json={"id": "666666666666666666", "channel_id": "555555555555555555"})
        )
        result = await svc.send("discord", f"user:{DISCORD_USER}", "hello")
    assert json.loads(dm.calls.last.request.content) == {"recipient_id": DISCORD_USER}
    assert post.called and result["channel_id"] == "555555555555555555" and result["provider"] == "user"


async def test_discord_rate_limit_and_auth(svc: MessagingService) -> None:
    url = f"https://discord.com/api/v10/channels/{DISCORD_CHANNEL}/messages"
    with respx.mock(assert_all_mocked=True) as mock:
        mock.post(url).mock(
            return_value=httpx.Response(
                429,
                headers={"Retry-After": "65"},
                json={"message": "You are being rate limited.", "retry_after": 64.5, "global": False},
            )
        )
        with pytest.raises(RateLimited) as rl:
            await svc.send("discord", DISCORD_CHANNEL, "x")
        mock.post(url).mock(return_value=httpx.Response(401, json={"message": "401: Unauthorized", "code": 0}))
        with pytest.raises(IntegrationAuthError, match="DISCORD_BOT_TOKEN"):
            await svc.send("discord", DISCORD_CHANNEL, "x")
    assert rl.value.retry_after == 65.0


async def test_discord_rejects_non_snowflake_and_long_text(svc: MessagingService) -> None:
    with pytest.raises(ToolError, match="snowflake"):
        await svc.send("discord", "general", "x")
    with pytest.raises(ToolError, match="2000"):
        await svc.send("discord", DISCORD_CHANNEL, "x" * 2001)


# ------------------------------------------------------------------ WhatsApp Cloud API
async def test_whatsapp_cloud_send_text(svc: MessagingService, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SCAR_TEST_WHATSAPP_TO", "15551234567")
    with respx.mock(assert_all_mocked=True) as mock:
        route = mock.post(f"https://graph.facebook.com/v23.0/{WA_PHONE_ID}/messages").mock(
            return_value=httpx.Response(
                200,
                json={
                    "messaging_product": "whatsapp",
                    "contacts": [{"input": "15551234567", "wa_id": "15551234567"}],
                    "messages": [{"id": "wamid.HBgLMTU1NTEyMzQ1NjcVAgARGBI"}],
                },
            )
        )
        result = await svc.send("whatsapp", "+1 (555) 123-4567", "Running late")
    req = route.calls.last.request
    assert req.headers["Authorization"] == "Bearer EAAG-wa-token"
    assert json.loads(req.content) == {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": "15551234567",
        "type": "text",
        "text": {"preview_url": False, "body": "Running late"},
    }
    assert result["message_id"] == "wamid.HBgLMTU1NTEyMzQ1NjcVAgARGBI" and result["provider"] == "cloud_api"


async def test_whatsapp_blocked_under_test_without_recipient_setting(svc: MessagingService, monkeypatch) -> None:
    monkeypatch.delenv("SCAR_TEST_WHATSAPP_TO", raising=False)
    with respx.mock(assert_all_mocked=True, assert_all_called=False) as mock:
        route = mock.post(f"https://graph.facebook.com/v23.0/{WA_PHONE_ID}/messages")
        with pytest.raises(LiveSendBlocked, match="no test recipient"):
            await svc.send("whatsapp", "+15551234567", "x")
    assert not route.called


async def test_whatsapp_error_taxonomy(msg_settings) -> None:
    from scar.integrations.whatsapp.cloud_api import WhatsAppCloud

    wa = WhatsAppCloud(msg_settings, FakeSecrets(WHATSAPP_CLOUD_TOKEN="EAAG-wa-token"))
    url = f"https://graph.facebook.com/v23.0/{WA_PHONE_ID}/messages"
    with respx.mock(assert_all_mocked=True) as mock:
        mock.post(url).mock(
            return_value=httpx.Response(
                400,
                json={
                    "error": {
                        "message": "Re-engagement message",
                        "type": "OAuthException",
                        "code": 131047,
                        "error_data": {"details": "Message failed to send because more than 24 hours have passed"},
                    }
                },
            )
        )
        with pytest.raises(ToolError) as closed:
            await wa._post({"messaging_product": "whatsapp", "to": "15551234567", "type": "text", "text": {"body": "x"}})
        mock.post(url).mock(
            return_value=httpx.Response(
                401, json={"error": {"message": "Error validating access token: Session has expired", "code": 190}}
            )
        )
        with pytest.raises(IntegrationAuthError, match="WHATSAPP_CLOUD_TOKEN"):
            await wa._post({"messaging_product": "whatsapp"})
        mock.post(url).mock(
            return_value=httpx.Response(
                429, headers={"Retry-After": "30"}, json={"error": {"message": "Rate limit hit", "code": 130429}}
            )
        )
        with pytest.raises(RateLimited):
            await wa._post({"messaging_product": "whatsapp"})
    assert closed.value.error_type == "WindowClosed"


async def test_whatsapp_unconfigured_and_desktop_off_by_default(settings) -> None:
    settings.whatsapp_phone_number_id = ""
    assert settings.whatsapp_desktop_enabled is False
    svc = MessagingService(settings, FakeSecrets())
    with pytest.raises(CapabilityUnavailable, match="WHATSAPP not configured"):
        await svc.send("whatsapp", "+15551234567", "x")
    with pytest.raises(CapabilityUnavailable, match="disabled"):
        await svc.whatsapp_desktop.send_text("+15551234567", "x")


def test_whatsapp_desktop_uri_is_encoded() -> None:
    from scar.integrations.whatsapp.desktop import build_send_uri

    assert build_send_uri("15551234567", "hi & bye?") == "whatsapp://send?phone=15551234567&text=hi%20%26%20bye%3F"
