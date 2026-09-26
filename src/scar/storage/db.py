"""SQLite storage in WAL mode, accessed from a dedicated worker thread.

All statements run on one thread (the connection is not shared across
threads); async callers submit work through ``run``. Busy/locked errors are
retried with bounded backoff. A corrupt database is backed up and recreated,
and the event is reported, never silently swallowed.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import json
import shutil
import sqlite3
import threading
import time
from collections.abc import Callable, Iterable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TypeVar

from scar.core.errors import StorageError
from scar.storage.migrations import MIGRATIONS

T = TypeVar("T")

_LOCK_RETRIES = 6


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.recovered_from_corruption: str | None = None
        self._executor = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="scar-db")
        self._thread_local = threading.local()
        self._closed = False
        self._conn: sqlite3.Connection | None = None
        path.parent.mkdir(parents=True, exist_ok=True)
        self._executor.submit(self._open).result()

    # ----- connection management (db thread only) -----
    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.path), timeout=5.0, isolation_level=None, check_same_thread=True)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA busy_timeout=5000")
            ok = conn.execute("PRAGMA quick_check").fetchone()[0]
            if ok != "ok":
                raise sqlite3.DatabaseError(f"quick_check: {ok}")
        except sqlite3.DatabaseError:
            conn.close()
            raise
        return conn

    def _open(self) -> None:
        try:
            conn = self._connect()
        except sqlite3.DatabaseError as exc:
            conn = self._recover(str(exc))
        self._conn = conn
        self._migrate()

    def _recover(self, reason: str) -> sqlite3.Connection:
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        backup = self.path.with_name(f"{self.path.stem}.corrupt-{stamp}{self.path.suffix}")
        for suffix in ("", "-wal", "-shm"):
            src = Path(str(self.path) + suffix)
            if src.exists():
                shutil.move(str(src), str(backup) + suffix)
        self.recovered_from_corruption = f"database was corrupt ({reason}); backed up to {backup} and recreated"
        return self._connect()

    def _migrate(self) -> None:
        conn = self._require_conn()
        conn.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER PRIMARY KEY, name TEXT, applied_at TEXT)")
        applied = {row[0] for row in conn.execute("SELECT version FROM schema_version")}
        for version, name, sql in MIGRATIONS:
            if version in applied:
                continue
            conn.execute("BEGIN")
            try:
                for statement in _split_sql(sql):
                    conn.execute(statement)
                conn.execute(
                    "INSERT INTO schema_version(version, name, applied_at) VALUES (?, ?, ?)", (version, name, now_iso())
                )
                conn.execute("COMMIT")
            except sqlite3.Error as exc:
                conn.execute("ROLLBACK")
                raise StorageError(f"migration {version} ({name}) failed: {exc}") from exc

    def _require_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            raise StorageError("database is closed")
        return self._conn

    # ----- execution -----
    def _with_retry(self, fn: Callable[[sqlite3.Connection], T]) -> T:
        delay = 0.05
        for attempt in range(_LOCK_RETRIES):
            try:
                return fn(self._require_conn())
            except sqlite3.OperationalError as exc:
                msg = str(exc).lower()
                if ("locked" in msg or "busy" in msg) and attempt < _LOCK_RETRIES - 1:
                    time.sleep(delay)
                    delay = min(delay * 2, 1.0)
                    continue
                raise StorageError(str(exc)) from exc
        raise StorageError("unreachable")

    def call(self, fn: Callable[[sqlite3.Connection], T]) -> T:
        """Run ``fn(conn)`` on the DB thread, synchronously."""
        if self._closed:
            raise StorageError("database is closed")
        if threading.current_thread().name.startswith("scar-db"):
            return self._with_retry(fn)
        return self._executor.submit(self._with_retry, fn).result()

    async def run(self, fn: Callable[[sqlite3.Connection], T]) -> T:
        if self._closed:
            raise StorageError("database is closed")
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._executor, self._with_retry, fn)

    # convenience helpers (sync)
    def execute(self, sql: str, params: Sequence[Any] = ()) -> int:
        return self.call(lambda c: c.execute(sql, params).rowcount)

    def executemany(self, sql: str, rows: Iterable[Sequence[Any]]) -> None:
        materialised = list(rows)
        self.call(lambda c: c.executemany(sql, materialised))

    def query(self, sql: str, params: Sequence[Any] = ()) -> list[dict[str, Any]]:
        return self.call(lambda c: [dict(r) for r in c.execute(sql, params).fetchall()])

    def query_one(self, sql: str, params: Sequence[Any] = ()) -> dict[str, Any] | None:
        rows = self.call(lambda c: c.execute(sql, params).fetchone())
        return dict(rows) if rows is not None else None

    def transaction(self, fn: Callable[[sqlite3.Connection], T]) -> T:
        def wrapped(conn: sqlite3.Connection) -> T:
            conn.execute("BEGIN IMMEDIATE")
            try:
                result = fn(conn)
            except BaseException:
                conn.execute("ROLLBACK")
                raise
            conn.execute("COMMIT")
            return result

        return self.call(wrapped)

    # async helpers
    async def aexecute(self, sql: str, params: Sequence[Any] = ()) -> int:
        return await self.run(lambda c: c.execute(sql, params).rowcount)

    async def aquery(self, sql: str, params: Sequence[Any] = ()) -> list[dict[str, Any]]:
        return await self.run(lambda c: [dict(r) for r in c.execute(sql, params).fetchall()])

    # kv
    def kv_get(self, key: str) -> Any | None:
        row = self.query_one("SELECT value FROM kv WHERE key = ?", (key,))
        return json.loads(row["value"]) if row else None

    def kv_set(self, key: str, value: Any) -> None:
        self.execute(
            "INSERT INTO kv(key, value, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
            (key, json.dumps(value, default=str), now_iso()),
        )

    def integrity_check(self) -> str:
        return self.call(lambda c: c.execute("PRAGMA integrity_check").fetchone()[0])

    def schema_version(self) -> int:
        row = self.query_one("SELECT max(version) AS v FROM schema_version")
        return int(row["v"]) if row and row["v"] is not None else 0

    def close(self) -> None:
        if self._closed:
            return

        def _close() -> None:
            if self._conn is not None:
                try:
                    self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                except sqlite3.Error:
                    pass
                self._conn.close()
                self._conn = None

        try:
            self._executor.submit(_close).result(timeout=10)
        finally:
            self._closed = True
            self._executor.shutdown(wait=True)


def _split_sql(script: str) -> list[str]:
    """Split a migration script into statements, keeping trigger bodies intact."""
    statements: list[str] = []
    buf: list[str] = []
    for line in script.splitlines():
        buf.append(line)
        candidate = "\n".join(buf).strip()
        if candidate and sqlite3.complete_statement(candidate):
            statements.append(candidate)
            buf = []
    tail = "\n".join(buf).strip()
    if tail:
        statements.append(tail)
    return statements
