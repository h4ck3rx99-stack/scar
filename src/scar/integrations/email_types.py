"""Provider-neutral email models plus MIME build/parse helpers (stdlib ``email``)."""

from __future__ import annotations

import email.policy
import email.utils
import html
import mimetypes
import re
from dataclasses import asdict, dataclass, field
from email.message import EmailMessage as MimeMessage
from email.message import Message
from pathlib import Path
from typing import Any, Protocol

from scar.core.errors import ToolError

MAX_ATTACHMENT_TOTAL = 25 * 1024 * 1024  # Gmail/Outlook practical ceiling for a single message


@dataclass
class EmailAddress:
    address: str
    name: str = ""

    @classmethod
    def parse(cls, raw: str) -> EmailAddress:
        name, addr = email.utils.parseaddr(raw)
        if not addr or "@" not in addr:
            raise ToolError(f"not an email address: {raw!r}", "InvalidInput")
        return cls(address=addr.strip(), name=name.strip())

    @classmethod
    def parse_list(cls, raw: str | None) -> list[EmailAddress]:
        if not raw:
            return []
        return [cls(address=a.strip(), name=n.strip()) for n, a in email.utils.getaddresses([raw]) if a and "@" in a]

    def formatted(self) -> str:
        return email.utils.formataddr((self.name, self.address)) if self.name else self.address

    def display(self) -> str:
        return f"{self.name} <{self.address}>" if self.name else self.address


@dataclass
class AttachmentInfo:
    filename: str
    size: int
    content_type: str = "application/octet-stream"
    attachment_id: str = ""


@dataclass
class EmailSummary:
    id: str
    provider: str
    subject: str = ""
    sender: EmailAddress | None = None
    to: list[EmailAddress] = field(default_factory=list)
    date: str = ""
    snippet: str = ""
    thread_id: str = ""
    unread: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class EmailMessageData(EmailSummary):
    cc: list[EmailAddress] = field(default_factory=list)
    reply_to: list[EmailAddress] = field(default_factory=list)
    body_text: str = ""
    attachments: list[AttachmentInfo] = field(default_factory=list)
    message_id_header: str = ""
    references: str = ""


@dataclass
class OutgoingEmail:
    to: list[EmailAddress]
    subject: str
    body: str
    cc: list[EmailAddress] = field(default_factory=list)
    bcc: list[EmailAddress] = field(default_factory=list)
    attachments: list[Path] = field(default_factory=list)
    in_reply_to: str = ""
    references: str = ""
    thread_id: str = ""

    @property
    def all_recipients(self) -> list[str]:
        return [a.address for a in [*self.to, *self.cc, *self.bcc]]


@dataclass
class SendReceipt:
    provider: str
    message_id: str
    recipients: list[str]
    thread_id: str = ""
    internet_message_id: str = ""
    accepted: bool = True
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class EmailProvider(Protocol):
    name: str

    async def search(self, query: str, max_results: int) -> list[EmailSummary]: ...

    async def read(self, message_id: str) -> EmailMessageData: ...

    async def send(self, msg: OutgoingEmail) -> SendReceipt: ...

    async def reply(self, original: EmailMessageData, msg: OutgoingEmail) -> SendReceipt: ...

    async def verify_sent(self, receipt: SendReceipt) -> bool | None: ...

    async def create_draft(self, msg: OutgoingEmail) -> str: ...


# ------------------------------------------------------------------ MIME
def attachment_content_type(path: Path) -> tuple[str, str]:
    ctype, encoding = mimetypes.guess_type(path.name)
    if ctype is None or encoding is not None:
        ctype = "application/octet-stream"
    maintype, _, subtype = ctype.partition("/")
    return maintype, subtype


def check_attachments(paths: list[Path]) -> int:
    total = 0
    for p in paths:
        if not p.is_file():
            raise ToolError(f"attachment not found: {p}", "NotFound")
        total += p.stat().st_size
    if total > MAX_ATTACHMENT_TOTAL:
        raise ToolError(f"attachments total {total} bytes exceed the {MAX_ATTACHMENT_TOTAL} byte limit", "TooLarge")
    return total


