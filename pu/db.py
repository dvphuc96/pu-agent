"""One SQLite file (state.db) holds everything PU remembers and does.

This mirrors the Hermes approach on the whiteboard: SQLite + FTS5, no server.
Open it yourself anytime:  sqlite3 .pu/state.db '.tables'
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from pu.persistence import SQLiteDatabase, SQLiteMigrationRunner

SCHEMA = """
-- Flagship-task artifact: events the calendar tool creates. The deterministic
-- eval asserts directly on rows in this table ("did the meeting trigger?").
CREATE TABLE IF NOT EXISTS calendar_events (
    id INTEGER PRIMARY KEY,
    title TEXT NOT NULL,
    start TEXT NOT NULL,           -- ISO 8601
    "end" TEXT,
    attendees TEXT DEFAULT '',     -- comma-separated
    notes TEXT DEFAULT '',
    created_at TEXT DEFAULT (datetime('now'))
);

-- Semantic memory: durable facts about you, your people, your projects.
CREATE TABLE IF NOT EXISTS facts (
    id INTEGER PRIMARY KEY,
    subject TEXT NOT NULL,         -- who/what the fact is about, e.g. 'alex'
    content TEXT NOT NULL,         -- the fact itself
    source TEXT DEFAULT 'user',    -- 'user' (told directly) or 'consolidation'
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE VIRTUAL TABLE IF NOT EXISTS facts_fts USING fts5(
    subject, content, content=facts, content_rowid=id
);
CREATE TRIGGER IF NOT EXISTS facts_ai AFTER INSERT ON facts BEGIN
    INSERT INTO facts_fts(rowid, subject, content) VALUES (new.id, new.subject, new.content);
END;
CREATE TRIGGER IF NOT EXISTS facts_ad AFTER DELETE ON facts BEGIN
    INSERT INTO facts_fts(facts_fts, rowid, subject, content) VALUES ('delete', old.id, old.subject, old.content);
END;
CREATE TRIGGER IF NOT EXISTS facts_au AFTER UPDATE ON facts BEGIN
    INSERT INTO facts_fts(facts_fts, rowid, subject, content) VALUES ('delete', old.id, old.subject, old.content);
    INSERT INTO facts_fts(rowid, subject, content) VALUES (new.id, new.subject, new.content);
END;

-- Episodic memory: dated things that happened (past chats, distilled).
CREATE TABLE IF NOT EXISTS episodes (
    id INTEGER PRIMARY KEY,
    happened_at TEXT NOT NULL,     -- ISO 8601 date of the episode
    summary TEXT NOT NULL,
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE VIRTUAL TABLE IF NOT EXISTS episodes_fts USING fts5(
    summary, content=episodes, content_rowid=id
);
CREATE TRIGGER IF NOT EXISTS episodes_ai AFTER INSERT ON episodes BEGIN
    INSERT INTO episodes_fts(rowid, summary) VALUES (new.id, new.summary);
END;
CREATE TRIGGER IF NOT EXISTS episodes_ad AFTER DELETE ON episodes BEGIN
    INSERT INTO episodes_fts(episodes_fts, rowid, summary) VALUES ('delete', old.id, old.summary);
END;

-- Raw chat log ("save the messages" box). Consolidation reads from here.
-- session_id tags each row with which conversation it belongs to, so the
-- dashboard can offer "New chat" and switch between past sessions (like a
-- chat app). Everything shares this one table — sessions are just a label.
CREATE TABLE IF NOT EXISTS chat_log (
    id INTEGER PRIMARY KEY,
    role TEXT NOT NULL,            -- 'user' | 'assistant'
    content TEXT NOT NULL,
    consolidated INTEGER DEFAULT 0,
    session_id TEXT DEFAULT 'default',
    created_at TEXT DEFAULT (datetime('now'))
);

-- CRM inbox: email pulled read-only from Gmail on demand (see
-- pu/tools/gmail.py). Keyed by Gmail's message id so a sync is an upsert —
-- re-pulling the same inbox never duplicates. Read-only end to end: nothing
-- here is ever sent back to Gmail.
CREATE TABLE IF NOT EXISTS crm_emails (
    gmail_id TEXT PRIMARY KEY,
    thread_id TEXT DEFAULT '',
    sender_name TEXT DEFAULT '',
    sender_email TEXT DEFAULT '',
    subject TEXT DEFAULT '',
    snippet TEXT DEFAULT '',
    body_text TEXT DEFAULT '',     -- plain text, truncated (see gmail.py)
    internal_date TEXT NOT NULL,   -- ISO 8601, the moment Gmail received it
    unread INTEGER DEFAULT 1,
    synced_at TEXT DEFAULT (datetime('now'))
);

-- Knowledge: documents the user teaches PU (pasted text, uploaded files,
-- the watched .pu/knowledge/ folder, Google Drive imports — see
-- pu/memory/knowledge.py). Sources are metadata; chunks are the searchable
-- text (paragraph-sized). The FTS index mirrors chunks exactly like facts_fts
-- mirrors facts; a disabled source keeps its chunks but they are filtered at
-- query time, so re-enabling is instant.
CREATE TABLE IF NOT EXISTS knowledge_sources (
    id INTEGER PRIMARY KEY,
    title TEXT NOT NULL,
    "group" TEXT DEFAULT 'Ungrouped',
    origin TEXT DEFAULT 'paste',   -- paste | upload | folder | drive
    path TEXT DEFAULT '',          -- where it came from (folder/drive/upload name)
    fingerprint TEXT DEFAULT '',   -- sha1 of content — reindex skips unchanged files
    enabled INTEGER DEFAULT 1,
    chars INTEGER DEFAULT 0,
    added_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS knowledge_chunks (
    id INTEGER PRIMARY KEY,
    source_id INTEGER NOT NULL REFERENCES knowledge_sources(id) ON DELETE CASCADE,
    content TEXT NOT NULL
);
CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_fts USING fts5(
    content, content=knowledge_chunks, content_rowid=id
);
CREATE TRIGGER IF NOT EXISTS knowledge_ai AFTER INSERT ON knowledge_chunks BEGIN
    INSERT INTO knowledge_fts(rowid, content) VALUES (new.id, new.content);
END;
CREATE TRIGGER IF NOT EXISTS knowledge_ad AFTER DELETE ON knowledge_chunks BEGIN
    INSERT INTO knowledge_fts(knowledge_fts, rowid, content) VALUES ('delete', old.id, old.content);
END;
CREATE TRIGGER IF NOT EXISTS knowledge_au AFTER UPDATE ON knowledge_chunks BEGIN
    INSERT INTO knowledge_fts(knowledge_fts, rowid, content) VALUES ('delete', old.id, old.content);
    INSERT INTO knowledge_fts(rowid, content) VALUES (new.id, new.content);
END;

-- Human approval: one durable row per exact proposed side-effect payload.
CREATE TABLE IF NOT EXISTS proposed_actions (
    id TEXT PRIMARY KEY,
    tool_name TEXT NOT NULL,
    arguments_json TEXT NOT NULL,
    action_hash TEXT NOT NULL,
    status TEXT NOT NULL,
    principal TEXT NOT NULL,
    agent_id TEXT,
    request_id TEXT,
    workflow_id TEXT,
    tool_call_id TEXT,
    capability TEXT NOT NULL,
    required_scope TEXT NOT NULL,
    side_effect INTEGER NOT NULL DEFAULT 1,
    external INTEGER NOT NULL DEFAULT 0,
    risk TEXT NOT NULL DEFAULT 'medium',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    approved_action_hash TEXT,
    decision_note TEXT,
    decided_by TEXT,
    decided_at TEXT,
    execution_id TEXT,
    executed_at TEXT,
    result_summary TEXT,
    error TEXT,
    revision INTEGER NOT NULL DEFAULT 1,
    metadata_json TEXT DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_proposed_actions_status_created
ON proposed_actions(status, created_at DESC);
"""


def _migrate(conn: sqlite3.Connection) -> None:
    """Additive, idempotent column upgrades for databases created before a
    column existed. SQLite has no 'ADD COLUMN IF NOT EXISTS', so we check."""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(chat_log)").fetchall()}
    if "session_id" not in cols:
        conn.execute("ALTER TABLE chat_log ADD COLUMN session_id TEXT DEFAULT 'default'")
        conn.commit()
    if "source" not in cols:
        # which supported surface a message came in through (cli / dashboard)
        conn.execute("ALTER TABLE chat_log ADD COLUMN source TEXT DEFAULT 'cli'")
        conn.commit()
    if "meta" not in cols:
        # per-turn telemetry as JSON on the assistant row (gate decision,
        # latency, iterations, tools) — so reopening a thread still shows how
        # each answer was produced, not just the plain text.
        conn.execute("ALTER TABLE chat_log ADD COLUMN meta TEXT")
        conn.commit()

    approval_cols = {r[1] for r in conn.execute("PRAGMA table_info(proposed_actions)").fetchall()}
    if approval_cols and "decided_by" not in approval_cols:
        conn.execute("ALTER TABLE proposed_actions ADD COLUMN decided_by TEXT")
        conn.commit()


def connect(home: Path, check_same_thread: bool = True) -> sqlite3.Connection:
    # Shared local connection retained for memory/session stores. Operational
    # repositories use SQLiteDatabase.connection() so each operation owns a
    # short-lived connection and survives process restarts cleanly.
    database = SQLiteDatabase.from_home(home)
    conn = database.connect(check_same_thread=check_same_thread)
    conn.executescript(SCHEMA)
    _migrate(conn)
    SQLiteMigrationRunner(conn).apply()
    return conn
