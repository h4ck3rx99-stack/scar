"""Messaging tools: send a message (Telegram / Discord / WhatsApp) and read recent messages."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from scar.core.types import RiskLevel, SideEffect, ToolResult, TrustLevel, VerificationResult
from scar.security.risk import RiskAssessment
from scar.tools.base import Tool, ToolContext, ToolInput
from scar.tools.contacts.resolver import get_resolver

Channel = Literal["telegram", "telegram_user", "discord", "whatsapp"]
TEXT_PREVIEW = 2000


def messaging_service(services: Any) -> Any:
    svc = getattr(services, "messaging", None)
    if svc is None:
        from scar.integrations.messaging import MessagingService

        svc = MessagingService(services.settings, services.secrets)
        services.messaging = svc
    return svc


class SendInput(ToolInput):
    channel: Channel = Field(description="telegram (bot), telegram_user (your account), discord (bot), whatsapp")
    recipient: str = Field(
        description="telegram: chat id or @username; discord: channel:<id>, user:<id>, webhook "
        "or empty for the default channel; whatsapp: +<country><number>"
    )
    text: str = Field(min_length=1, max_length=4096)


class MessageSend(Tool):
    name = "message.send"
    description = (
        "Send a chat message on Telegram, Discord or WhatsApp. Resolve people with contacts.resolve "
        "first. Always requires the user's approval."
    )
    input_model = SendInput
    capabilities = ("comms.send.message",)
    base_risk = RiskLevel.HIGH
    side_effects = SideEffect.EXTERNAL
    sensitive_args = {"recipient": "recipient", "text": "body"}
    data_class = "messages"
    categories = ("messaging",)
    timeout = 90.0

    def _identity(self, args: SendInput) -> tuple[str, str]:
        """(recipient label, resolved contact name or '')."""
        if self.services is None:
            return args.recipient, ""
        target = args.recipient.split(":", 1)[1] if args.recipient.startswith(("user:", "channel:")) else args.recipient
        contact = get_resolver(self.services).lookup_handle(target, args.channel) if target else None
        name = contact.name if contact is not None else ""
        return (f"{name} ({args.recipient})" if name else args.recipient or "(default channel)"), name

    def assess(self, args: SendInput, ctx: ToolContext) -> RiskAssessment:
        a = RiskAssessment(RiskLevel.HIGH, [f"sends a {args.channel} message to someone outside this computer"])
        a.facts.external_destination = True
        recipient = args.recipient.strip()
        if args.channel == "discord" and not recipient:
            recipient = f"channel:{getattr(ctx.services.settings, 'discord_default_channel', '')}"
        a.facts.recipients.append(recipient)
        _, name = self._identity(args)
        if name:
            a.facts.recipient_names.append(name)
        a.facts.data_classes.append("messages")
        if args.channel == "whatsapp" and not getattr(ctx.services.settings, "whatsapp_phone_number_id", ""):
            a.reasons.append("uses UI automation of WhatsApp Desktop")
        return a

    def describe(self, args: SendInput) -> str:
        label, _ = self._identity(args)
        return f"send {args.channel} message to {label}: {args.text[:80]!r}"

    def approval_details(self, args: SendInput) -> dict[str, Any]:
        label, name = self._identity(args)
        text = args.text if len(args.text) <= TEXT_PREVIEW else args.text[:TEXT_PREVIEW] + "…"
        return {"channel": args.channel, "recipient": label, "recipient_name": name, "text": text, "text_chars": len(args.text)}

    def progress_line(self, args: SendInput) -> str | None:
        return f"Sending the {args.channel.replace('_user', '')} message."

    async def run(self, args: SendInput, ctx: ToolContext) -> ToolResult:
        result = await messaging_service(ctx.services).send(args.channel, args.recipient, args.text)
        label, _ = self._identity(args)
        mid = result.get("message_id")
        summary = f"Sent {args.channel} message to {label}" + (f" (message id {mid})" if mid else "")
        return self.ok(summary, result)

    async def verify(self, args: SendInput, result: ToolResult, ctx: ToolContext) -> VerificationResult:
        return await messaging_service(ctx.services).verify(result.data)


class RecentInput(ToolInput):
    channel: Channel
    chat: str = Field("", description="chat/channel to read (telegram: chat id or @name; discord: channel:<id> or user:<id>)")
    limit: int = Field(20, ge=1, le=100)


class MessageRecent(Tool):
    name = "message.recent"
    description = "Read recent messages from a Telegram or Discord chat (untrusted content)."
    input_model = RecentInput
    capabilities = ("comms.read.message",)
    output_trust = TrustLevel.UNTRUSTED_EXTERNAL
    data_class = "messages"
    categories = ("messaging",)

    def describe(self, args: RecentInput) -> str:
        return f"read recent {args.channel} messages{' in ' + args.chat if args.chat else ''}"

    async def run(self, args: RecentInput, ctx: ToolContext) -> ToolResult:
        items = await messaging_service(ctx.services).recent(args.channel, args.chat, args.limit)
        lines = [
            f"- [{m.get('message_id')}] {m.get('date')} {m.get('from') or m.get('author') or m.get('sender_id', '')}: "
            f"{str(m.get('text', ''))[:300]}"
            for m in items
        ]
        return self.ok(
            f"{len(items)} recent {args.channel} messages",
            {"messages": items, "count": len(items)},
            model_view="\n".join(lines) or "No messages.",
            source=f"{args.channel}:{args.chat or 'updates'}",
        )


TOOLS: list[type[Tool]] = [MessageSend, MessageRecent]
