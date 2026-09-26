"""Metrics persisted in SQLite and summarised by ``scar status``."""

from __future__ import annotations

import json
import threading
import time
from collections import deque
from typing import Any

from scar.storage.db import Database, now_iso


class Metrics:
    """Buffered metric writer; flushes in batches to avoid a DB write per sample."""

    def __init__(self, db: Database | None, flush_every: int = 50) -> None:
        self.db = db
        self.flush_every = flush_every
        self._buf: deque[tuple[str, str, float, str]] = deque(maxlen=5000)
        self._lock = threading.Lock()
        self._counters: dict[str, float] = {}

    def record(self, name: str, value: float, **labels: Any) -> None:
        with self._lock:
            self._buf.append((now_iso(), name, float(value), json.dumps(labels, default=str, sort_keys=True)))
            self._counters[name] = self._counters.get(name, 0.0) + float(value)
            should_flush = len(self._buf) >= self.flush_every
        if should_flush:
            self.flush()

    def incr(self, name: str, **labels: Any) -> None:
        self.record(name, 1.0, **labels)

    def timer(self, name: str, **labels: Any) -> _Timer:
        return _Timer(self, name, labels)

    def flush(self) -> None:
        if self.db is None:
            return
        with self._lock:
            rows = list(self._buf)
            self._buf.clear()
        if rows:
            self.db.executemany("INSERT INTO metrics(at, name, value, labels_json) VALUES (?,?,?,?)", rows)

    def session_counters(self) -> dict[str, float]:
        with self._lock:
            return dict(self._counters)

    def summary(self, since_iso: str | None = None) -> list[dict[str, Any]]:
        if self.db is None:
            return []
        self.flush()
        where = "WHERE at >= ?" if since_iso else ""
        params: tuple[Any, ...] = (since_iso,) if since_iso else ()
        return self.db.query(
            f"SELECT name, count(*) AS n, sum(value) AS total, avg(value) AS mean, max(value) AS max FROM metrics {where} "
            "GROUP BY name ORDER BY name",
            params,
        )

    def prune(self, keep_days: int = 30) -> int:
        if self.db is None:
            return 0
        return self.db.execute("DELETE FROM metrics WHERE at < datetime('now', ?)", (f"-{keep_days} days",))


class _Timer:
    def __init__(self, metrics: Metrics, name: str, labels: dict[str, Any]) -> None:
        self.metrics = metrics
        self.name = name
        self.labels = labels
        self.start = 0.0

    def __enter__(self) -> _Timer:
        self.start = time.perf_counter()
        return self

    def __exit__(self, *exc: object) -> None:
        self.metrics.record(self.name, (time.perf_counter() - self.start) * 1000.0, **self.labels)
