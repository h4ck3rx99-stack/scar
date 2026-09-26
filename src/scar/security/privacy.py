"""Privacy routing per data class (C4.11)."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from enum import StrEnum

from scar.security.redaction import global_redactor
from scar.storage.db import Database, now_iso


class DataClass(StrEnum):
    GENERAL = "general"
    SCREEN = "screen"
    AUDIO = "audio"
    EMAIL = "email"
    MESSAGES = "messages"
    FILES = "files"
    CONTACTS = "contacts"
    MEMORY = "memory"
    CLIPBOARD = "clipboard"


class Routing(StrEnum):
    CLOUD_ALLOWED = "cloud_allowed"
    CLOUD_REDACTED = "cloud_redacted"
    LOCAL_ONLY = "local_only"


_MODES: dict[str, dict[DataClass, Routing]] = {
    "open": {dc: Routing.CLOUD_ALLOWED for dc in DataClass},
    "balanced": {
        DataClass.GENERAL: Routing.CLOUD_REDACTED,
        DataClass.SCREEN: Routing.CLOUD_REDACTED,
        DataClass.AUDIO: Routing.CLOUD_ALLOWED,
        DataClass.EMAIL: Routing.CLOUD_REDACTED,
        DataClass.MESSAGES: Routing.CLOUD_REDACTED,
        DataClass.FILES: Routing.CLOUD_REDACTED,
        DataClass.CONTACTS: Routing.CLOUD_REDACTED,
        DataClass.MEMORY: Routing.CLOUD_REDACTED,
        DataClass.CLIPBOARD: Routing.CLOUD_REDACTED,
    },
    "strict": {
        **{dc: Routing.LOCAL_ONLY for dc in DataClass},
        DataClass.GENERAL: Routing.CLOUD_REDACTED,
    },
}


@dataclass
class PrivacyPolicy:
    mode: str
    overrides: dict[str, str]

    def routing(self, data_class: DataClass | str) -> Routing:
        dc = DataClass(data_class)
        if dc.value in self.overrides:
            return Routing(self.overrides[dc.value])
        return _MODES.get(self.mode, _MODES["balanced"])[dc]

    def cloud_allowed(self, data_class: DataClass | str) -> bool:
        return self.routing(data_class) != Routing.LOCAL_ONLY

    def prepare_for_cloud(self, text: str, data_class: DataClass | str) -> str:
        """Secrets are always redacted before leaving the machine."""
        return global_redactor().redact(text)

    def most_restrictive(self, classes: list[str]) -> Routing:
        order = [Routing.CLOUD_ALLOWED, Routing.CLOUD_REDACTED, Routing.LOCAL_ONLY]
        worst = Routing.CLOUD_ALLOWED
        for c in classes or [DataClass.GENERAL.value]:
            r = self.routing(c)
            if order.index(r) > order.index(worst):
                worst = r
        return worst


class EgressTracker:
    """Records which cloud providers received which data classes this session."""

    def __init__(self, db: Database | None, session_id: str) -> None:
        self.db = db
        self.session_id = session_id
        self._seen: dict[str, set[str]] = {}
        self._lock = threading.Lock()

    def record(self, provider: str, data_classes: list[str]) -> None:
        new: list[str] = []
        with self._lock:
            seen = self._seen.setdefault(provider, set())
            for dc in data_classes or [DataClass.GENERAL.value]:
                if dc not in seen:
                    seen.add(dc)
                    new.append(dc)
        if self.db is not None:
            for dc in new:
                self.db.execute(
                    "INSERT INTO data_egress(session_id, provider, data_class, at) VALUES (?,?,?,?)",
                    (self.session_id, provider, dc, now_iso()),
                )

    def summary(self) -> dict[str, list[str]]:
        with self._lock:
            return {p: sorted(c) for p, c in self._seen.items()}
