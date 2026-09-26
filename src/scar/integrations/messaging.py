"""MessagingService: one facade over Telegram (bot / user), Discord (bot / webhook) and WhatsApp.

Channels and recipient formats:

==============  ==============================================================
telegram        chat id (``123456789`` / ``-100...``) or ``@channelusername``
telegram_user   phone ``+15551234567``, ``@username`` or numeric id (Telethon)
discord         ``channel:<id>`` or ``<id>`` (channel), ``user:<id>`` (DM),
                ``webhook`` (DISCORD_WEBHOOK_URL); empty -> default channel
whatsapp        international phone number; Cloud API when configured,
                otherwise the opt-in desktop provider
==============  ==============================================================
"""

from __future__ import annotations

from typing import Any

import httpx

from scar.core.errors import CapabilityUnavailable, ToolError
from scar.core.types import Check, VerificationResult

CHANNELS = ("telegram", "telegram_user", "discord", "whatsapp")


class MessagingService:
    def __init__(self, settings: Any, secrets: Any, *, http: httpx.AsyncClient | None = None) -> None:
        from scar.integrations.discord.bot import DiscordBot
        from scar.integrations.telegram.bot import TelegramBot
        from scar.integrations.telegram.user_client import TelegramUserClient
        from scar.integrations.whatsapp.cloud_api import WhatsAppCloud
        from scar.integrations.whatsapp.desktop import WhatsAppDesktop

        self.settings = settings
        self.secrets = secrets
        self.telegram = TelegramBot(settings, secrets, http)
        self.telegram_user = TelegramUserClient(settings, secrets)
        self.discord = DiscordBot(settings, secrets, http)
        self.whatsapp_cloud = WhatsAppCloud(settings, secrets, http)
        self.whatsapp_desktop = WhatsAppDesktop(settings)

    @staticmethod
    def _check_channel(channel: str) -> str:
        ch = channel.strip().lower()
        if ch not in CHANNELS:
            raise ToolError(f"unknown channel {channel!r}; use one of {', '.join(CHANNELS)}", "InvalidInput")
        return ch

    def discord_target(self, recipient: str) -> tuple[str, str]:
        """(kind, id) where kind is channel | user | webhook."""
        r = recipient.strip()
        if not r:
            default = str(getattr(self.settings, "discord_default_channel", "") or "")
            if not default:
                raise ToolError("no Discord recipient given and SCAR_DISCORD_DEFAULT_CHANNEL is not set", "InvalidInput")
            return "channel", default
        if r.lower() == "webhook":
            return "webhook", ""
        kind, sep, ident = r.partition(":")
        if sep and kind.lower() in ("channel", "user"):
            return kind.lower(), ident.strip()
        return "channel", r

    def whatsapp_provider(self) -> str:
        if self.whatsapp_cloud.configured():
            return "cloud_api"
        if self.whatsapp_desktop.enabled():
            return "desktop"
        raise CapabilityUnavailable(
            "WHATSAPP not configured: set SCAR_WHATSAPP_PHONE_NUMBER_ID + WHATSAPP_CLOUD_TOKEN "
            "(Cloud API) or opt in to the desktop provider with "
            "SCAR_WHATSAPP_DESKTOP_ENABLED=true",
            "docs/integrations/whatsapp.md",
        )

    async def send(self, channel: str, recipient: str, text: str) -> dict[str, Any]:
        ch = self._check_channel(channel)
        result: dict[str, Any]
        if ch == "telegram":
            if not recipient.strip():
                raise ToolError("a Telegram chat id or @username is required", "InvalidInput")
            result = await self.telegram.send_message(recipient.strip(), text)
            result["provider"] = "bot"
        elif ch == "telegram_user":
            result = await self.telegram_user.send_message(recipient.strip(), text)
            result["provider"] = "user"
        elif ch == "discord":
            kind, ident = self.discord_target(recipient)
            if kind == "user":
                result = await self.discord.send_dm(ident, text)
            elif kind == "webhook":
                result = await self.discord.send_webhook(text)
            else:
                result = await self.discord.send_to_channel(ident, text)
            result["provider"] = kind
        else:
            if self.whatsapp_provider() == "cloud_api":
                result = await self.whatsapp_cloud.send_text(recipient, text)
            else:
                result = await self.whatsapp_desktop.send_text(recipient, text)
        result["channel"] = ch
        result["recipient"] = recipient
        return result

    async def recent(self, channel: str, chat: str = "", limit: int = 20) -> list[dict[str, Any]]:
        ch = self._check_channel(channel)
        if ch == "telegram":
            return await self.telegram.recent(chat or None, limit)
        if ch == "telegram_user":
            if not chat:
                raise ToolError("telegram_user needs a chat (@username, phone or id)", "InvalidInput")
            return await self.telegram_user.recent(chat, limit)
        if ch == "discord":
            kind, ident = self.discord_target(chat)
            if kind == "webhook":
                raise ToolError("webhooks cannot read messages; give a channel id", "InvalidInput")
            if kind == "user":
                ident = await self.discord.open_dm(ident)
            return await self.discord.recent(ident, limit)
        raise CapabilityUnavailable(
            "WhatsApp message history is not available: the Cloud API only delivers incoming "
            "messages to a webhook, and the desktop provider is send-only",
            "docs/integrations/whatsapp.md",
        )

    async def verify(self, result: dict[str, Any]) -> VerificationResult:
        channel = str(result.get("channel", ""))
        mid = result.get("message_id")
        evidence = {k: v for k, v in result.items() if k in ("channel", "provider", "message_id", "chat_id", "to")}
        if channel == "whatsapp" and result.get("provider") == "desktop":
            verified = result.get("verified")
            note = str(result.get("verification_note") or "")
            if verified is None:
                return VerificationResult(verified=None, evidence=evidence, note=note or "sent but couldn't confirm")
            return VerificationResult.from_checks(
                [Check(name="message visible in WhatsApp chat", passed=bool(verified), detail=note)], evidence
            )
        checks = [Check(name="provider returned a message id", passed=bool(mid), detail=str(mid or ""))]
        if mid and channel == "discord" and result.get("provider") in ("channel", "user") and result.get("channel_id"):
            checks.append(
                Check(
                    name="message readable in channel",
                    passed=await self.discord.message_exists(str(result["channel_id"]), str(mid)),
                )
            )
        if mid and channel == "telegram_user" and result.get("chat_id"):
            checks.append(
                Check(
                    name="message present in chat",
                    passed=await self.telegram_user.verify_message(str(result["chat_id"]), str(mid)),
                )
            )
        return VerificationResult.from_checks(checks, evidence)
