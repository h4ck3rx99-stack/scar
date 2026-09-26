"""Telegram *user account* client via Telethon (MTProto). Optional and lazy.

Use this only when a bot cannot do the job (e.g. messaging a person who never
started your bot). It acts as your own Telegram account:

* needs an API id/hash from https://my.telegram.org -> "API development tools"
  (``TELEGRAM_API_ID`` via ``SCAR_TELEGRAM_API_ID`` setting or secret, and the
  ``TELEGRAM_API_HASH`` secret);
* the login (``scar auth telegram``) asks for your phone number, the login
  code Telegram sends you, and your 2FA password if set;
* the authorised session is stored at ``<data dir>/telethon/scar.session``.
  That file grants full access to your account: keep it private, and revoke it
  under Telegram -> Settings -> Devices if it leaks;
* Telegram's Terms of Service forbid spam/bulk messaging from user accounts;
  accounts that automate abusively get limited or banned.

Telethon is imported lazily so SCAR starts without it being used.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from scar.core.errors import CapabilityUnavailable, ToolError
from scar.integrations.live_guard import ensure_live_send_allowed

SETUP_DOC = "docs/integrations/telegram.md#user-account-optional"
NOT_CONFIGURED = (
    "TELEGRAM user client not configured: set TELEGRAM_API_ID and TELEGRAM_API_HASH from "
    "my.telegram.org, then run `scar auth telegram`"
)


class TelegramUserClient:
    def __init__(self, settings: Any, secrets: Any) -> None:
        self.settings = settings
        self.secrets = secrets
        self._client: Any = None

    @property
    def session_path(self) -> Path:
        return Path(self.settings.data_path) / "telethon" / "scar"  # Telethon appends ".session"

    def _credentials(self) -> tuple[int, str]:
        api_id = str(getattr(self.settings, "telegram_api_id", "") or "") or (self.secrets.get_plain("TELEGRAM_API_ID") or "")
        api_hash = self.secrets.get("TELEGRAM_API_HASH")
        if not api_id or api_hash is None:
            raise CapabilityUnavailable(NOT_CONFIGURED, SETUP_DOC)
        if not api_id.strip().isdigit():
            raise CapabilityUnavailable("TELEGRAM_API_ID must be the numeric api_id from my.telegram.org", SETUP_DOC)
        return int(api_id), api_hash.get_secret_value()

    def _make_client(self) -> Any:
        try:
            from telethon import TelegramClient
        except ImportError as exc:
            raise CapabilityUnavailable("Python package 'telethon' is not installed", SETUP_DOC) from exc
        api_id, api_hash = self._credentials()
        self.session_path.parent.mkdir(parents=True, exist_ok=True)
        return TelegramClient(str(self.session_path), api_id, api_hash, device_model="SCAR", app_version="1.0")

    async def _connected(self) -> Any:
        if self._client is None:
            self._client = self._make_client()
        if not self._client.is_connected():
            try:
                await self._client.connect()
            except OSError as exc:
                raise ToolError(f"cannot reach Telegram: {exc}", "NetworkError") from exc
        if not await self._client.is_user_authorized():
            raise CapabilityUnavailable("Telegram user session is not authorised: run `scar auth telegram`", SETUP_DOC)
        return self._client

    async def login(self, phone: Callable[[], str], code: Callable[[], str], password: Callable[[], str]) -> dict[str, Any]:
        """Interactive login; returns the account's basic identity."""
        client = self._client or self._make_client()
        self._client = client
        await client.start(phone=phone, code_callback=code, password=password)
        me = await client.get_me()
        return {"id": me.id, "username": me.username or "", "name": " ".join(filter(None, [me.first_name, me.last_name]))}

    async def send_message(self, recipient: str, text: str) -> dict[str, Any]:
        if not text.strip():
            raise ToolError("message text is empty", "InvalidInput")
        self._credentials()
        ensure_live_send_allowed(self.settings, "telegram_user", recipient)
        client = await self._connected()
        from telethon import errors as tl_errors

        entity: Any = int(recipient) if recipient.lstrip("-").isdigit() else recipient
        try:
            msg = await client.send_message(entity, text, link_preview=False)
        except tl_errors.FloodWaitError as exc:
            from scar.integrations.common import RateLimited

            raise RateLimited("Telegram", float(exc.seconds)) from exc
        except (ValueError, tl_errors.RPCError) as exc:
            raise ToolError(f"Telegram could not deliver to {recipient!r}: {exc}", "SendFailed") from exc
        return {"message_id": str(msg.id), "chat_id": str(msg.chat_id), "date": msg.date.isoformat() if msg.date else None}

    async def recent(self, chat: str, limit: int = 20) -> list[dict[str, Any]]:
        client = await self._connected()
        entity: Any = int(chat) if chat.lstrip("-").isdigit() else chat
        try:
            messages = await client.get_messages(entity, limit=limit)
        except ValueError as exc:
            raise ToolError(f"unknown Telegram chat {chat!r}: {exc}", "NotFound") from exc
        return [
            {
                "message_id": str(m.id),
                "chat_id": str(m.chat_id),
                "from_me": bool(m.out),
                "sender_id": str(m.sender_id),
                "date": m.date.isoformat() if m.date else None,
                "text": m.message or "",
            }
            for m in messages
        ]

    async def verify_message(self, chat_id: str, message_id: str) -> bool:
        client = await self._connected()
        entity: Any = int(chat_id) if chat_id.lstrip("-").isdigit() else chat_id
        found = await client.get_messages(entity, ids=int(message_id))
        return found is not None

    async def close(self) -> None:
        if self._client is not None:
            await self._client.disconnect()
