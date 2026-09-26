"""Append-only, hash-chained audit log (C3).

Rows cannot be updated or deleted (SQLite triggers), and each row's hash
covers the previous row's hash, so tampering is detectable with ``verify``.
"""

from __future__ import annotations

import hashlib
import json
import threading
from typing import Any

from scar.security.redaction import global_redactor
from scar.storage.db import Database, now_iso


class AuditLog:
    def __init__(self, db: Database) -> None:
        self.db = db
        self._lock = threading.Lock()

    def append(self, kind: str, detail: dict[str, Any], task_id: str | None = None) -> str:
        safe = global_redactor().redact_obj(detail)
        detail_json = json.dumps(safe, sort_keys=True, default=str, ensure_ascii=False)
        with self._lock:

            def tx(conn: Any) -> str:
                row = conn.execute("SELECT hash FROM audit_log ORDER BY seq DESC LIMIT 1").fetchone()
                prev = row[0] if row else "genesis"
                at = now_iso()
                digest = hashlib.sha256(f"{prev}|{at}|{kind}|{task_id or ''}|{detail_json}".encode()).hexdigest()
                conn.execute(
                    "INSERT INTO audit_log(at, kind, task_id, detail_json, prev_hash, hash) VALUES (?,?,?,?,?,?)",
                    (at, kind, task_id, detail_json, prev, digest),
                )
                return digest

            return self.db.transaction(tx)

    def verify(self) -> tuple[bool, int]:
        """Recompute the chain. Returns (intact, rows_checked)."""
        rows = self.db.query("SELECT at, kind, task_id, detail_json, prev_hash, hash FROM audit_log ORDER BY seq")
        prev = "genesis"
        for i, r in enumerate(rows):
            expected = hashlib.sha256(
                f"{prev}|{r['at']}|{r['kind']}|{r['task_id'] or ''}|{r['detail_json']}".encode()
            ).hexdigest()
            if r["prev_hash"] != prev or r["hash"] != expected:
                return False, i
            prev = r["hash"]
        return True, len(rows)

    def recent(self, limit: int = 50, kind: str | None = None) -> list[dict[str, Any]]:
        if kind:
            return self.db.query("SELECT * FROM audit_log WHERE kind = ? ORDER BY seq DESC LIMIT ?", (kind, limit))
        return self.db.query("SELECT * FROM audit_log ORDER BY seq DESC LIMIT ?", (limit,))