def build_mime(msg: OutgoingEmail, sender: str = "", message_id: str = "") -> MimeMessage:
    """RFC 5322 message; multipart/mixed with base64 attachments when there are any."""
    check_attachments(msg.attachments)
    mime = MimeMessage(policy=email.policy.SMTP)
    if sender:
        mime["From"] = sender
    mime["To"] = ", ".join(a.formatted() for a in msg.to)
    if msg.cc:
        mime["Cc"] = ", ".join(a.formatted() for a in msg.cc)
    if msg.bcc:
        mime["Bcc"] = ", ".join(a.formatted() for a in msg.bcc)
    mime["Subject"] = msg.subject
    mime["Date"] = email.utils.formatdate(localtime=True)
    if message_id:
        mime["Message-ID"] = message_id
    if msg.in_reply_to:
        mime["In-Reply-To"] = msg.in_reply_to
        mime["References"] = (msg.references + " " + msg.in_reply_to).strip() if msg.references else msg.in_reply_to
    mime.set_content(msg.body)
    for path in msg.attachments:
        maintype, subtype = attachment_content_type(path)
        mime.add_attachment(path.read_bytes(), maintype=maintype, subtype=subtype, filename=path.name)
    return mime


_TAG = re.compile(r"<[^>]+>")
_SCRIPT = re.compile(r"(?is)<(script|style)[^>]*>.*?</\1>")
_BR = re.compile(r"(?i)<br\s*/?>|</p>|</div>|</li>|</tr>")


def html_to_text(markup: str) -> str:
    text = _SCRIPT.sub("", markup)
    text = _BR.sub("\n", text)
    text = _TAG.sub("", text)
    text = html.unescape(text)
    return re.sub(r"\n{3,}", "\n\n", re.sub(r"[ \t]+", " ", text)).strip()


def parse_mime(raw: bytes, *, msg_id: str, provider: str, thread_id: str = "") -> EmailMessageData:
    parsed = email.message_from_bytes(raw, policy=email.policy.default)
    return message_to_data(parsed, msg_id=msg_id, provider=provider, thread_id=thread_id)


def message_to_data(parsed: Message, *, msg_id: str, provider: str, thread_id: str = "") -> EmailMessageData:
    text_parts: list[str] = []
    html_parts: list[str] = []
    attachments: list[AttachmentInfo] = []
    for part in parsed.walk():
        if part.is_multipart():
            continue
        disposition = (part.get_content_disposition() or "").lower()
        filename = part.get_filename()
        payload = part.get_payload(decode=True)
        data = payload if isinstance(payload, bytes) else b""
        if disposition == "attachment" or filename:
            attachments.append(
                AttachmentInfo(filename=filename or "attachment", size=len(data), content_type=part.get_content_type())
            )
            continue
        charset = part.get_content_charset() or "utf-8"
        try:
            text = data.decode(charset, errors="replace")
        except LookupError:
            text = data.decode("utf-8", errors="replace")
        if part.get_content_type() == "text/plain":
            text_parts.append(text)
        elif part.get_content_type() == "text/html":
            html_parts.append(text)
    body = "\n".join(text_parts).strip() or html_to_text("\n".join(html_parts))
    sender = EmailAddress.parse_list(str(parsed.get("From", "")))
    return EmailMessageData(
        id=msg_id,
        provider=provider,
        subject=str(parsed.get("Subject", "")),
        sender=sender[0] if sender else None,
        to=EmailAddress.parse_list(str(parsed.get("To", ""))),
        cc=EmailAddress.parse_list(str(parsed.get("Cc", ""))),
        reply_to=EmailAddress.parse_list(str(parsed.get("Reply-To", ""))),
        date=str(parsed.get("Date", "")),
        snippet=body[:200],
        thread_id=thread_id,
        body_text=body,
        attachments=attachments,
        message_id_header=str(parsed.get("Message-ID", "")).strip(),
        references=str(parsed.get("References", "")).strip(),
    )


def reply_subject(subject: str) -> str:
    return subject if re.match(r"(?i)^\s*re\s*:", subject) else f"Re: {subject}"


def reply_recipients(
    original: EmailMessageData, own_addresses: set[str], reply_all: bool
) -> tuple[list[EmailAddress], list[EmailAddress]]:
    """(to, cc) for a reply, excluding the user's own addresses."""
    own = {a.casefold() for a in own_addresses}
    primary = original.reply_to or ([original.sender] if original.sender else [])
    to = [a for a in primary if a.address.casefold() not in own]
    cc: list[EmailAddress] = []
    if reply_all:
        seen = {a.address.casefold() for a in to} | own
        for a in [*original.to, *original.cc]:
            if a.address.casefold() not in seen:
                cc.append(a)
                seen.add(a.address.casefold())
    if not to and original.sender is not None:
        to = [original.sender]
    return to, cc
