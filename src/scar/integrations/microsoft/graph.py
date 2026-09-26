"""Microsoft Graph v1.0: Outlook mail (search/read/send with attachments, verify via Sent Items),
calendar events CRUD and contacts search."""

from __future__ import annotations

import asyncio
import base64
import re
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import quote

import httpx

from scar.core.errors import ToolError
from scar.integrations.common import ApiClient, ServiceInfo, TokenSource, safe_json
from scar.integrations.email_types import (
    AttachmentInfo,
    EmailAddress,
    EmailMessageData,
    EmailSummary,
    OutgoingEmail,
    SendReceipt,
    attachment_content_type,
    check_attachments,
    html_to_text,
    reply_recipients,
)
from scar.integrations.ics import Attendee, CalendarEvent, resolve_tz
from scar.integrations.live_guard import ensure_live_send_allowed
from scar.integrations.microsoft.auth import LOGIN_COMMAND, SETUP_DOC

GRAPH_BASE = "https://graph.microsoft.com/v1.0"
INLINE_ATTACHMENT_MAX = 3 * 1024 * 1024  # larger files need an upload session
UPLOAD_CHUNK = 320 * 1024 * 10  # multiple of 320 KiB as Graph requires
SUMMARY_SELECT = "id,subject,from,toRecipients,receivedDateTime,bodyPreview,conversationId,isRead"
MESSAGE_SELECT = SUMMARY_SELECT + ",ccRecipients,replyTo,body,internetMessageId,hasAttachments"
VERIFY_DELAYS = (0.0, 1.5, 3.0)


def _addr(obj: dict[str, Any] | None) -> EmailAddress | None:
    ea = (obj or {}).get("emailAddress") or {}
    if not ea.get("address"):
        return None
    return EmailAddress(address=str(ea["address"]), name=str(ea.get("name") or ""))


def _addrs(items: list[dict[str, Any]] | None) -> list[EmailAddress]:
    return [a for a in (_addr(i) for i in items or []) if a is not None]


def _recipients(addrs: list[EmailAddress]) -> list[dict[str, Any]]:
    return [{"emailAddress": {"address": a.address, **({"name": a.name} if a.name else {})}} for a in addrs]


def _odata_quote(value: str) -> str:
    return value.replace("'", "''")


