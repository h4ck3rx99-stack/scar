"""Gmail REST API v1 client: search, read, send (MIME multipart with attachments), drafts, SENT check."""

from __future__ import annotations

import asyncio
import base64
from typing import Any

import httpx

from scar.core.errors import ToolError
from scar.integrations.common import ApiClient, ServiceInfo, TokenSource
from scar.integrations.email_types import (
    AttachmentInfo,
    EmailAddress,
    EmailMessageData,
    EmailSummary,
    OutgoingEmail,
    SendReceipt,
    build_mime,
    html_to_text,
    reply_recipients,
)
from scar.integrations.google.auth import LOGIN_COMMAND, SETUP_DOC
from scar.integrations.live_guard import ensure_live_send_allowed

GMAIL_BASE = "https://gmail.googleapis.com/gmail/v1/users/me"
METADATA_HEADERS = ("From", "To", "Subject", "Date")


def b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii")


def b64url_decode(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def _headers(payload: dict[str, Any]) -> dict[str, str]:
    return {str(h.get("name", "")).lower(): str(h.get("value", "")) for h in payload.get("headers") or []}


def _walk_parts(part: dict[str, Any]) -> list[dict[str, Any]]:
    out = [part]
    for child in part.get("parts") or []:
        out.extend(_walk_parts(child))
    return out


def parse_gmail_message(raw: dict[str, Any]) -> EmailMessageData:
    payload = raw.get("payload") or {}
    hdr = _headers(payload)
    text_parts: list[str] = []
    html_parts: list[str] = []
    attachments: list[AttachmentInfo] = []
    for part in _walk_parts(payload):
        body = part.get("body") or {}
        filename = part.get("filename") or ""
        mime = str(part.get("mimeType") or "")
        if filename or body.get("attachmentId"):
            attachments.append(
                AttachmentInfo(
                    filename=filename or "attachment",
                    size=int(body.get("size") or 0),
                    content_type=mime,
                    attachment_id=str(body.get("attachmentId") or ""),
                )
            )
            continue
        data = body.get("data")
        if not data:
            continue
        text = b64url_decode(str(data)).decode("utf-8", errors="replace")
        if mime == "text/plain":
            text_parts.append(text)
        elif mime == "text/html":
            html_parts.append(text)
    body_text = "\n".join(text_parts).strip() or html_to_text("\n".join(html_parts))
    senders = EmailAddress.parse_list(hdr.get("from"))
    return EmailMessageData(
        id=str(raw.get("id", "")),
        provider="gmail",
        thread_id=str(raw.get("threadId", "")),
        subject=hdr.get("subject", ""),
        sender=senders[0] if senders else None,
        to=EmailAddress.parse_list(hdr.get("to")),
        cc=EmailAddress.parse_list(hdr.get("cc")),
        reply_to=EmailAddress.parse_list(hdr.get("reply-to")),
        date=hdr.get("date", ""),
        snippet=str(raw.get("snippet", "")),
        unread="UNREAD" in (raw.get("labelIds") or []),
        body_text=body_text,
        attachments=attachments,
        message_id_header=hdr.get("message-id", ""),
        references=hdr.get("references", ""),
    )


def parse_gmail_summary(raw: dict[str, Any]) -> EmailSummary:
    hdr = _headers(raw.get("payload") or {})
    senders = EmailAddress.parse_list(hdr.get("from"))
    return EmailSummary(
        id=str(raw.get("id", "")),
        provider="gmail",
        thread_id=str(raw.get("threadId", "")),
        subject=hdr.get("subject", ""),
        sender=senders[0] if senders else None,
        to=EmailAddress.parse_list(hdr.get("to")),
        date=hdr.get("date", ""),
        snippet=str(raw.get("snippet", "")),
        unread="UNREAD" in (raw.get("labelIds") or []),
    )


class GmailClient:
    name = "gmail"

    def __init__(self, tokens: TokenSource, settings: Any, http: httpx.AsyncClient | None = None) -> None:
        self.settings = settings
        self.api = ApiClient(
            ServiceInfo("GMAIL", SETUP_DOC, f"run `{LOGIN_COMMAND}`"), tokens=tokens, http=http, base_url=GMAIL_BASE
        )
        self._profile_email: str | None = None

    async def profile_email(self) -> str:
        if self._profile_email is None:
            body = await self.api.get_json("profile")
            self._profile_email = str((body or {}).get("emailAddress", ""))
        return self._profile_email

    async def search(self, query: str, max_results: int = 20) -> list[EmailSummary]:
        params: dict[str, Any] = {"maxResults": max(1, min(max_results, 100))}
        if query:
            params["q"] = query
        body = await self.api.get_json("messages", params=params) or {}
        ids = [str(m["id"]) for m in body.get("messages") or [] if m.get("id")]
        sem = asyncio.Semaphore(8)

        async def one(mid: str) -> EmailSummary:
            async with sem:
                raw = await self.api.get_json(
                    f"messages/{mid}", params=[("format", "metadata"), *[("metadataHeaders", h) for h in METADATA_HEADERS]]
                )
            return parse_gmail_summary(raw or {})

        return list(await asyncio.gather(*(one(i) for i in ids)))

    async def read(self, message_id: str) -> EmailMessageData:
        raw = await self.api.get_json(f"messages/{message_id}", params={"format": "full"})
        return parse_gmail_message(raw or {})

    async def _send_raw(self, msg: OutgoingEmail) -> SendReceipt:
        ensure_live_send_allowed(self.settings, "email", msg.all_recipients)
        mime = build_mime(msg)
        body: dict[str, Any] = {"raw": b64url_encode(mime.as_bytes())}
        if msg.thread_id:
            body["threadId"] = msg.thread_id
        resp = await self.api.post_json("messages/send", body) or {}
        mid = str(resp.get("id") or "")
        if not mid:
            raise ToolError("Gmail accepted the request but returned no message id", "ProviderError")
        return SendReceipt(
            provider=self.name,
            message_id=mid,
            thread_id=str(resp.get("threadId") or ""),
            recipients=msg.all_recipients,
            detail=",".join(resp.get("labelIds") or []),
        )

    async def send(self, msg: OutgoingEmail) -> SendReceipt:
        return await self._send_raw(msg)

    async def reply(self, original: EmailMessageData, msg: OutgoingEmail) -> SendReceipt:
        msg.thread_id = msg.thread_id or original.thread_id
        msg.in_reply_to = msg.in_reply_to or original.message_id_header
        msg.references = msg.references or original.references
        return await self._send_raw(msg)

    async def reply_targets(self, original: EmailMessageData, reply_all: bool) -> tuple[list[EmailAddress], list[EmailAddress]]:
        return reply_recipients(original, {await self.profile_email()}, reply_all)

    async def create_draft(self, msg: OutgoingEmail) -> str:
        mime = build_mime(msg)
        message: dict[str, Any] = {"raw": b64url_encode(mime.as_bytes())}
        if msg.thread_id:
            message["threadId"] = msg.thread_id
        resp = await self.api.post_json("drafts", {"message": message}) or {}
        draft_id = str(resp.get("id") or "")
        if not draft_id:
            raise ToolError("Gmail returned no draft id", "ProviderError")
        return draft_id

    async def verify_sent(self, receipt: SendReceipt) -> bool | None:
        raw = await self.api.get_json(f"messages/{receipt.message_id}", params={"format": "minimal"}) or {}
        return "SENT" in (raw.get("labelIds") or [])
