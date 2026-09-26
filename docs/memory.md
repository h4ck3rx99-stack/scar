# Memory

SCAR remembers durable, useful facts, not whole conversations.

| Category | Examples | Lifetime |
|---|---|---|
| `preference` | "preferred editor is VS Code" | until forgotten |
| `semantic` | facts you asked SCAR to remember | until forgotten |
| `project` | "use pnpm in C:\Projects\web" (per-project preferences) | until forgotten |
| `alias` | "my BISense folder" → `C:\Projects\BISense`; app and URL aliases | until forgotten |
| `episodic` | short-lived notes | 30 days (TTL) |
| `task_history` | one line per finished task | 30 days |
| conversation | the last turns of the current conversation (for follow-ups) | last 400 lines |
| working memory | the current task's state and observations | the task |

## How retrieval works

Memories live in SQLite (`memories` table) with an FTS5 index (BM25) and local embeddings
(fastembed `BAAI/bge-small-en-v1.5`, CPU) searched with FAISS (ADR 0002/0003). A query scores both, fuses them
(0.45 keyword + 0.55 vector), then weights by importance and recency (90-day half-life style decay). Before each
LLM task, the top few relevant memories are added to the model's context. Near-duplicates (cosine ≥ 0.92, same
category) update the existing memory instead of adding a new one.

## Write policy

* Never stores secrets or sensitive identifiers: API keys and tokens, passwords, Luhn-valid card numbers, SSNs,
  government ID mentions, and bank account markers are refused.
* In a task that has read external content (web, email, files, screen), a memory write that the user did not state
  requires the user's confirmation. This is non-grantable, so injected instructions can't plant memories.
* Memories are short (≤4000 characters).

## Using it

Say it:

* "Remember that my BISense folder is at C:\Projects\BISense"
* "Use pnpm in this project"
* "Forget my coffee order"

Or use the CLI:

```
scar memory list [--category alias]
scar memory search "bisense"
scar memory forget <mem_id | description>
scar memory wipe            # deletes all memories and the conversation history
```

Setting `SCAR_MEMORY_BACKEND=fts_only` disables embeddings entirely (keyword search only). Embeddings never
leave the machine.