def parse_graph_datetime(obj: dict[str, Any]) -> datetime:
    raw = str(obj.get("dateTime") or "")
    m = re.match(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(\.\d+)?", raw)
    if not m:
        raise ToolError(f"unexpected Graph dateTime {raw!r}", "ProviderError")
    frac = (m.group(2) or "")[:7]  # "." + up to 6 digits
    dt = datetime.fromisoformat(m.group(1) + (frac if len(frac) > 1 else ""))
    tzname = str(obj.get("timeZone") or "UTC")
    if tzname.upper() in ("UTC", "ETC/UTC", "COORDINATED UNIVERSAL TIME"):
        return dt.replace(tzinfo=UTC)
    tz = resolve_tz(tzname)
    return dt.replace(tzinfo=tz or UTC)


def event_from_graph(raw: dict[str, Any]) -> CalendarEvent:
    reminder = (
        int(raw["reminderMinutesBeforeStart"])
        if raw.get("isReminderOn") and raw.get("reminderMinutesBeforeStart") is not None
        else None
    )
    body = raw.get("body") or {}
    content = str(body.get("content") or "")
    if str(body.get("contentType") or "").lower() == "html":
        content = html_to_text(content)
    return CalendarEvent(
        title=str(raw.get("subject") or "(no title)"),
        start=parse_graph_datetime(raw.get("start") or {}),
        end=parse_graph_datetime(raw.get("end") or {}),
        location=str((raw.get("location") or {}).get("displayName") or ""),
        description=content,
        attendees=[Attendee(address=a.address, name=a.name) for a in _addrs(raw.get("attendees"))],
        reminder_minutes=reminder,
        uid=str(raw.get("iCalUId") or ""),
        event_id=str(raw.get("id") or ""),
        provider="outlook",
        all_day=bool(raw.get("isAllDay")),
        link=str(raw.get("webLink") or ""),
    )


def event_to_graph(ev: CalendarEvent) -> dict[str, Any]:
    def when(dt: datetime) -> dict[str, str]:
        if ev.all_day:
            return {"dateTime": dt.strftime("%Y-%m-%dT00:00:00"), "timeZone": "UTC"}
        return {"dateTime": dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S"), "timeZone": "UTC"}

    end = ev.end if not ev.all_day or ev.end.date() > ev.start.date() else ev.start + timedelta(days=1)
    body: dict[str, Any] = {
        "subject": ev.title,
        "body": {"contentType": "Text", "content": ev.description},
        "start": when(ev.start),
        "end": when(end),
        "location": {"displayName": ev.location},
        "isAllDay": ev.all_day,
        "attendees": [
            {"emailAddress": {"address": a.address, **({"name": a.name} if a.name else {})}, "type": "required"}
            for a in ev.attendees
        ],
    }
    if ev.reminder_minutes is not None:
        body["isReminderOn"] = True
        body["reminderMinutesBeforeStart"] = ev.reminder_minutes
    return body


class GraphClient:
    name = "outlook"

    def __init__(
        self,
        tokens: TokenSource,
        settings: Any,
        http: httpx.AsyncClient | None = None,
        verify_delays: tuple[float, ...] = VERIFY_DELAYS,
    ) -> None:
        self.settings = settings
        self.api = ApiClient(
            ServiceInfo("Microsoft Graph", SETUP_DOC, f"run `{LOGIN_COMMAND}`"), tokens=tokens, http=http, base_url=GRAPH_BASE
        )
        self._http = http
        self._me: str | None = None
        self.verify_delays = verify_delays

    # ------------------------------------------------------------ identity
    async def me_address(self) -> str:
        if self._me is None:
            body = await self.api.get_json("me", params={"$select": "mail,userPrincipalName"}) or {}
            self._me = str(body.get("mail") or body.get("userPrincipalName") or "")
        return self._me

    # ------------------------------------------------------------ mail
    async def search(self, query: str, max_results: int = 20) -> list[EmailSummary]:
        params: dict[str, Any] = {"$top": max(1, min(max_results, 100)), "$select": SUMMARY_SELECT}
        if query:
            params["$search"] = '"' + query.replace('"', "") + '"'
        else:
            params["$orderby"] = "receivedDateTime desc"
        body = await self.api.get_json("me/messages", params=params) or {}
        out: list[EmailSummary] = []
        for m in body.get("value") or []:
            out.append(
                EmailSummary(
                    id=str(m.get("id")),
                    provider=self.name,
                    subject=str(m.get("subject") or ""),
                    sender=_addr(m.get("from")),
                    to=_addrs(m.get("toRecipients")),
                    date=str(m.get("receivedDateTime") or ""),
                    snippet=str(m.get("bodyPreview") or ""),
                    thread_id=str(m.get("conversationId") or ""),
                    unread=not m.get("isRead", True),
                )
            )
        return out

    async def read(self, message_id: str) -> EmailMessageData:
        mid = quote(message_id, safe="")
        m = (
            await self.api.get_json(
                f"me/messages/{mid}", params={"$select": MESSAGE_SELECT}, headers={"Prefer": 'outlook.body-content-type="text"'}
            )
            or {}
        )
        attachments: list[AttachmentInfo] = []
        if m.get("hasAttachments"):
            att = await self.api.get_json(f"me/messages/{mid}/attachments", params={"$select": "id,name,size,contentType"}) or {}
            attachments = [
                AttachmentInfo(
                    filename=str(a.get("name") or "attachment"),
                    size=int(a.get("size") or 0),
                    content_type=str(a.get("contentType") or ""),
                    attachment_id=str(a.get("id")),
                )
                for a in att.get("value") or []
            ]
        body = m.get("body") or {}
        text = str(body.get("content") or "")
        if str(body.get("contentType") or "").lower() == "html":
            text = html_to_text(text)
        return EmailMessageData(
            id=str(m.get("id") or message_id),
            provider=self.name,
            subject=str(m.get("subject") or ""),
            sender=_addr(m.get("from")),
            to=_addrs(m.get("toRecipients")),
            cc=_addrs(m.get("ccRecipients")),
            reply_to=_addrs(m.get("replyTo")),
            date=str(m.get("receivedDateTime") or ""),
            snippet=str(m.get("bodyPreview") or ""),
            thread_id=str(m.get("conversationId") or ""),
            unread=not m.get("isRead", True),
            body_text=text.strip(),
            attachments=attachments,
            message_id_header=str(m.get("internetMessageId") or ""),
        )

    def _message_body(self, msg: OutgoingEmail, *, inline_attachments: bool) -> dict[str, Any]:
        body: dict[str, Any] = {
            "subject": msg.subject,
            "body": {"contentType": "Text", "content": msg.body},
            "toRecipients": _recipients(msg.to),
        }
        if msg.cc:
            body["ccRecipients"] = _recipients(msg.cc)
        if msg.bcc:
            body["bccRecipients"] = _recipients(msg.bcc)
        if inline_attachments:
            small = [p for p in msg.attachments if p.stat().st_size <= INLINE_ATTACHMENT_MAX]
            if small:
                body["attachments"] = [self._file_attachment(p) for p in small]
        return body

    @staticmethod
    def _file_attachment(path: Any) -> dict[str, Any]:
        maintype, subtype = attachment_content_type(path)
        return {
            "@odata.type": "#microsoft.graph.fileAttachment",
            "name": path.name,
            "contentType": f"{maintype}/{subtype}",
            "contentBytes": base64.b64encode(path.read_bytes()).decode("ascii"),
        }

    async def _upload_large(self, message_id: str, msg: OutgoingEmail) -> None:
        mid = quote(message_id, safe="")
        for path in msg.attachments:
            size = path.stat().st_size
            if size <= INLINE_ATTACHMENT_MAX:
                continue
            session = (
                await self.api.post_json(
                    f"me/messages/{mid}/attachments/createUploadSession",
                    {"AttachmentItem": {"attachmentType": "file", "name": path.name, "size": size}},
                )
                or {}
            )
            upload_url = str(session.get("uploadUrl") or "")
            if not upload_url:
                raise ToolError("Graph did not return an upload URL for a large attachment", "ProviderError")
            client = self._http or httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=10.0))
            try:
                with path.open("rb") as fh:
                    offset = 0
                    while offset < size:
                        chunk = fh.read(UPLOAD_CHUNK)
                        end = offset + len(chunk) - 1
                        # the upload URL is pre-authenticated: no Authorization header allowed
                        resp = await client.put(
                            upload_url,
                            content=chunk,
                            headers={"Content-Length": str(len(chunk)), "Content-Range": f"bytes {offset}-{end}/{size}"},
                        )
                        if resp.status_code not in (200, 201, 202):
                            self.api.raise_for(resp)
                        offset = end + 1
            finally:
                if self._http is None:
                    await client.aclose()

    async def create_draft(self, msg: OutgoingEmail) -> str:
        check_attachments(msg.attachments)
        draft = await self.api.post_json("me/messages", self._message_body(msg, inline_attachments=True)) or {}
        draft_id = str(draft.get("id") or "")
        if not draft_id:
            raise ToolError("Graph returned no draft id", "ProviderError")
        await self._upload_large(draft_id, msg)
        return draft_id

    async def send(self, msg: OutgoingEmail) -> SendReceipt:
        ensure_live_send_allowed(self.settings, "email", msg.all_recipients)
        check_attachments(msg.attachments)
        draft = await self.api.post_json("me/messages", self._message_body(msg, inline_attachments=True)) or {}
        draft_id = str(draft.get("id") or "")
        if not draft_id:
            raise ToolError("Graph returned no message id for the outgoing draft", "ProviderError")
        await self._upload_large(draft_id, msg)
        await self.api.request("POST", f"me/messages/{quote(draft_id, safe='')}/send", expected=(202, 204))
        return SendReceipt(
            provider=self.name,
            message_id=draft_id,
            recipients=msg.all_recipients,
            thread_id=str(draft.get("conversationId") or ""),
            internet_message_id=str(draft.get("internetMessageId") or ""),
        )

    async def reply_targets(self, original: EmailMessageData, reply_all: bool) -> tuple[list[EmailAddress], list[EmailAddress]]:
        return reply_recipients(original, {await self.me_address()}, reply_all)

    async def reply(self, original: EmailMessageData, msg: OutgoingEmail) -> SendReceipt:
        ensure_live_send_allowed(self.settings, "email", msg.all_recipients)
        check_attachments(msg.attachments)
        mid = quote(original.id, safe="")
        action = "createReplyAll" if msg.cc else "createReply"
        draft = await self.api.post_json(f"me/messages/{mid}/{action}", {"comment": msg.body}) or {}
        draft_id = str(draft.get("id") or "")
        if not draft_id:
            raise ToolError("Graph returned no reply draft id", "ProviderError")
        did = quote(draft_id, safe="")
        # pin the recipients the user approved (the reply draft may otherwise differ)
        await self.api.request(
            "PATCH",
            f"me/messages/{did}",
            json={
                "toRecipients": _recipients(msg.to),
                "ccRecipients": _recipients(msg.cc),
                **({"bccRecipients": _recipients(msg.bcc)} if msg.bcc else {}),
            },
        )
        for path in msg.attachments:
            if path.stat().st_size <= INLINE_ATTACHMENT_MAX:
                await self.api.post_json(f"me/messages/{did}/attachments", self._file_attachment(path))
        await self._upload_large(draft_id, msg)
        await self.api.request("POST", f"me/messages/{did}/send", expected=(202, 204))
        return SendReceipt(
            provider=self.name,
            message_id=draft_id,
            recipients=msg.all_recipients,
            thread_id=str(draft.get("conversationId") or original.thread_id),
            internet_message_id=str(draft.get("internetMessageId") or ""),
        )

    async def verify_sent(self, receipt: SendReceipt) -> bool | None:
        if not receipt.internet_message_id:
            return None
        flt = f"internetMessageId eq '{_odata_quote(receipt.internet_message_id)}'"
        for delay in self.verify_delays:
            if delay:
                await asyncio.sleep(delay)
            body = (
                await self.api.get_json(
                    "me/mailFolders/sentitems/messages", params={"$filter": flt, "$select": "id,internetMessageId"}
                )
                or {}
            )
            if body.get("value"):
                return True
        return False

    # ------------------------------------------------------------ calendar
    async def list_events(self, start: datetime, end: datetime, limit: int = 250) -> list[CalendarEvent]:
        out: list[CalendarEvent] = []
        url: str | None = "me/calendarView"
        params: dict[str, Any] | None = {
            "startDateTime": start.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "endDateTime": end.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "$top": min(limit, 100),
            "$orderby": "start/dateTime",
        }
        while url:
            body = await self.api.get_json(url, params=params, headers={"Prefer": 'outlook.timezone="UTC"'}) or {}
            out += [event_from_graph(e) for e in body.get("value") or [] if not e.get("isCancelled")]
            url = body.get("@odata.nextLink")
            params = None
            if len(out) >= limit:
                break
        return out[:limit]

    async def get_event(self, event_id: str) -> CalendarEvent | None:
        resp = await self.api.request(
            "GET", f"me/events/{quote(event_id, safe='')}", expected=(200, 404), headers={"Prefer": 'outlook.timezone="UTC"'}
        )
        if resp.status_code == 404:
            return None
        raw = safe_json(resp) or {}
        return None if raw.get("isCancelled") else event_from_graph(raw)

    async def create_event(self, ev: CalendarEvent) -> CalendarEvent:
        raw = await self.api.post_json("me/events", event_to_graph(ev), headers={"Prefer": 'outlook.timezone="UTC"'})
        return event_from_graph(raw or {})

    async def update_event(self, event_id: str, ev: CalendarEvent) -> CalendarEvent:
        resp = await self.api.request(
            "PATCH",
            f"me/events/{quote(event_id, safe='')}",
            json=event_to_graph(ev),
            headers={"Prefer": 'outlook.timezone="UTC"'},
        )
        return event_from_graph(resp.json())

    async def delete_event(self, event_id: str, notify: bool = True) -> None:
        await self.api.request("DELETE", f"me/events/{quote(event_id, safe='')}")

    # ------------------------------------------------------------ contacts
    async def search_contacts(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        q = _odata_quote(query)
        flt = f"startswith(displayName,'{q}') or startswith(givenName,'{q}') or startswith(surname,'{q}')"
        body = (
            await self.api.get_json(
                "me/contacts",
                params={
                    "$filter": flt,
                    "$top": min(limit, 50),
                    "$select": "id,displayName,emailAddresses,mobilePhone,businessPhones,homePhones",
                },
            )
            or {}
        )
        out: list[dict[str, Any]] = []
        for c in body.get("value") or []:
            phones = [p for p in [c.get("mobilePhone"), *(c.get("businessPhones") or []), *(c.get("homePhones") or [])] if p]
            out.append(
                {
                    "name": str(c.get("displayName") or ""),
                    "emails": [str(e["address"]) for e in c.get("emailAddresses") or [] if e.get("address")],
                    "phones": [str(p) for p in phones],
                    "source": "outlook",
                    "remote_id": str(c.get("id") or ""),
                }
            )
        return out
