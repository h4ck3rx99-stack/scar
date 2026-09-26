"""Taint tracking (C4.9).

A value is *tainted* when it appears in content that entered the task as
``UNTRUSTED_EXTERNAL`` (web pages, emails, documents, terminal output, OCR…)
and does **not** appear in anything the user said. The check is purely
deterministic and does not depend on the model reporting where a value came
from.
"""

from __future__ import annotations

import re
import threading
from collections import deque
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

_WS = re.compile(r"\s+")
_ENTITY = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+|https?://[^\s\"'<>]+|[A-Za-z]:\\[^\s\"'<>]+")
_MIN_LEN = 4
_MAX_TOTAL_CHARS = 4_000_000


def _norm(text: str) -> str:
    return _WS.sub(" ", text.casefold()).strip()


def _domain(value: str) -> str | None:
    if "://" not in value:
        return None
    try:
        host = urlparse(value).hostname
    except ValueError:
        return None
    return host.lower() if host else None


@dataclass
class TaintHit:
    arg: str
    value: str
    sources: list[str]


class TaintTracker:
    def __init__(self) -> None:
        self._untrusted: deque[tuple[str, str]] = deque()
        self._untrusted_chars = 0
        self._trusted: list[str] = []
        self._confirmed: set[str] = set()
        self._own: set[str] = set()
        self._local: deque[tuple[str, str]] = deque(maxlen=200)
        self._lock = threading.Lock()

    def record_untrusted(self, text: str, source: str) -> None:
        if not text:
            return
        norm = _norm(text)
        with self._lock:
            self._untrusted.append((source, norm))
            self._untrusted_chars += len(norm)
            while self._untrusted_chars > _MAX_TOTAL_CHARS and self._untrusted:
                _, old = self._untrusted.popleft()
                self._untrusted_chars -= len(old)

    def record_local(self, text: str, data_class: str) -> None:
        """Private local content (file contents, memory, clipboard, screen text)."""
        if text and len(text) >= 12:
            with self._lock:
                self._local.append((data_class, _norm(text)[:200_000]))

    def local_sources_of(self, value: str) -> list[str]:
        """Data classes of private local content that ``value`` carries (min 24 chars overlap)."""
        norm = _norm(value)
        if len(norm) < 24:
            return []
        hits: set[str] = set()
        with self._lock:
            probes = [norm] if len(norm) <= 200 else [norm[i : i + 60] for i in range(0, min(len(norm), 3000), 60)]
            for dc, text in self._local:
                if any(len(p) >= 24 and p in text for p in probes):
                    hits.add(dc)
        return sorted(hits)

    def record_trusted(self, text: str) -> None:
        """User-authored text (objective, clarifications, approvals)."""
        if text:
            with self._lock:
                self._trusted.append(_norm(text))

    def confirm(self, value: str) -> None:
        """The user explicitly confirmed this value (e.g. picked a recipient)."""
        with self._lock:
            self._confirmed.add(_norm(value))

    def record_own(self, values: list[str]) -> None:
        """Argument values of actions that already passed the taint check. External output that later echoes
        them (e.g. a terminal printing the command it ran) does not make them externally derived."""
        with self._lock:
            for v in values:
                n = _norm(v)
                if len(n) >= _MIN_LEN:
                    self._own.add(n)

    def _in_trusted(self, norm: str) -> bool:
        if norm in self._confirmed or norm in self._own:
            return True
        return any(norm in t for t in self._trusted)

    def sources_of(self, value: str) -> list[str]:
        """Untrusted sources containing ``value`` if it is not user-provided; else []."""
        norm = _norm(value)
        if len(norm) < _MIN_LEN:
            return []
        with self._lock:
            if self._in_trusted(norm):
                return []
            hits = [src for src, text in self._untrusted if norm in text]
            if hits:
                return sorted(set(hits))
            domain = _domain(value)
            if domain and not any(domain in t for t in self._trusted) and domain not in self._confirmed:
                dhits = [src for src, text in self._untrusted if domain in text]
                if dhits:
                    return sorted(set(dhits))
            # long free text: tainted if a substantial sentence was lifted from untrusted content
            if len(norm) >= 80:
                for sentence in re.split(r"(?<=[.!?])\s+", norm):
                    if len(sentence) >= 40 and not self._in_trusted(sentence):
                        shits = [src for src, text in self._untrusted if sentence in text]
                        if shits:
                            return sorted(set(shits))
        return []

    def check_args(self, args: Mapping[str, Any], sensitive: Iterable[str] | None = None) -> list[TaintHit]:
        names = set(sensitive) if sensitive is not None else None
        hits: list[TaintHit] = []
        for name, value in _flatten(args):
            top = name.split(".", 1)[0].split("[", 1)[0]
            if names is not None and top not in names:
                continue
            if isinstance(value, str):
                src = self.sources_of(value)
                if not src:
                    # free text: check embedded entities (emails, URLs, Windows paths) individually
                    for ent in _ENTITY.findall(value):
                        src = self.sources_of(ent)
                        if src:
                            value = ent
                            break
                if src:
                    hits.append(TaintHit(name, value[:120], src))
        return hits

    @property
    def untrusted_chars(self) -> int:
        return self._untrusted_chars


def _flatten(obj: Any, prefix: str = "") -> Iterable[tuple[str, Any]]:
    if isinstance(obj, Mapping):
        for k, v in obj.items():
            yield from _flatten(v, f"{prefix}.{k}" if prefix else str(k))
    elif isinstance(obj, list | tuple):
        for i, v in enumerate(obj):
            yield from _flatten(v, f"{prefix}[{i}]")
    else:
        yield prefix, obj
