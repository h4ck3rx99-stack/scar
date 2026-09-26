"""Telegram Bot API over httpx: getMe, sendMessage, getUpdates.

A bot can only message chats that have started a conversation with it (or
groups/channels it was added to). ``getUpdates`` does not work while a
webhook is set on the bot. See docs/integrations/telegram.md.
"""

from __future__ import annotations

from typing import Any

import httpx

from scar.core.errors import CapabilityUnavailable, ToolError
from scar.integrations.common import (
    ApiClient,
    IntegrationAuthError,
    ProviderError,
    RateLimited,
    ServiceInfo,
    parse_retry_after,
    safe_json,
)
from scar.integrations.live_guard import ensure_live_send_allowed

SETUP_DOC = "docs/integrations/telegram.md"
API_BASE = "https://api.telegram.org"
TOKEN_SECRET = "TELEGRAM_BOT_TOKEN"
NOT_CONFIGURED = (
    "TELEGRAM bot not configured: create a bot with @BotFather and store TELEGRAM_BOT_TOKEN "
    "(`scar config set-secret TELEGRAM_BOT_TOKEN`)"
)
MAX_TEXT = 4096


class TelegramBot:
    def __init__(self, settings: Any, secrets: Any, http: httpx.AsyncClient | None = None) -> None:
        self.settings = settings
        self.secrets = secrets
        self._http = http

    def _api(self) -> ApiClient:
        token = self.secrets.get(TOKEN_SECRET)
        if token is None:
            raise CapabilityUnavailable(NOT_CONFIGURED, SETUP_DOC)
        # the credential is part of the URL path (Bot API design); the redactor knows the token value
        return ApiClient(
            ServiceInfo("Telegram Bot API", SETUP_DOC, "check TELEGRAM_BOT_TOKEN"),
            http=self._http,
            base_url=f"{API_BASE}/bot{token.get_secret_value()}",
        )

    async def _call(self, method: str, payload: dict[str, Any] | None = None) -> Any:
        api = self._api()
        resp = await api.request("POST", method, json=payload or {}, expected=tuple(range(200, 600)))
        body = safe_json(resp)
        if isinstance(body, dict) and body.get("ok") is True:
            return body.get("result")
        code = int((body or {}).get("error_code") or resp.status_code) if isinstance(body, dict) else resp.status_code
        desc = str((body or {}).get("description") or resp.text[:200]) if isinstance(body, dict) else resp.text[:200]
        if code == 401 or (code == 404 and desc.strip().lower() == "not found"):  # malformed token -> 404
            raise IntegrationAuthError(
                "TELEGRAM bot token was rejected: create/revoke it with @BotFather and store TELEGRAM_BOT_TOKEN again",
                SETUP_DOC,
                desc,
                code,
            )
        if code == 429:
            raise RateLimited("Telegram Bot API", parse_retry_after(resp.headers.get("Retry-After"), body), desc)
        if code == 403:
            raise ToolError(
                f"Telegram refused: {desc} (the user must start a chat with the bot first, or the bot was removed from the chat)",
                "Forbidden",
            )
        raise ProviderError("Telegram Bot API", code, desc, "NotFound" if "not found" in desc.lower() else "ProviderError")

    async def get_me(self) -> dict[str, Any]:
        result = await self._call("getMe")
        return dict(result or {})

    async def send_message(self, chat_id: str, text: str) -> dict[str, Any]:
        if not text.strip():
            raise ToolError("message text is empty", "InvalidInput")
        if len(text) > MAX_TEXT:
            raise ToolError(f"Telegram messages are limited to {MAX_TEXT} characters", "TooLarge")
        self._api()  # configuration first, so a missing token is reported as the prerequisite
        ensure_live_send_allowed(self.settings, "telegram", str(chat_id))
        result = await self._call("sendMessage", {"chat_id": _chat_ref(chat_id), "text": text, "disable_web_page_preview": True})
        if not isinstance(result, dict) or "message_id" not in result:
            raise ToolError("Telegram accepted the request but returned no message_id", "ProviderError")
        chat = result.get("chat") or {}
        return {
            "message_id": str(result["message_id"]),
            "chat_id": str(chat.get("id", chat_id)),
            "date": result.get("date"),
            "text": result.get("text", ""),
        }

    async def get_updates(self, limit: int = 50, offset: int | None = None) -> list[dict[str, Any]]:
        payload: dict[str, Any] = {"limit": max(1, min(limit, 100)), "timeout": 0, "allowed_updates": ["message", "channel_post"]}
        if offset is not None:
            payload["offset"] = offset
        result = await self._call("getUpdates", payload)
        return list(result or [])

    async def recent(self, chat: str | None, limit: int = 20) -> list[dict[str, Any]]:
        """Recent messages the bot received (optionally only from one chat id / @username)."""
        out: list[dict[str, Any]] = []
        for upd in await self.get_updates(100):
            msg = upd.get("message") or upd.get("channel_post")
            if not isinstance(msg, dict):
                continue
            c = msg.get("chat") or {}
            if chat and str(c.get("id")) != str(chat) and ("@" + str(c.get("username") or "")).lower() != chat.lower():
                continue
            sender = msg.get("from") or {}
            out.append(
                {
                    "message_id": str(msg.get("message_id")),
                    "chat_id": str(c.get("id")),
                    "chat": c.get("title") or c.get("username") or c.get("first_name") or "",
                    "from": sender.get("username") or sender.get("first_name") or "",
                    "date": msg.get("date"),
                    "text": msg.get("text") or msg.get("caption") or "",
                }
            )
        return out[-limit:]


def _chat_ref(chat_id: str) -> int | str:
    value = str(chat_id).strip()
    if value.lstrip("-").isdigit():
        return int(value)
    return value if value.startswith("@") else f"@{value}"
