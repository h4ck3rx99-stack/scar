"""EmailService: one facade over Gmail, Outlook (Microsoft Graph) and IMAP/SMTP.

Provider choice follows ``settings.email_provider``; ``auto`` picks the first
*configured* provider in the order gmail, outlook, imap (configured = its
non-secret setting is present). A provider that is selected but not ready
raises ``CapabilityUnavailable`` naming the exact missing prerequisite and
pointing at docs/integrations/<provider>.md.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

import httpx

from scar.core.errors import CapabilityUnavailable, ToolError
from scar.core.ids import new_id
from scar.core.types import Check, VerificationResult
from scar.integrations.email_types import (
    EmailAddress,
    EmailMessageData,
    EmailSummary,
    OutgoingEmail,
    SendReceipt,
    reply_subject,
)

GMAIL_DOC = "docs/integrations/gmail.md"
OUTLOOK_DOC = "docs/integrations/outlook.md"
NONE_CONFIGURED = (
    "EMAIL not configured: set SCAR_GOOGLE_OAUTH_CLIENT_FILE and run `scar auth google` (Gmail), "
    "or SCAR_MS_CLIENT_ID and `scar auth microsoft` (Outlook), or SCAR_IMAP_HOST/SCAR_IMAP_USER/"
    "SCAR_SMTP_HOST + SCAR_IMAP_PASSWORD (IMAP)"
)


class EmailService:
    def __init__(self, settings: Any, secrets: Any, *, http: httpx.AsyncClient | None = None) -> None:
        self.settings = settings
        self.secrets = secrets
        self._http = http
        self._providers: dict[str, Any] = {}

    # ------------------------------------------------------------ provider selection
    def configured_providers(self) -> list[str]:
        out: list[str] = []
        if getattr(self.settings, "google_oauth_client_file", ""):
            out.append("gmail")
        if getattr(self.settings, "ms_client_id", ""):
            out.append("outlook")
        if getattr(self.settings, "imap_host", ""):
            out.append("imap")
        return out

    def provider_name(self, override: str | None = None) -> str:
        choice = override or str(getattr(self.settings, "email_provider", "auto") or "auto")
        if override and not self.configured_providers():
            # the model guessed a provider but no account is connected at all: show every way to connect one
            raise CapabilityUnavailable(NONE_CONFIGURED, GMAIL_DOC)
        if choice != "auto":
            if choice not in ("gmail", "outlook", "imap"):
                raise ToolError(f"unknown email provider {choice!r}", "InvalidInput")
            return choice
        configured = self.configured_providers()
        if not configured:
            raise CapabilityUnavailable(NONE_CONFIGURED, GMAIL_DOC)
        return configured[0]

    def provider(self, override: str | None = None) -> Any:
        name = self.provider_name(override)
        if name in self._providers:
            return self._providers[name]
        if name == "gmail":
            from scar.integrations.google.auth import NOT_CONFIGURED, google_configured, google_session
            from scar.integrations.google.gmail import GmailClient

            if not google_configured(self.settings):
                raise CapabilityUnavailable(NOT_CONFIGURED, GMAIL_DOC)
            prov: Any = GmailClient(google_session(self.settings, self.secrets, self._http), self.settings, self._http)
        elif name == "outlook":
            from scar.integrations.microsoft.auth import NOT_CONFIGURED as MS_NOT_CONFIGURED
            from scar.integrations.microsoft.auth import microsoft_configured, microsoft_session
            from scar.integrations.microsoft.graph import GraphClient

            if not microsoft_configured(self.settings):
                raise CapabilityUnavailable(MS_NOT_CONFIGURED, OUTLOOK_DOC)
            prov = GraphClient(microsoft_session(self.settings, self.secrets, self._http), self.settings, self._http)
        else:
            from scar.integrations.imap_smtp import ImapSmtpClient

            prov = ImapSmtpClient(self.settings, self.secrets)
        self._providers[name] = prov
        return prov

    def status(self) -> dict[str, Any]:
        """Non-throwing readiness summary (for `scar doctor` and tool descriptions)."""
        try:
            name = self.provider_name()
            self.provider(name)
            return {"ready": True, "provider": name}
        except CapabilityUnavailable as exc:
            return {"ready": False, "prerequisite": exc.prerequisite, "setup_doc": exc.setup_doc}

    # ------------------------------------------------------------ operations
    async def search(self, query: str = "", max_results: int = 20, provider: str | None = None) -> list[EmailSummary]:
        return list(await self.provider(provider).search(query, max_results))

    async def read(self, message_id: str, provider: str | None = None) -> EmailMessageData:
        return await self.provider(provider).read(message_id)

    async def send(self, msg: OutgoingEmail, provider: str | None = None) -> SendReceipt:
        if not msg.to:
            raise ToolError("an email needs at least one recipient", "InvalidInput")
        return await self.provider(provider).send(msg)

    async def reply_targets(
        self, message_id: str, reply_all: bool, provider: str | None = None
    ) -> tuple[EmailMessageData, list[EmailAddress], list[EmailAddress]]:
        prov = self.provider(provider)
        original = await prov.read(message_id)
        to, cc = await prov.reply_targets(original, reply_all)
        return original, to, cc

    async def reply(
        self,
        message_id: str,
        body: str,
        *,
        reply_all: bool = False,
        attachments: list[Path] | None = None,
        expected_to: list[str] | None = None,
        provider: str | None = None,
    ) -> SendReceipt:
        """Reply in-thread. ``expected_to`` pins the recipients the user approved; a mismatch aborts."""
        prov = self.provider(provider)
        original, to, cc = await self.reply_targets(message_id, reply_all, provider)
        actual = sorted({a.address.casefold() for a in [*to, *cc]})
        if expected_to is not None and actual != sorted({a.casefold() for a in expected_to}):
            raise ToolError(
                f"the reply would go to {actual}, not the approved {sorted(expected_to)}; re-read the message and ask again",
                "RecipientMismatch",
            )
        msg = OutgoingEmail(
            to=to,
            cc=cc,
            subject=reply_subject(original.subject),
            body=body,
            attachments=list(attachments or []),
            thread_id=original.thread_id,
            in_reply_to=original.message_id_header,
            references=original.references,
        )
        return await prov.reply(original, msg)

    async def verify(self, receipt: SendReceipt, provider: str | None = None) -> VerificationResult:
        checks = [Check(name="provider returned a message id", passed=bool(receipt.message_id), detail=receipt.message_id[:60])]
        if not receipt.accepted:
            checks.append(Check(name="all recipients accepted", passed=False, detail=receipt.detail))
        found = await self.provider(provider or receipt.provider).verify_sent(receipt)
        evidence = receipt.to_dict()
        if found is None:
            if all(c.passed for c in checks):
                return VerificationResult(
                    verified=None, checks=checks, evidence=evidence, note="sent, but couldn't confirm it in the Sent folder"
                )
            return VerificationResult.from_checks(checks, evidence)
        checks.append(Check(name="message is in the Sent folder", passed=found))
        return VerificationResult.from_checks(checks, evidence)

    # ------------------------------------------------------------ drafts
    @property
    def drafts_dir(self) -> Path:
        return Path(self.settings.data_path) / "drafts"

    def save_local_draft(self, msg: OutgoingEmail) -> dict[str, Any]:
        self.drafts_dir.mkdir(parents=True, exist_ok=True)
        draft_id = new_id("draft")
        path = self.drafts_dir / f"{draft_id}.json"
        payload = asdict(msg)
        payload["attachments"] = [str(p) for p in msg.attachments]
        payload["draft_id"] = draft_id
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return {"draft_id": draft_id, "location": "local", "path": str(path)}

    def load_local_draft(self, draft_id: str) -> OutgoingEmail:
        if not draft_id.replace("_", "").isalnum():
            raise ToolError(f"bad draft id {draft_id!r}", "InvalidInput")
        path = self.drafts_dir / f"{draft_id}.json"
        if not path.is_file():
            raise ToolError(f"draft {draft_id} not found", "NotFound")
        raw = json.loads(path.read_text(encoding="utf-8"))

        def addrs(items: list[dict[str, str]]) -> list[EmailAddress]:
            return [EmailAddress(address=i["address"], name=i.get("name", "")) for i in items]

        return OutgoingEmail(
            to=addrs(raw["to"]),
            cc=addrs(raw.get("cc", [])),
            bcc=addrs(raw.get("bcc", [])),
            subject=raw.get("subject", ""),
            body=raw.get("body", ""),
            attachments=[Path(p) for p in raw.get("attachments", [])],
            in_reply_to=raw.get("in_reply_to", ""),
            references=raw.get("references", ""),
            thread_id=raw.get("thread_id", ""),
        )

    async def draft(self, msg: OutgoingEmail, *, location: str = "local", provider: str | None = None) -> dict[str, Any]:
        if location == "local":
            return self.save_local_draft(msg)
        name = self.provider_name(provider)
        draft_id = await self.provider(name).create_draft(msg)
        return {"draft_id": draft_id, "location": name}

    # ------------------------------------------------------------ contacts support
    async def recent_correspondents(self, limit: int = 50) -> list[EmailAddress]:
        """Addresses from recent mail (senders and recipients), most recent first, de-duplicated."""
        seen: set[str] = set()
        out: list[EmailAddress] = []
        for summary in await self.search("", max_results=limit):
            for addr in [*([summary.sender] if summary.sender else []), *summary.to]:
                key = addr.address.casefold()
                if key not in seen:
                    seen.add(key)
                    out.append(addr)
        return out

    async def search_contacts(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        """Remote address book (Google People / Graph contacts) for the active provider; [] for IMAP."""
        name = self.provider_name()
        if name == "gmail":
            from scar.integrations.google.auth import google_session
            from scar.integrations.google.people import GooglePeopleClient

            people = self._providers.get("people")
            if people is None:
                people = GooglePeopleClient(google_session(self.settings, self.secrets, self._http), self._http)
                self._providers["people"] = people
            return list(await people.search(query, limit))
        if name == "outlook":
            return list(await self.provider("outlook").search_contacts(query, limit))
        return []
