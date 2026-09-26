"""ContactResolver: turn "John", "my professor" or "him" into a concrete person - or say it can't.

Sources, in order of trust:

1. user aliases (``contacts_aliases``: "my professor" -> contact)
2. the local ``contacts`` table (added by the user or imported)
3. the provider address book (Google People / Graph contacts) when configured
4. recent email correspondents (From/To of recent mail)

``resolve`` returns scored candidates and a status: ``resolved`` (exactly one
confident match), ``ambiguous`` (the agent must ask the user - it never
guesses) or ``not_found``. Pronouns ("him", "her", "them") resolve only from an
explicit last-discussed-person value and only when that value is a single
known person.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from typing import Any

import structlog

from scar.core.errors import CapabilityUnavailable, ToolError
from scar.core.ids import new_id
from scar.storage.db import now_iso

log = structlog.get_logger("scar.contacts")

PRONOUNS = frozenset({"him", "her", "them", "he", "she", "they", "his", "hers", "their", "that person"})
_ALIAS_PREFIX = re.compile(r"^(?:my|our|the)\s+")
RESOLVE_THRESHOLD = 0.9
SINGLE_MATCH_THRESHOLD = 0.75
MARGIN = 0.15
REMOTE_FACTOR = 0.9
RECENT_FACTOR = 0.85
CHANNEL_HANDLE_KEYS = {"telegram": "telegram", "telegram_user": "telegram", "discord": "discord", "whatsapp": "whatsapp"}


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text.casefold()).strip()


def normalise_alias(alias: str) -> str:
    return _ALIAS_PREFIX.sub("", _norm(alias).strip(" .,!?'\""))


def _digits(text: str) -> str:
    return re.sub(r"\D", "", text)


@dataclass
class ContactCandidate:
    name: str
    emails: list[str] = field(default_factory=list)
    phones: list[str] = field(default_factory=list)
    handles: dict[str, str] = field(default_factory=dict)
    source: str = "local"
    confidence: float = 0.0
    reason: str = ""
    contact_id: str = ""

    def key(self) -> str:
        if self.contact_id:
            return f"id:{self.contact_id}"
        if self.emails:
            return f"email:{self.emails[0].casefold()}"
        return f"name:{_norm(self.name)}"

    def label(self) -> str:
        ident = self.emails[0] if self.emails else (self.phones[0] if self.phones else next(iter(self.handles.values()), ""))
        return f"{self.name} <{ident}>" if ident and ident != self.name else self.name

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["confidence"] = round(self.confidence, 3)
        d["label"] = self.label()
        return d


@dataclass
class Resolution:
    query: str
    status: str  # resolved | ambiguous | not_found
    candidates: list[ContactCandidate] = field(default_factory=list)
    question: str = ""
    notes: list[str] = field(default_factory=list)

    @property
    def best(self) -> ContactCandidate | None:
        return self.candidates[0] if self.status == "resolved" and self.candidates else None

    def to_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "status": self.status,
            "question": self.question,
            "notes": self.notes,
            "candidates": [c.to_dict() for c in self.candidates],
        }


def score_name(query: str, name: str) -> tuple[float, str]:
    q, n = _norm(query), _norm(name)
    if not q or not n:
        return 0.0, ""
    if q == n:
        return 0.95, "exact name"
    q_tokens, n_tokens = q.split(), [t.strip(".") for t in n.split()]
    if len(q_tokens) > 1 and all(t in n_tokens for t in q_tokens):
        return 0.85, "all name parts match"
    if len(q_tokens) == 1 and q in n_tokens:
        return 0.75, "first/last name match"
    if len(q) >= 3 and any(t.startswith(q) for t in n_tokens):
        return 0.5, "name prefix"
    if len(q) >= 4 and q in n:
        return 0.4, "partial name"
    return 0.0, ""


class ContactResolver:
    def __init__(self, db: Any, *, email: Any = None) -> None:
        self.db = db
        self.email = email
        self._last_discussed: list[str] = []

    # ------------------------------------------------------------ last discussed (pronouns)
    def set_last_discussed(self, refs: str | list[str] | None) -> None:
        """Explicitly record who is being discussed (contact ids or email addresses)."""
        if refs is None:
            self._last_discussed = []
        else:
            self._last_discussed = [refs] if isinstance(refs, str) else list(refs)

    @property
    def last_discussed(self) -> list[str]:
        return list(self._last_discussed)

    # ------------------------------------------------------------ local store
    def _row_to_candidate(self, row: dict[str, Any]) -> ContactCandidate:
        return ContactCandidate(
            name=row["name"],
            emails=json.loads(row["emails_json"]),
            phones=json.loads(row["phones_json"]),
            handles=json.loads(row["handles_json"]),
            source=row["source"],
            contact_id=row["contact_id"],
        )

    def all_contacts(self) -> list[ContactCandidate]:
        return [self._row_to_candidate(r) for r in self.db.query("SELECT * FROM contacts ORDER BY name")]

    def get(self, contact_id: str) -> ContactCandidate | None:
        row = self.db.query_one("SELECT * FROM contacts WHERE contact_id = ?", (contact_id,))
        return self._row_to_candidate(row) if row else None

    def lookup_address(self, address: str) -> ContactCandidate | None:
        a = address.strip().casefold()
        for c in self.all_contacts():
            if any(e.casefold() == a for e in c.emails):
                return c
        return None

    def lookup_handle(self, value: str, channel: str | None = None) -> ContactCandidate | None:
        v = value.strip().casefold()
        key = CHANNEL_HANDLE_KEYS.get(channel or "")
        digits = _digits(value)
        for c in self.all_contacts():
            handles = {k: h for k, h in c.handles.items() if key is None or k == key}
            if any(h.strip().casefold() == v for h in handles.values()):
                return c
            if len(digits) >= 7 and any(_digits(p) == digits for p in c.phones):
                return c
        return None

    def add(
        self,
        name: str,
        emails: list[str] | None = None,
        phones: list[str] | None = None,
        handles: dict[str, str] | None = None,
        source: str = "user",
    ) -> str:
        if not name.strip():
            raise ToolError("a contact needs a name", "InvalidInput")
        emails = [e.strip() for e in emails or [] if e.strip()]
        for e in emails:
            if "@" not in e:
                raise ToolError(f"not an email address: {e!r}", "InvalidInput")
        for e in emails:
            existing = self.lookup_address(e)
            if existing is not None:
                merged_emails = existing.emails + [
                    x for x in emails if x.casefold() not in {y.casefold() for y in existing.emails}
                ]
                merged_phones = existing.phones + [p for p in phones or [] if p not in existing.phones]
                merged_handles = {**existing.handles, **(handles or {})}
                self.db.execute(
                    "UPDATE contacts SET name = ?, emails_json = ?, phones_json = ?, handles_json = ?, "
                    "last_seen = ? WHERE contact_id = ?",
                    (
                        name.strip(),
                        json.dumps(merged_emails),
                        json.dumps(merged_phones),
                        json.dumps(merged_handles),
                        now_iso(),
                        existing.contact_id,
                    ),
                )
                return existing.contact_id
        contact_id = new_id("ct")
        self.db.execute(
            "INSERT INTO contacts(contact_id, name, emails_json, phones_json, handles_json, source, last_seen, created_at)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (
                contact_id,
                name.strip(),
                json.dumps(emails),
                json.dumps([p.strip() for p in phones or [] if p.strip()]),
                json.dumps(handles or {}),
                source,
                None,
                now_iso(),
            ),
        )
        return contact_id

    def set_alias(self, alias: str, contact_id: str) -> str:
        key = normalise_alias(alias)
        if not key:
            raise ToolError("alias is empty", "InvalidInput")
        if key in PRONOUNS:
            raise ToolError(f"{alias!r} is a pronoun and cannot be an alias", "InvalidInput")
        if self.get(contact_id) is None:
            raise ToolError(f"contact {contact_id} not found", "NotFound")
        self.db.execute(
            "INSERT INTO contacts_aliases(alias, contact_id, created_at) VALUES (?,?,?) "
            "ON CONFLICT(alias) DO UPDATE SET contact_id = excluded.contact_id, created_at = excluded.created_at",
            (key, contact_id, now_iso()),
        )
        return key

    def alias_target(self, alias: str) -> str | None:
        row = self.db.query_one("SELECT contact_id FROM contacts_aliases WHERE alias = ?", (normalise_alias(alias),))
        return str(row["contact_id"]) if row else None

    # ------------------------------------------------------------ resolution
    def _local_candidates(self, query: str) -> list[ContactCandidate]:
        q = query.strip()
        out: list[ContactCandidate] = []
        target = self.alias_target(q)
        if target is not None:
            c = self.get(target)
            if c is not None:
                c.confidence, c.reason = 1.0, f"your alias {normalise_alias(q)!r}"
                out.append(c)
        digits = _digits(q)
        for c in self.all_contacts():
            best, why = 0.0, ""
            if "@" in q and any(e.casefold() == q.casefold() for e in c.emails):
                best, why = 1.0, "email address"
            elif (
                len(digits) >= 7
                and len(digits) >= len(q.replace(" ", "")) - 3
                and any(_digits(p).endswith(digits) or digits.endswith(_digits(p)) for p in c.phones if _digits(p))
            ):
                best, why = 1.0, "phone number"
            elif any(
                h.casefold() == q.casefold() or h.casefold().lstrip("@") == q.casefold().lstrip("@") for h in c.handles.values()
            ):
                best, why = 0.97, "handle"
            else:
                best, why = score_name(q, c.name)
            if best > 0:
                c.confidence, c.reason = best, why
                out.append(c)
        return out

    async def _remote_candidates(self, query: str, notes: list[str]) -> list[ContactCandidate]:
        if self.email is None:
            return []
        out: list[ContactCandidate] = []
        try:
            for raw in await self.email.search_contacts(query):
                score, why = (
                    (1.0, "email address")
                    if any(e.casefold() == query.casefold() for e in raw.get("emails", []))
                    else score_name(query, raw["name"])
                )
                if score > 0:
                    out.append(
                        ContactCandidate(
                            name=raw["name"],
                            emails=list(raw.get("emails", [])),
                            phones=list(raw.get("phones", [])),
                            source=str(raw.get("source")),
                            confidence=score * REMOTE_FACTOR,
                            reason=f"{why} ({raw.get('source')})",
                        )
                    )
        except (CapabilityUnavailable, ToolError) as exc:
            notes.append(f"address book unavailable: {exc}")
        try:
            for addr in await self.email.recent_correspondents(50):
                local_part = addr.address.split("@", 1)[0].replace(".", " ").replace("_", " ")
                if addr.address.casefold() == query.casefold():
                    score, why = 1.0, "email address"
                else:
                    score, why = max(score_name(query, addr.name), score_name(query, local_part))
                if score > 0:
                    out.append(
                        ContactCandidate(
                            name=addr.name or addr.address,
                            emails=[addr.address],
                            source="recent_email",
                            confidence=score * RECENT_FACTOR,
                            reason=f"{why} (recent email)",
                        )
                    )
        except (CapabilityUnavailable, ToolError) as exc:
            notes.append(f"recent correspondents unavailable: {exc}")
        return out

    @staticmethod
    def _merge(cands: list[ContactCandidate]) -> list[ContactCandidate]:
        merged: dict[str, ContactCandidate] = {}
        by_email: dict[str, str] = {}
        for c in cands:
            key = next((by_email[e.casefold()] for e in c.emails if e.casefold() in by_email), c.key())
            cur = merged.get(key)
            if cur is None:
                merged[key] = c
            else:
                if c.confidence > cur.confidence:
                    cur.confidence, cur.reason = c.confidence, c.reason
                cur.emails += [e for e in c.emails if e.casefold() not in {x.casefold() for x in cur.emails}]
                cur.phones += [p for p in c.phones if p not in cur.phones]
                if c.source not in cur.source.split("+"):
                    cur.source = f"{cur.source}+{c.source}"
            for e in merged[key].emails:
                by_email[e.casefold()] = key
        return sorted(merged.values(), key=lambda c: (-c.confidence, c.name.casefold()))

    def _resolve_pronoun(self, query: str, last: list[str]) -> Resolution:
        if not last:
            return Resolution(
                query,
                "not_found",
                question=f"Who do you mean by {query!r}?",
                notes=["no person has been explicitly discussed yet"],
            )
        cands: list[ContactCandidate] = []
        for ref in last:
            c = self.get(ref) or (self.lookup_address(ref) if "@" in ref else None)
            if c is None and "@" in ref:
                c = ContactCandidate(name=ref, emails=[ref], source="conversation")
            if c is not None:
                c.confidence, c.reason = 0.9, "the person you were just discussing"
                cands.append(c)
        if len(cands) == 1:
            return Resolution(query, "resolved", cands)
        if not cands:
            return Resolution(query, "not_found", question=f"Who do you mean by {query!r}?")
        return Resolution(
            query, "ambiguous", cands, question=f"Who do you mean by {query!r}: " + ", ".join(c.label() for c in cands) + "?"
        )

    async def resolve(
        self, query: str, *, include_remote: bool = True, channel: str | None = None, last_discussed: list[str] | None = None
    ) -> Resolution:
        q = query.strip()
        if not q:
            raise ToolError("nothing to resolve", "InvalidInput")
        if _norm(q) in PRONOUNS:
            return self._resolve_pronoun(q, self._last_discussed if last_discussed is None else last_discussed)
        notes: list[str] = []
        cands = self._local_candidates(q)
        alias_hit = any(c.confidence >= 1.0 and c.reason.startswith("your alias") for c in cands)
        if include_remote and not alias_hit:
            cands += await self._remote_candidates(q, notes)
        cands = self._merge(cands)
        if channel:
            cands = [c for c in cands if _reachable(c, channel)] or cands
        if alias_hit:
            top = cands[0]
            return Resolution(q, "resolved", [top], notes=notes)
        if not cands:
            return Resolution(
                q,
                "not_found",
                question=f"I don't know who {q!r} is. What is their email address (or other contact detail)?",
                notes=notes,
            )
        top = cands[0]
        second = cands[1] if len(cands) > 1 else None
        confident = top.confidence >= RESOLVE_THRESHOLD and (second is None or second.confidence < top.confidence - MARGIN)
        single = second is None and top.confidence >= SINGLE_MATCH_THRESHOLD
        if confident or single:
            return Resolution(q, "resolved", [top], notes=notes)
        shown = cands[:6]
        question = (
            (f"Which {q} do you mean: " + "; ".join(c.label() for c in shown) + "?")
            if len(shown) > 1
            else f"Do you mean {shown[0].label()}?"
        )
        return Resolution(q, "ambiguous", shown, question=question, notes=notes)


def _reachable(c: ContactCandidate, channel: str) -> bool:
    if channel == "email":
        return bool(c.emails)
    key = CHANNEL_HANDLE_KEYS.get(channel)
    if channel == "whatsapp":
        return bool(c.phones) or "whatsapp" in c.handles
    return bool(key and key in c.handles) or (channel == "telegram_user" and bool(c.phones))


def get_resolver(services: Any) -> ContactResolver:
    """The shared resolver from Services (created on first use if the composition root did not)."""
    resolver = getattr(services, "contacts", None)
    if resolver is None:
        resolver = ContactResolver(services.db, email=getattr(services, "email", None))
        services.contacts = resolver
    return resolver
