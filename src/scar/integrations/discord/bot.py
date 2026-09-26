"""Discord bot REST API v10 via httpx (``DISCORD_BOT_TOKEN``) plus webhook sends.

Only a *bot* account is ever automated. Automating a normal user account
("self-bot") violates Discord's Terms of Service and is not supported.
The bot can post only in channels of servers it was invited to (with the
View Channel + Send Messages permissions) and can DM users who share a
server with it and allow DMs. See docs/integrations/discord.md.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

import httpx

from scar.core.errors import CapabilityUnavailable, ToolError
from scar.integrations.common import ApiClient, ServiceInfo
from scar.integrations.live_guard import ensure_live_send_allowed

SETUP_DOC = "docs/integrations/discord.md"
API_BASE = "https://discord.com/api/v10"
TOKEN_SECRET = "DISCORD_BOT_TOKEN"
WEBHOOK_SECRET = "DISCORD_WEBHOOK_URL"
NOT_CONFIGURED = (
    "DISCORD not configured: create a bot in the Discord Developer Portal, invite it to your server "
    "and store DISCORD_BOT_TOKEN (`scar config set-secret DISCORD_BOT_TOKEN`)"
)
MAX_CONTENT = 2000
USER_AGENT = "DiscordBot (https://github.com/scar-operator/scar, 1.0)"
WEBHOOK_HOSTS = {"discord.com", "discordapp.com", "ptb.discord.com", "canary.discord.com"}


def _snowflake(value: str, what: str) -> str:
    v = value.strip()
    if not v.isdigit() or not 15 <= len(v) <= 21:
        raise ToolError(f"{what} must be a numeric Discord id (snowflake), got {value!r}", "InvalidInput")
    return v


def _check_content(text: str) -> None:
    if not text.strip():
        raise ToolError("message text is empty", "InvalidInput")
    if len(text) > MAX_CONTENT:
        raise ToolError(f"Discord messages are limited to {MAX_CONTENT} characters", "TooLarge")


def validate_webhook_url(url: str) -> str:
    parsed = urlparse(url)
    if (
        parsed.scheme != "https"
        or (parsed.hostname or "").lower() not in WEBHOOK_HOSTS
        or not parsed.path.startswith("/api/webhooks/")
    ):
        raise CapabilityUnavailable("DISCORD_WEBHOOK_URL must be an https://discord.com/api/webhooks/... URL", SETUP_DOC)
    return url


class DiscordBot:
    def __init__(self, settings: Any, secrets: Any, http: httpx.AsyncClient | None = None) -> None:
        self.settings = settings
        self.secrets = secrets
        self._http = http

    def _api(self) -> ApiClient:
        token = self.secrets.get(TOKEN_SECRET)
        if token is None:
            raise CapabilityUnavailable(NOT_CONFIGURED, SETUP_DOC)
        value = token.get_secret_value()
        return ApiClient(
            ServiceInfo("Discord", SETUP_DOC, "check DISCORD_BOT_TOKEN and the bot's permissions"),
            auth_header=lambda: f"Bot {value}",
            http=self._http,
            base_url=API_BASE,
            default_headers={"User-Agent": USER_AGENT},
        )

    async def me(self) -> dict[str, Any]:
        return dict(await self._api().get_json("users/@me") or {})

    async def send_to_channel(self, channel_id: str, text: str) -> dict[str, Any]:
        _check_content(text)
        cid = _snowflake(channel_id, "channel id")
        api = self._api()
        ensure_live_send_allowed(self.settings, "discord", cid)
        body = await api.post_json(f"channels/{cid}/messages", {"content": text, "allowed_mentions": {"parse": []}}) or {}
        if not body.get("id"):
            raise ToolError("Discord accepted the request but returned no message id", "ProviderError")
        return {"message_id": str(body["id"]), "channel_id": str(body.get("channel_id", cid))}

    async def open_dm(self, user_id: str) -> str:
        uid = _snowflake(user_id, "user id")
        body = await self._api().post_json("users/@me/channels", {"recipient_id": uid}) or {}
        if not body.get("id"):
            raise ToolError("Discord did not return a DM channel", "ProviderError")
        return str(body["id"])

    async def send_dm(self, user_id: str, text: str) -> dict[str, Any]:
        _check_content(text)
        uid = _snowflake(user_id, "user id")
        self._api()
        ensure_live_send_allowed(self.settings, "discord", uid)
        channel = await self.open_dm(uid)
        body = (
            await self._api().post_json(f"channels/{channel}/messages", {"content": text, "allowed_mentions": {"parse": []}})
            or {}
        )
        if not body.get("id"):
            raise ToolError("Discord accepted the request but returned no message id", "ProviderError")
        return {"message_id": str(body["id"]), "channel_id": channel, "user_id": uid}

    async def recent(self, channel_id: str, limit: int = 20) -> list[dict[str, Any]]:
        cid = _snowflake(channel_id, "channel id")
        items = await self._api().get_json(f"channels/{cid}/messages", params={"limit": max(1, min(limit, 100))}) or []
        return [
            {
                "message_id": str(m.get("id")),
                "channel_id": cid,
                "author": (m.get("author") or {}).get("username", ""),
                "author_is_bot": bool((m.get("author") or {}).get("bot")),
                "date": m.get("timestamp"),
                "text": m.get("content", ""),
            }
            for m in items
        ]

    async def message_exists(self, channel_id: str, message_id: str) -> bool:
        resp = await self._api().request(
            "GET",
            f"channels/{_snowflake(channel_id, 'channel id')}/messages/{_snowflake(message_id, 'message id')}",
            expected=(200, 404),
        )
        return resp.status_code == 200

    async def send_webhook(self, text: str, url: str | None = None, username: str | None = None) -> dict[str, Any]:
        _check_content(text)
        target: str
        if url is None:
            secret = self.secrets.get(WEBHOOK_SECRET)
            if secret is None:
                raise CapabilityUnavailable("DISCORD webhook not configured: store DISCORD_WEBHOOK_URL", SETUP_DOC)
            target = str(secret.get_secret_value())
        else:
            target = url
        target = validate_webhook_url(target)
        ensure_live_send_allowed(self.settings, "discord", "webhook")
        api = ApiClient(
            ServiceInfo("Discord webhook", SETUP_DOC, "check DISCORD_WEBHOOK_URL"),
            http=self._http,
            default_headers={"User-Agent": USER_AGENT},
        )
        payload: dict[str, Any] = {"content": text, "allowed_mentions": {"parse": []}}
        if username:
            payload["username"] = username
        body = await api.post_json(target, payload, params={"wait": "true"}) or {}
        if not body.get("id"):
            raise ToolError("Discord webhook returned no message id", "ProviderError")
        return {"message_id": str(body["id"]), "channel_id": str(body.get("channel_id", ""))}
