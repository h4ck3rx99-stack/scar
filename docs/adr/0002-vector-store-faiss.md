# ADR 0002: Vector store: FAISS (faiss-cpu) over SQLite-stored embeddings

Status: accepted (2026-09-26)

## Decision

Embeddings are stored as float32 BLOBs in the `memories` table; an in-memory `faiss.IndexFlatIP` is rebuilt lazily when the table changes. Keyword search uses SQLite FTS5 (BM25). Retrieval fuses both, weighted by importance and recency.

## Alternatives and rationale

sqlite-vec was the alternative. FAISS was chosen because it already exists in the user's environment (directive A6), has Windows wheels for Python 3.11 (1.15.1 verified), and personal memory stays small enough that a flat index is exact and fast. The SQLite table stays the source of truth, so switching is a local change.
