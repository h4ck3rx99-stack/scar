"""Secret redaction for logs, traces, model-bound text and CLI output (C3, C4.10)."""

from __future__ import annotations

import re
import threading
from collections.abc import Iterable, Mapping
from typing import Any

REDACTED = "[REDACTED]"

# (name, pattern). Patterns are deliberately conservative to avoid mangling normal text.
_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("private_key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]+?-----END [A-Z ]*PRIVATE KEY-----")),
    ("auth_header", re.compile(r"(?i)\b(authorization|proxy-authorization)\s*[:=]\s*(?:bearer|basic|token)?\s*[^\s,;\"']+")),
    ("bearer", re.compile(r"(?i)\bbearer\s+[A-Za-z0-9\-._~+/]{12,}=*")),
    ("openai_key", re.compile(r"\bsk-(?:proj-|ant-)?[A-Za-z0-9_\-]{16,}")),
    ("groq_key", re.compile(r"\bgsk_[A-Za-z0-9]{20,}")),
    ("google_api_key", re.compile(r"\bAIza[0-9A-Za-z_\-]{30,}")),
    ("google_oauth", re.compile(r"\bya29\.[0-9A-Za-z_\-]{20,}")),
    ("google_refresh", re.compile(r"\b1//0[0-9A-Za-z_\-]{30,}")),
    ("github_token", re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{30,}|\bgithub_pat_[A-Za-z0-9_]{40,}")),
    ("slack_token", re.compile(r"\bxox[abprs]-[A-Za-z0-9\-]{10,}")),
    ("hf_token", re.compile(r"\bhf_[A-Za-z0-9]{30,}")),
    ("nvidia_key", re.compile(r"\bnvapi-[A-Za-z0-9_\-]{30,}")),
    ("cerebras_key", re.compile(r"\bcsk-[A-Za-z0-9]{30,}")),
    ("openrouter_key", re.compile(r"\bsk-or-v1-[a-f0-9]{40,}")),
    ("aws_key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("telegram_bot_token", re.compile(r"\b\d{8,10}:AA[A-Za-z0-9_\-]{30,}")),
    ("discord_token", re.compile(r"\b[MNO][A-Za-z\d]{23,25}\.[\w-]{6}\.[\w-]{27,38}\b")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}\.eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}")),
    ("url_credentials", re.compile(r"(?i)\b([a-z][a-z0-9+.\-]*://)[^\s:/@]+:[^\s@/]+@")),
    (
        "password_assignment",
        re.compile(
            r"(?i)\b(password|passwd|pwd|secret|api[_-]?key|access[_-]?token|client[_-]?secret)\b"
            r"(\s*[:=]\s*|\s+)(\"[^\"]{3,}\"|'[^']{3,}'|[^\s\"',;]{3,})"
        ),
    ),
    ("ps_password_param", re.compile(r"(?i)(-(?:Password|Credential|Token|ApiKey|Secret)\s+)(\"[^\"]+\"|'[^']+'|\S+)")),
    ("secure_string", re.compile(r"(?i)ConvertTo-SecureString\s+(-String\s+)?(\"[^\"]+\"|'[^']+'|\S+)")),
]


_REPLACERS: dict[str, Any] = {
    "url_credentials": lambda m: f"{m.group(1)}{REDACTED}@",
    "password_assignment": lambda m: f"{m.group(1)}{m.group(2)}{REDACTED}",
    "ps_password_param": lambda m: f"{m.group(1)}{REDACTED}",
    "secure_string": lambda m: f"ConvertTo-SecureString {m.group(1) or ''}{REDACTED}",
}


class Redactor:
    """Redacts known secret values and common token patterns. Thread-safe."""

    def __init__(self) -> None:
        self._known: dict[str, str] = {}
        self._lock = threading.Lock()
        self._known_re: re.Pattern[str] | None = None

    def add_secret(self, name: str, value: str | None) -> None:
        if not value or len(value) < 6:
            return
        with self._lock:
            self._known[value] = name
            self._known_re = re.compile("|".join(re.escape(v) for v in sorted(self._known, key=len, reverse=True)))

    def add_secrets(self, items: Iterable[tuple[str, str | None]]) -> None:
        """Register (name, value) pairs, e.g. ``redactor.add_secrets(mapping.items())``."""
        for name, value in items:
            self.add_secret(name, value)

    @property
    def known_count(self) -> int:
        return len(self._known)

    def redact(self, text: str) -> str:
        if not text:
            return text
        known_re = self._known_re
        if known_re is not None:
            text = known_re.sub(lambda m: f"[REDACTED:{self._known.get(m.group(0), 'secret')}]", text)
        for name, pattern in _PATTERNS:
            text = pattern.sub(_REPLACERS.get(name, REDACTED), text)
        return text

    def contains_secret(self, text: str) -> list[str]:
        """Return the names of secret kinds found in ``text`` (empty when clean)."""
        if not text:
            return []
        found: list[str] = []
        known_re = self._known_re
        if known_re is not None:
            for m in known_re.finditer(text):
                found.append(self._known.get(m.group(0), "secret"))
        for name, pattern in _PATTERNS:
            if name in ("password_assignment", "ps_password_param", "url_credentials"):
                # assignments with obvious placeholders are not secrets
                for m in pattern.finditer(text):
                    value = m.group(m.lastindex or 0).strip("\"'")
                    if value.lower() not in {"xxx", "***", "changeme", "password", "<password>", "your_key"}:
                        found.append(name)
                        break
            elif pattern.search(text):
                found.append(name)
        return found

    def redact_obj(self, obj: Any, _depth: int = 0) -> Any:
        if _depth > 20:
            return obj
        if isinstance(obj, str):
            return self.redact(obj)
        if isinstance(obj, Mapping):
            out: dict[Any, Any] = {}
            for k, v in obj.items():
                key = str(k).lower()
                if any(s in key for s in ("password", "secret", "api_key", "apikey", "token", "authorization")) and isinstance(v, str) and v:
                    out[k] = REDACTED
                else:
                    out[k] = self.redact_obj(v, _depth + 1)
            return out
        if isinstance(obj, list | tuple):
            items = [self.redact_obj(v, _depth + 1) for v in obj]
            return type(obj)(items) if isinstance(obj, tuple) else items
        return obj


_global = Redactor()


def global_redactor() -> Redactor:
    return _global


def redact(text: str) -> str:
    return _global.redact(text)
