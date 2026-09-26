"""Long-term memory: SQLite + FTS5 (BM25) + FAISS vectors (local ONNX embeddings).

Retrieval is hybrid: keyword and vector scores are fused, then weighted by
importance and recency. Near-duplicates (similarity above a threshold, same
category) update the existing memory instead of adding a new one.
"""

from __future__ import annotations

import math
import re
import threading
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import numpy as np
import structlog

from scar.core.errors import CapabilityUnavailable
from scar.core.ids import new_id
from scar.memory.policy import EPISODIC_TTL_DAYS, check_write
from scar.storage.db import Database, now_iso

log = structlog.get_logger("scar.memory")

DEDUP_THRESHOLD = 0.92


@dataclass
class Memory:
    id: str
    category: str
    text: str
    key: str | None
    project: str | None
    importance: float
    source: str
    trust: str
    created_at: str
    updated_at: str
    score: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


def _fts_query(q: str) -> str:
    words = re.findall(r"[\w]+", q.lower())
    words = [w for w in words if len(w) > 1 and w not in {"the", "a", "an", "is", "my", "to", "of", "in", "and", "at", "for",
                                                          "what", "where", "which", "me", "i", "it", "on"}]
    return " OR ".join(f'"{w}"*' for w in words[:12])


class MemoryStore:
    def __init__(self, db: Database, embeddings: Any | None) -> None:
        self.db = db
        self.embeddings = embeddings
        self._index: Any = None
        self._ids: list[str] = []
        self._lock = threading.Lock()
        self._index_dirty = True

    # ------------------------------------------------------------------ vectors
    def _embed(self, texts: list[str]) -> np.ndarray | None:
        if self.embeddings is None or not getattr(self.embeddings, "enabled", False):
            return None
        try:
            return self.embeddings.embed_sync(texts)
        except CapabilityUnavailable as exc:
            log.warning("embeddings_unavailable", error=str(exc)[:200])
            return None

    def _ensure_index(self) -> None:
        with self._lock:
            if not self._index_dirty:
                return
            rows = self.db.query("SELECT id, embedding FROM memories WHERE embedding IS NOT NULL")
            if not rows:
                self._index, self._ids, self._index_dirty = None, [], False
                return
            import faiss

            vecs = np.vstack([np.frombuffer(r["embedding"], dtype=np.float32) for r in rows])
            index = faiss.IndexFlatIP(vecs.shape[1])
            index.add(vecs)
            self._index, self._ids, self._index_dirty = index, [r["id"] for r in rows], False

    def _vector_search(self, qvec: np.ndarray, k: int) -> dict[str, float]:
        self._ensure_index()
        if self._index is None or not self._ids:
            return {}
        scores, idx = self._index.search(qvec.reshape(1, -1).astype(np.float32), min(k, len(self._ids)))
        return {self._ids[i]: float(s) for s, i in zip(scores[0], idx[0], strict=False) if i >= 0}

    # ------------------------------------------------------------------ write
    def store(self, text: str, category: str = "semantic", *, key: str | None = None, project: str | None = None,
              importance: float = 0.5, source: str = "user", trust: str = "user", ttl_days: float | None = None) -> tuple[str, str]:
        """Store (or update a near-duplicate). Returns (memory_id, 'created'|'updated'). Raises ValueError if refused."""
        check = check_write(text, category)
        if not check.allowed:
            raise ValueError(check.reason)
        text = text.strip()
        now = now_iso()
        expires = None
        if category == "episodic" or ttl_days:
            expires = (datetime.now(UTC) + timedelta(days=ttl_days or EPISODIC_TTL_DAYS)).isoformat()
        vec = self._embed([text])
        # exact key replaces (aliases, project preferences)
        if key:
            row = self.db.query_one("SELECT id FROM memories WHERE key = ? AND category = ? AND coalesce(project,'') = ?",
                                    (key.lower(), category, project or ""))
            if row:
                self.db.execute("UPDATE memories SET text = ?, importance = max(importance, ?), updated_at = ?, embedding = ?, "
                                "source = ?, trust = ?, expires_at = ? WHERE id = ?",
                                (text, importance, now, vec[0].tobytes() if vec is not None else None, source, trust, expires, row["id"]))
                self._index_dirty = True
                return row["id"], "updated"
        # near-duplicate update
        if vec is not None:
            for mid, sim in self._vector_search(vec[0], 5).items():
                if sim < DEDUP_THRESHOLD:
                    continue
                row = self.db.query_one("SELECT category FROM memories WHERE id = ?", (mid,))
                if row and row["category"] == category:
                    self.db.execute("UPDATE memories SET text = ?, importance = max(importance, ?), updated_at = ?, "
                                    "embedding = ? WHERE id = ?", (text, importance, now, vec[0].tobytes(), mid))
                    self._index_dirty = True
                    return mid, "updated"
        else:
            dup = self.db.query_one("SELECT id FROM memories WHERE lower(text) = lower(?) AND category = ?", (text, category))
            if dup:
                self.db.execute("UPDATE memories SET updated_at = ? WHERE id = ?", (now, dup["id"]))
                return dup["id"], "updated"
        mid = new_id("mem")
        self.db.execute(
            "INSERT INTO memories(id, category, text, key, project, importance, source, trust, embedding, embed_model, "
            "created_at, updated_at, expires_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (mid, category, text, key.lower() if key else None, project, float(importance), source, trust,
             vec[0].tobytes() if vec is not None else None,
             getattr(self.embeddings, "model_name", None) if vec is not None else None, now, now, expires),
        )
        self._index_dirty = True
        return mid, "created"

    # ------------------------------------------------------------------ read
    def search(self, query: str, k: int = 8, categories: list[str] | None = None, project: str | None = None,
               min_score: float = 0.05) -> list[Memory]:
        self.expire()
        candidates: dict[str, dict[str, float]] = {}
        fts = _fts_query(query)
        if fts:
            try:
                rows = self.db.query("SELECT m.id, bm25(memories_fts) AS rank FROM memories_fts JOIN memories m ON "
                                     "m.rowid = memories_fts.rowid WHERE memories_fts MATCH ? ORDER BY rank LIMIT 50", (fts,))
            except Exception:  # noqa: BLE001 - malformed FTS query from unusual input: fall back to vectors
                rows = []
            if rows:
                ranks = [-r["rank"] for r in rows]  # bm25(): lower is better
                top = max(ranks) or 1.0
                for r, rank in zip(rows, ranks, strict=False):
                    candidates.setdefault(r["id"], {})["kw"] = max(0.0, rank / top)
        vec = self._embed([query])
        if vec is not None:
            for mid, sim in self._vector_search(vec[0], 30).items():
                candidates.setdefault(mid, {})["vec"] = max(0.0, sim)
        if not candidates:
            return []
        ids = list(candidates)
        rows = self.db.query(f"SELECT * FROM memories WHERE id IN ({','.join('?' * len(ids))})", ids)
        now = datetime.now(UTC)
        out: list[Memory] = []
        for r in rows:
            if categories and r["category"] not in categories:
                continue
            if project and r["project"] and r["project"].lower() != project.lower():
                continue
            c = candidates[r["id"]]
            base = 0.45 * c.get("kw", 0.0) + 0.55 * c.get("vec", 0.0) if vec is not None else c.get("kw", 0.0)
            age_days = max(0.0, (now - datetime.fromisoformat(r["updated_at"])).total_seconds() / 86400)
            recency = math.exp(-age_days / 90.0)
            score = base * (0.75 + 0.25 * recency) * (0.7 + 0.6 * float(r["importance"]))
            if score < min_score:
                continue
            out.append(Memory(r["id"], r["category"], r["text"], r["key"], r["project"], float(r["importance"]), r["source"],
                              r["trust"], r["created_at"], r["updated_at"], round(score, 4)))
        out.sort(key=lambda m: m.score, reverse=True)
        top = out[:k]
        if top:
            self.db.execute(f"UPDATE memories SET access_count = access_count + 1 WHERE id IN ({','.join('?' * len(top))})",
                            [m.id for m in top])
        return top

    def get(self, mid: str) -> Memory | None:
        r = self.db.query_one("SELECT * FROM memories WHERE id = ?", (mid,))
        if not r:
            return None
        return Memory(r["id"], r["category"], r["text"], r["key"], r["project"], float(r["importance"]), r["source"], r["trust"],
                      r["created_at"], r["updated_at"])

    def list(self, category: str | None = None, limit: int = 100) -> list[Memory]:
        rows = self.db.query("SELECT * FROM memories " + ("WHERE category = ? " if category else "") +
                             "ORDER BY updated_at DESC LIMIT ?", (category, limit) if category else (limit,))
        return [Memory(r["id"], r["category"], r["text"], r["key"], r["project"], float(r["importance"]), r["source"], r["trust"],
                       r["created_at"], r["updated_at"]) for r in rows]

    # ------------------------------------------------------------------ aliases & preferences
    def set_alias(self, alias: str, target: str, kind: str = "folder") -> str:
        mid, _ = self.store(f"{kind} alias '{alias}' -> {target}", "alias", key=f"{kind}:{_norm_alias(alias)}", importance=0.9)
        self.db.execute("UPDATE memories SET text = ? WHERE id = ?", (f"{kind} alias '{alias}' -> {target}", mid))
        return mid

    def aliases(self, kind: str) -> dict[str, str]:
        out: dict[str, str] = {}
        for r in self.db.query("SELECT key, text FROM memories WHERE category = 'alias' AND key LIKE ?", (f"{kind}:%",)):
            m = re.search(r"-> (.+)$", r["text"])
            if m:
                out[r["key"].split(":", 1)[1]] = m.group(1).strip()
        return out

    def resolve_alias(self, phrase: str, kind: str = "folder") -> str | None:
        n = _norm_alias(phrase)
        table = self.aliases(kind)
        if n in table:
            return table[n]
        for alias, target in table.items():
            if alias and (alias in n or n in alias):
                return target
        return None

    def set_project_preference(self, root: str, key: str, value: str) -> str:
        mid, _ = self.store(f"In project {root}: {key} = {value}", "project", key=f"pref:{key}", project=root.lower(),
                            importance=0.8)
        return mid

    def project_preference(self, root: str, key: str) -> str | None:
        r = self.db.query_one("SELECT text FROM memories WHERE category = 'project' AND key = ? AND project = ?",
                              (f"pref:{key}", root.lower()))
        if not r:
            return None
        m = re.search(r"= (.+)$", r["text"])
        return m.group(1).strip() if m else None

    # ------------------------------------------------------------------ delete
    def forget(self, mid: str | None = None, query: str | None = None, limit: int = 5) -> list[str]:
        if mid:
            n = self.db.execute("DELETE FROM memories WHERE id = ?", (mid,))
            self._index_dirty = True
            return [mid] if n else []
        if not query:
            return []
        hits = [m for m in self.search(query, k=limit, min_score=0.2)]
        if not hits:
            return []
        best = hits[0].score
        doomed = [m.id for m in hits if m.score >= best * 0.8]
        self.db.execute(f"DELETE FROM memories WHERE id IN ({','.join('?' * len(doomed))})", doomed)
        self._index_dirty = True
        return doomed

    def wipe(self) -> int:
        n = self.db.execute("DELETE FROM memories")
        self._index_dirty = True
        return n

    def expire(self) -> int:
        n = self.db.execute("DELETE FROM memories WHERE expires_at IS NOT NULL AND expires_at < ?", (now_iso(),))
        if n:
            self._index_dirty = True
        return n

    def count(self) -> int:
        row = self.db.query_one("SELECT count(*) AS n FROM memories")
        return int(row["n"]) if row else 0


def _norm_alias(s: str) -> str:
    s = s.lower().strip()
    s = re.sub(r"^(my|the|our)\s+", "", s)
    s = re.sub(r"\s+(folder|directory|dir|project|repo|repository)$", "", s)
    return re.sub(r"\s+", " ", s).strip()
