"""Memory write policy (C9.11): never store secrets or sensitive identifiers; keep only durable, useful facts."""

from __future__ import annotations

import re
from dataclasses import dataclass

from scar.security.redaction import global_redactor

_SENSITIVE = [
    ("payment card number", re.compile(r"\b(?:\d[ -]?){13,19}\b")),
    ("government id", re.compile(r"(?i)\b(ssn|social security|passport|aadhaar|pan card|national id)\b.{0,20}\d")),
    ("us ssn", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
    ("password", re.compile(r"(?i)\b(password|passcode|pin|otp)\b\s*(is|:|=)")),
    ("bank account", re.compile(r"(?i)\b(iban|account number|routing number|ifsc)\b")),
]

CATEGORIES = ("semantic", "preference", "project", "episodic", "task_history", "alias", "tool_history", "conversation")
EPISODIC_TTL_DAYS = 30


@dataclass
class WriteCheck:
    allowed: bool
    reason: str = ""


def check_write(text: str, category: str) -> WriteCheck:
    if category not in CATEGORIES:
        return WriteCheck(False, f"unknown memory category {category!r}")
    if not text.strip():
        return WriteCheck(False, "empty memory")
    if len(text) > 4000:
        return WriteCheck(False, "memories must be short facts (<4000 characters)")
    kinds = global_redactor().contains_secret(text)
    if kinds:
        return WriteCheck(False, f"looks like a secret ({', '.join(sorted(set(kinds))[:2])}); secrets are never stored")
    for name, pattern in _SENSITIVE:
        if pattern.search(text):
            if name == "payment card number" and not _luhn_any(text):
                continue
            return WriteCheck(False, f"contains sensitive data ({name}); not stored")
    return WriteCheck(True)


def _luhn_any(text: str) -> bool:
    for m in re.finditer(r"\b(?:\d[ -]?){13,19}\b", text):
        digits = [int(d) for d in re.sub(r"\D", "", m.group(0))]
        total = 0
        for i, d in enumerate(reversed(digits)):
            if i % 2 == 1:
                d = d * 2 - 9 if d * 2 > 9 else d * 2
            total += d
        if total % 10 == 0:
            return True
    return False
