"""Generic IMAP (SSL) search/read + SMTP (STARTTLS or implicit TLS) send.

Uses app passwords from the credential store: ``SCAR_IMAP_PASSWORD`` and
``SCAR_SMTP_PASSWORD`` (the SMTP password falls back to the IMAP one, which
is the same app password for most providers). Sends are verified by looking
up the generated ``Message-ID`` in the server's Sent folder where the server
files SMTP submissions there (Gmail, iCloud, Fastmail do; many others do not,
in which case verification is reported as "couldn't confirm").
"""

from __future__ import annotations

import asyncio
import email.utils
import imaplib
import re
import smtplib
import ssl
from datetime import datetime
from typing import Any

from scar.core.errors import CapabilityUnavailable, ToolError
from scar.integrations.email_types import (
    EmailAddress,
    EmailMessageData,
    EmailSummary,
    OutgoingEmail,
    SendReceipt,
    build_mime,
    parse_mime,
    reply_recipients,
)
from scar.integrations.live_guard import ensure_live_send_allowed

SETUP_DOC = "docs/integrations/gmail.md#imap-smtp-alternative"
NOT_CONFIGURED = (
    "IMAP not configured: set SCAR_IMAP_HOST, SCAR_IMAP_USER, SCAR_SMTP_HOST and store the app "
    "password with `scar config set-secret SCAR_IMAP_PASSWORD`"
)
SENT_CANDIDATES = ("Sent", "Sent Items", "Sent Messages", "[Gmail]/Sent Mail", "INBOX.Sent", "Sent Mail")
TIMEOUT = 30.0
_LIST_RE = re.compile(r'\((?P<flags>[^)]*)\) (?P<delim>"[^"]*"|NIL) (?P<name>.+)')


