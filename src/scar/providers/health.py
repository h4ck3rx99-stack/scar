"""Circuit breakers, cooldowns and latency tracking per provider/model (C6)."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime

from scar.providers.errors import ProviderError, ProviderErrorKind
from scar.storage.db import Database, now_iso

FAILURE_THRESHOLD = 3
OPEN_SECONDS = 60.0
QUOTA_COOLDOWN = 3600.0
AUTH_COOLDOWN = 10 * 365 * 86400.0  # until the credential changes (see forget_if_credential_changed)
UNAVAILABLE_RETRY = 60.0  # a server that is down (Ollama not started yet, local model crashed) is re-probed
EWMA_ALPHA = 0.3


@dataclass
class Health:
    provider: str
    model: str
    state: str = "closed"  # closed | open | half_open | unavailable | removed
    failures: int = 0
    cooldown_until: float = 0.0
    last_error: str = ""
    latency_ewma_ms: float | None = None
    config_fingerprint: str = ""

    def usable(self, now: float | None = None) -> bool:
        now = now or time.time()
        if self.state == "removed":
            return False
        if now < self.cooldown_until:
            return False
        if self.state == "unavailable":
            self.state = "half_open"  # cooldown over: probe again
        if self.state == "open":
            self.state = "half_open"
        return True

    @property
    def score(self) -> float:
        lat = self.latency_ewma_ms or 1500.0
        return 1.0 / (1.0 + lat / 1000.0) - 0.2 * self.failures


class HealthTracker:
    def __init__(self, db: Database | None = None) -> None:
        self.db = db
        self._h: dict[tuple[str, str], Health] = {}
        self._lock = threading.Lock()
        if db is not None:
            self._load()

    def _load(self) -> None:
        assert self.db is not None
        now = time.time()
        for r in self.db.query("SELECT * FROM provider_health"):
            until = datetime.fromisoformat(r["cooldown_until"]).timestamp() if r["cooldown_until"] else 0.0
            # persisted state only carries forward cooldowns (rate limit / quota); auth is re-checked each start
            if until > now and r["state"] not in ("unavailable",):
                self._h[(r["provider"], r["model"])] = Health(
                    r["provider"], r["model"], r["state"], int(r["failures"]), until, r["last_error"] or "",
                    r["latency_ewma_ms"],
                )

    def get(self, provider: str, model: str) -> Health:
        with self._lock:
            key = (provider, model)
            if key not in self._h:
                self._h[key] = Health(provider, model)
            return self._h[key]

    def all(self) -> list[Health]:
        with self._lock:
            return list(self._h.values())

    def success(self, provider: str, model: str, latency_ms: float) -> None:
        h = self.get(provider, model)
        with self._lock:
            h.state = "closed"
            h.failures = 0
            h.cooldown_until = 0.0
            h.latency_ewma_ms = latency_ms if h.latency_ewma_ms is None else (
                EWMA_ALPHA * latency_ms + (1 - EWMA_ALPHA) * h.latency_ewma_ms)
        self._persist(h)

    def failure(self, err: ProviderError, credential_fingerprint: str = "") -> Health:
        h = self.get(err.provider, err.model)
        now = time.time()
        with self._lock:
            h.last_error = str(err)[:300]
            k = err.kind
            if k == ProviderErrorKind.AUTH:
                h.state = "unavailable"
                h.cooldown_until = now + AUTH_COOLDOWN
                h.config_fingerprint = credential_fingerprint
            elif k == ProviderErrorKind.UNAVAILABLE:
                h.state = "unavailable"
                h.cooldown_until = now + UNAVAILABLE_RETRY
            elif k == ProviderErrorKind.RATE_LIMIT:
                h.cooldown_until = now + (err.retry_after if err.retry_after is not None else 20.0)
            elif k == ProviderErrorKind.QUOTA:
                h.cooldown_until = now + max(err.retry_after or 0.0, QUOTA_COOLDOWN)
            elif k == ProviderErrorKind.MODEL_UNAVAILABLE:
                h.state = "removed"
            elif k == ProviderErrorKind.TRANSIENT:
                h.failures += 1
                if h.failures >= FAILURE_THRESHOLD:
                    h.state = "open"
                    h.cooldown_until = now + OPEN_SECONDS
            elif k == ProviderErrorKind.MALFORMED:
                h.failures += 1
        self._persist(h)
        return h

    def forget_if_credential_changed(self, provider: str, fingerprint: str) -> None:
        """An auth failure holds until the key changes: a new key (e.g. `scar config set-secret` while the daemon
        runs) clears it immediately."""
        with self._lock:
            stale = [k for k, h in self._h.items() if k[0] == provider and h.state in ("unavailable", "half_open")
                     and h.config_fingerprint and h.config_fingerprint != fingerprint]
            for k in stale:
                del self._h[k]
        if stale and self.db is not None:
            self.db.execute("DELETE FROM provider_health WHERE provider = ?", (provider,))

    def mark_unavailable(self, provider: str, model: str, reason: str) -> None:
        h = self.get(provider, model)
        with self._lock:
            h.state = "unavailable"
            h.last_error = reason
        self._persist(h)

    def reset(self, provider: str | None = None) -> None:
        with self._lock:
            for key in list(self._h):
                if provider is None or key[0] == provider:
                    del self._h[key]
        if self.db is not None:
            if provider is None:
                self.db.execute("DELETE FROM provider_health")
            else:
                self.db.execute("DELETE FROM provider_health WHERE provider = ?", (provider,))

    def _persist(self, h: Health) -> None:
        if self.db is None:
            return
        until = datetime.fromtimestamp(h.cooldown_until, UTC).isoformat() if h.cooldown_until else None
        try:
            self.db.execute(
                "INSERT INTO provider_health(provider, model, state, failures, cooldown_until, last_error, latency_ewma_ms, "
                "updated_at) VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(provider, model) DO UPDATE SET state=excluded.state, "
                "failures=excluded.failures, cooldown_until=excluded.cooldown_until, last_error=excluded.last_error, "
                "latency_ewma_ms=excluded.latency_ewma_ms, updated_at=excluded.updated_at",
                (h.provider, h.model, h.state, h.failures, until, h.last_error, h.latency_ewma_ms, now_iso()),
            )
        except Exception:  # noqa: BLE001 - health persistence is best effort
            return
