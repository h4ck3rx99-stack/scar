"""Versioned schema migrations. Append new versions; never edit an applied one."""

from __future__ import annotations

MIGRATIONS: list[tuple[int, str, str]] = [
    (
        1,
        "initial schema",
        """
CREATE TABLE IF NOT EXISTS tasks (
    task_id TEXT PRIMARY KEY,
    parent_task_id TEXT,
    objective TEXT NOT NULL,
    origin TEXT NOT NULL,
    status TEXT NOT NULL,
    autonomy_level INTEGER NOT NULL,
    role TEXT NOT NULL DEFAULT 'executor',
    background INTEGER NOT NULL DEFAULT 0,
    result_summary TEXT NOT NULL DEFAULT '',
    state_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    finished_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_tasks_parent ON tasks(parent_task_id);

CREATE TABLE IF NOT EXISTS steps (
    task_id TEXT NOT NULL,
    idx INTEGER NOT NULL,
    intent TEXT NOT NULL,
    success_criteria TEXT NOT NULL DEFAULT '',
    done INTEGER NOT NULL DEFAULT 0,
    revision INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (task_id, idx, revision)
);

CREATE TABLE IF NOT EXISTS tool_calls (
    action_id TEXT PRIMARY KEY,
    task_id TEXT,
    tool TEXT NOT NULL,
    args_json TEXT NOT NULL,
    args_hash TEXT NOT NULL,
    risk TEXT NOT NULL,
    tainted TEXT NOT NULL DEFAULT '[]',
    decision TEXT,
    decision_reason TEXT NOT NULL DEFAULT '',
    status TEXT,
    summary TEXT NOT NULL DEFAULT '',
    error_type TEXT,
    verified INTEGER,
    duration_ms INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tool_calls_task ON tool_calls(task_id);

CREATE TABLE IF NOT EXISTS approvals (
    request_id TEXT PRIMARY KEY,
    task_id TEXT,
    tool TEXT NOT NULL,
    args_hash TEXT NOT NULL,
    risk TEXT NOT NULL,
    description TEXT NOT NULL,
    decision TEXT,
    scope TEXT,
    channel TEXT,
    created_at TEXT NOT NULL,
    resolved_at TEXT
);

CREATE TABLE IF NOT EXISTS grants (
    grant_id TEXT PRIMARY KEY,
    effect TEXT NOT NULL,
    kind TEXT NOT NULL,
    tool TEXT,
    match_json TEXT NOT NULL DEFAULT '{}',
    args_hash TEXT,
    scope TEXT NOT NULL,
    task_id TEXT,
    session_id TEXT,
    max_risk TEXT NOT NULL DEFAULT 'HIGH',
    expires_at TEXT,
    created_at TEXT NOT NULL,
    revoked_at TEXT,
    uses INTEGER NOT NULL DEFAULT 0,
    created_via TEXT NOT NULL DEFAULT 'cli'
);

CREATE TABLE IF NOT EXISTS audit_log (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    at TEXT NOT NULL,
    kind TEXT NOT NULL,
    task_id TEXT,
    detail_json TEXT NOT NULL,
    prev_hash TEXT NOT NULL,
    hash TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS audit_no_update BEFORE UPDATE ON audit_log
BEGIN SELECT RAISE(ABORT, 'audit_log is append-only'); END;
CREATE TRIGGER IF NOT EXISTS audit_no_delete BEFORE DELETE ON audit_log
BEGIN SELECT RAISE(ABORT, 'audit_log is append-only'); END;

CREATE TABLE IF NOT EXISTS memories (
    id TEXT PRIMARY KEY,
    category TEXT NOT NULL,
    text TEXT NOT NULL,
    key TEXT,
    project TEXT,
    importance REAL NOT NULL DEFAULT 0.5,
    source TEXT NOT NULL,
    trust TEXT NOT NULL,
    embedding BLOB,
    embed_model TEXT,
    access_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    expires_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_memories_key ON memories(key);
CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(text, key, content='memories', content_rowid='rowid');
CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
  INSERT INTO memories_fts(rowid, text, key) VALUES (new.rowid, new.text, coalesce(new.key, ''));
END;
CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
  INSERT INTO memories_fts(memories_fts, rowid, text, key) VALUES ('delete', old.rowid, old.text, coalesce(old.key, ''));
END;
CREATE TRIGGER IF NOT EXISTS memories_au AFTER UPDATE OF text, key ON memories BEGIN
  INSERT INTO memories_fts(memories_fts, rowid, text, key) VALUES ('delete', old.rowid, old.text, coalesce(old.key, ''));
  INSERT INTO memories_fts(rowid, text, key) VALUES (new.rowid, new.text, coalesce(new.key, ''));
END;

CREATE TABLE IF NOT EXISTS contacts (
    contact_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    emails_json TEXT NOT NULL DEFAULT '[]',
    phones_json TEXT NOT NULL DEFAULT '[]',
    handles_json TEXT NOT NULL DEFAULT '{}',
    source TEXT NOT NULL,
    last_seen TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS contacts_aliases (
    alias TEXT PRIMARY KEY,
    contact_id TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS schedules (
    schedule_id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    text TEXT NOT NULL,
    objective TEXT,
    next_run TEXT NOT NULL,
    interval_seconds REAL,
    tz TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    last_run TEXT,
    fired_count INTEGER NOT NULL DEFAULT 0,
    missed_count INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS monitors (
    monitor_id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    target_json TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    last_event TEXT,
    last_event_json TEXT,
    task_id TEXT
);

CREATE TABLE IF NOT EXISTS provider_health (
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    state TEXT NOT NULL,
    failures INTEGER NOT NULL DEFAULT 0,
    cooldown_until TEXT,
    last_error TEXT,
    latency_ewma_ms REAL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (provider, model)
);

CREATE TABLE IF NOT EXISTS metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    at TEXT NOT NULL,
    name TEXT NOT NULL,
    value REAL NOT NULL,
    labels_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_metrics_name ON metrics(name, at);

CREATE TABLE IF NOT EXISTS artifacts (
    ref TEXT PRIMARY KEY,
    task_id TEXT,
    path TEXT NOT NULL,
    size INTEGER NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS conversation (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_conversation_session ON conversation(session_id, id);

CREATE TABLE IF NOT EXISTS calendar_events (
    event_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    start TEXT NOT NULL,
    end TEXT NOT NULL,
    location TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    attendees_json TEXT NOT NULL DEFAULT '[]',
    reminder_minutes INTEGER,
    uid TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS data_egress (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    provider TEXT NOT NULL,
    data_class TEXT NOT NULL,
    at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS kv (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
""",
    ),
]