def _imap_quote(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def build_search_criteria(query: str) -> tuple[list[str], bytes | None]:
    """Translate ``from:x to:y subject:"a b" since:2026-01-31 unread free text`` into IMAP SEARCH keys.

    Returns (criteria, literal) where literal is set for non-ASCII free text (sent as a UTF-8 literal).
    """
    criteria: list[str] = []
    free: list[str] = []
    for m in re.finditer(r'(\w+):"([^"]*)"|(\w+):(\S+)|"([^"]*)"|(\S+)', query):
        key = (m.group(1) or m.group(3) or "").lower()
        val = m.group(2) if m.group(1) else m.group(4)
        if key in ("from", "to", "cc", "subject", "body"):
            criteria += [key.upper(), _imap_quote(val or "")]
        elif key in ("since", "before"):
            try:
                day = datetime.strptime(val or "", "%Y-%m-%d")
            except ValueError as exc:
                raise ToolError(f"{key}: expects YYYY-MM-DD, got {val!r}", "InvalidInput") from exc
            criteria += [key.upper(), day.strftime("%d-%b-%Y")]
        elif key:
            free.append(f"{key}:{val}")
        else:
            word = m.group(5) if m.group(5) is not None else m.group(6)
            if word and word.lower() in ("unread", "is:unread"):
                criteria.append("UNSEEN")
            elif word:
                free.append(word)
    literal: bytes | None = None
    text = " ".join(free).strip()
    if text:
        if text.isascii():
            criteria += ["TEXT", _imap_quote(text)]
        else:
            literal = text.encode("utf-8")
            criteria.append("TEXT")
    return criteria or ["ALL"], literal


class ImapSmtpClient:
    name = "imap"

    def __init__(self, settings: Any, secrets: Any) -> None:
        self.settings = settings
        self.secrets = secrets

    # ------------------------------------------------------------ config
    def _require(self) -> tuple[str, str, str]:
        host = str(getattr(self.settings, "imap_host", "") or "")
        user = str(getattr(self.settings, "imap_user", "") or "")
        pw = self.secrets.get("SCAR_IMAP_PASSWORD")
        if not host or not user or pw is None:
            missing = [n for n, v in (("SCAR_IMAP_HOST", host), ("SCAR_IMAP_USER", user), ("SCAR_IMAP_PASSWORD", pw)) if not v]
            raise CapabilityUnavailable(NOT_CONFIGURED, SETUP_DOC, "missing " + ", ".join(missing))
        return host, user, pw.get_secret_value()

    def _smtp_config(self) -> tuple[str, int, str, str]:
        host = str(getattr(self.settings, "smtp_host", "") or "")
        user = str(getattr(self.settings, "imap_user", "") or "")
        pw = self.secrets.get("SCAR_SMTP_PASSWORD") or self.secrets.get("SCAR_IMAP_PASSWORD")
        if not host or not user or pw is None:
            raise CapabilityUnavailable(
                "SMTP not configured: set SCAR_SMTP_HOST (and SCAR_SMTP_PORT) and store SCAR_SMTP_PASSWORD", SETUP_DOC
            )
        return host, int(getattr(self.settings, "smtp_port", 587) or 587), user, pw.get_secret_value()

    @property
    def address(self) -> str:
        return str(getattr(self.settings, "imap_user", "") or "")

    def _connect(self) -> imaplib.IMAP4_SSL:
        host, user, pw = self._require()
        port = int(getattr(self.settings, "imap_port", 993) or 993)
        try:
            conn = imaplib.IMAP4_SSL(host, port, ssl_context=ssl.create_default_context(), timeout=TIMEOUT)
        except (OSError, imaplib.IMAP4.error) as exc:
            raise ToolError(f"cannot connect to IMAP server {host}:{port}: {exc}", "NetworkError") from exc
        try:
            conn.login(user, pw)
        except imaplib.IMAP4.error as exc:
            conn.shutdown()
            raise CapabilityUnavailable(
                "IMAP login failed: check SCAR_IMAP_USER and the app password", SETUP_DOC, str(exc)
            ) from exc
        return conn

    # ------------------------------------------------------------ read side
    @staticmethod
    def _split_id(message_id: str) -> tuple[str, str]:
        folder, sep, uid = message_id.rpartition(":")
        if not sep or not uid.isdigit():
            raise ToolError(f"bad IMAP message id {message_id!r} (expected FOLDER:UID)", "InvalidInput")
        return folder or "INBOX", uid

    def _search_sync(self, query: str, max_results: int, folder: str = "INBOX") -> list[EmailSummary]:
        conn = self._connect()
        try:
            typ, _ = conn.select(_imap_quote(folder), readonly=True)
            if typ != "OK":
                raise ToolError(f"cannot open IMAP folder {folder}", "ProviderError")
            criteria, literal = build_search_criteria(query)
            if literal is not None:
                # imaplib sends a pending ``literal`` after the command arguments (not in the type stubs)
                setattr(conn, "literal", literal)  # noqa: B010
                typ, data = conn.uid("SEARCH", "CHARSET", "UTF-8", *criteria)
            else:
                typ, data = conn.uid("SEARCH", *criteria)
            if typ != "OK":
                raise ToolError(f"IMAP search failed: {data!r}", "ProviderError")
            uids = (data[0] or b"").split()
            uids = uids[-max_results:][::-1]
            out: list[EmailSummary] = []
            for uid in uids:
                typ, fetched = conn.uid("FETCH", uid.decode(), "(FLAGS BODY.PEEK[HEADER.FIELDS (FROM TO SUBJECT DATE)])")
                if typ != "OK":
                    continue
                header_bytes = b""
                flags = b""
                for item in fetched:
                    if isinstance(item, tuple):
                        flags += item[0]
                        header_bytes += item[1]
                msg = parse_mime(header_bytes, msg_id=f"{folder}:{uid.decode()}", provider=self.name)
                out.append(
                    EmailSummary(
                        id=msg.id,
                        provider=self.name,
                        subject=msg.subject,
                        sender=msg.sender,
                        to=msg.to,
                        date=msg.date,
                        unread=b"\\Seen" not in flags,
                    )
                )
            return out
        finally:
            _logout(conn)

    async def search(self, query: str, max_results: int = 20) -> list[EmailSummary]:
        return await asyncio.to_thread(self._search_sync, query, max_results)

    def _read_sync(self, message_id: str) -> EmailMessageData:
        folder, uid = self._split_id(message_id)
        conn = self._connect()
        try:
            typ, _ = conn.select(_imap_quote(folder), readonly=True)
            if typ != "OK":
                raise ToolError(f"cannot open IMAP folder {folder}", "ProviderError")
            typ, fetched = conn.uid("FETCH", uid, "(BODY.PEEK[])")
            raw = b"".join(item[1] for item in fetched if isinstance(item, tuple))
            if typ != "OK" or not raw:
                raise ToolError(f"message {message_id} not found", "NotFound")
            return parse_mime(raw, msg_id=message_id, provider=self.name)
        finally:
            _logout(conn)

    async def read(self, message_id: str) -> EmailMessageData:
        return await asyncio.to_thread(self._read_sync, message_id)

    # ------------------------------------------------------------ send side
    def _send_sync(self, msg: OutgoingEmail) -> SendReceipt:
        host, port, user, pw = self._smtp_config()
        domain = user.rpartition("@")[2] or None
        message_id = email.utils.make_msgid(domain=domain)
        mime = build_mime(msg, sender=user, message_id=message_id)
        context = ssl.create_default_context()
        try:
            if port == 465:
                server: smtplib.SMTP = smtplib.SMTP_SSL(host, port, context=context, timeout=TIMEOUT)
            else:
                server = smtplib.SMTP(host, port, timeout=TIMEOUT)
            with server:
                server.ehlo()
                if port != 465:
                    server.starttls(context=context)
                    server.ehlo()
                server.login(user, pw)
                refused = server.send_message(mime)
        except smtplib.SMTPAuthenticationError as exc:
            raise CapabilityUnavailable(
                "SMTP login failed: check the app password (SCAR_SMTP_PASSWORD)", SETUP_DOC, str(exc)
            ) from exc
        except smtplib.SMTPRecipientsRefused as exc:
            raise ToolError(f"SMTP server refused every recipient: {list(exc.recipients)}", "RecipientsRefused") from exc
        except (smtplib.SMTPException, OSError) as exc:
            raise ToolError(f"SMTP send failed: {exc}", "SendFailed") from exc
        accepted = [r for r in msg.all_recipients if r not in refused]
        detail = f"refused: {sorted(refused)}" if refused else ""
        return SendReceipt(
            provider=self.name,
            message_id=message_id,
            internet_message_id=message_id,
            recipients=accepted,
            accepted=not refused,
            detail=detail,
        )

    async def send(self, msg: OutgoingEmail) -> SendReceipt:
        self._smtp_config()
        ensure_live_send_allowed(self.settings, "email", msg.all_recipients)
        return await asyncio.to_thread(self._send_sync, msg)

    async def reply_targets(self, original: EmailMessageData, reply_all: bool) -> tuple[list[EmailAddress], list[EmailAddress]]:
        return reply_recipients(original, {self.address}, reply_all)

    async def reply(self, original: EmailMessageData, msg: OutgoingEmail) -> SendReceipt:
        msg.in_reply_to = msg.in_reply_to or original.message_id_header
        msg.references = msg.references or original.references
        return await self.send(msg)

    async def create_draft(self, msg: OutgoingEmail) -> str:
        raise CapabilityUnavailable("IMAP provider drafts are not supported; use a local draft (location=local)", SETUP_DOC)

    # ------------------------------------------------------------ verification
    @staticmethod
    def _find_sent_folder(conn: imaplib.IMAP4_SSL) -> str | None:
        typ, data = conn.list()
        if typ != "OK":
            return None
        names: list[str] = []
        for raw in data:
            if not isinstance(raw, bytes):
                continue
            m = _LIST_RE.match(raw.decode("utf-8", errors="replace"))
            if not m:
                continue
            name = m.group("name").strip().strip('"')
            if "\\sent" in m.group("flags").lower():
                return name
            names.append(name)
        for cand in SENT_CANDIDATES:
            for n in names:
                if n.lower() == cand.lower():
                    return n
        return None

    def _verify_sync(self, receipt: SendReceipt) -> bool | None:
        conn = self._connect()
        try:
            folder = self._find_sent_folder(conn)
            if folder is None:
                return None
            typ, _ = conn.select(_imap_quote(folder), readonly=True)
            if typ != "OK":
                return None
            typ, data = conn.uid("SEARCH", "HEADER", "Message-ID", _imap_quote(receipt.internet_message_id))
            if typ != "OK":
                return None
            return True if (data[0] or b"").split() else None
        finally:
            _logout(conn)

    async def verify_sent(self, receipt: SendReceipt) -> bool | None:
        if not receipt.internet_message_id:
            return None
        for delay in (1.0, 3.0):
            await asyncio.sleep(delay)
            found = await asyncio.to_thread(self._verify_sync, receipt)
            if found:
                return True
        return None  # SMTP accepted it; this server may not file submissions in Sent


def _logout(conn: imaplib.IMAP4_SSL) -> None:
    try:
        conn.logout()
    except (imaplib.IMAP4.error, OSError):
        conn.shutdown()
