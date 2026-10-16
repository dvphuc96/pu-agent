"""Idempotent schema migrations for durable operational persistence."""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    sql: str


MIGRATIONS = (
    Migration(
        9001,
        "workflow_checkpoints_agentops_and_durable_audit",
        r'''
CREATE TABLE IF NOT EXISTS workflow_runs (
    id TEXT PRIMARY KEY,
    workflow_type TEXT NOT NULL,
    status TEXT NOT NULL,
    request_json TEXT NOT NULL,
    current_stage TEXT,
    report_id TEXT,
    error TEXT,
    attempt INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    completed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_workflow_runs_status_updated
ON workflow_runs(status, updated_at DESC);

CREATE TABLE IF NOT EXISTS workflow_stage_checkpoints (
    workflow_id TEXT NOT NULL REFERENCES workflow_runs(id) ON DELETE CASCADE,
    stage_name TEXT NOT NULL,
    status TEXT NOT NULL,
    result_json TEXT,
    error TEXT,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(workflow_id, stage_name)
);
CREATE INDEX IF NOT EXISTS idx_workflow_checkpoints_status
ON workflow_stage_checkpoints(status, updated_at DESC);

CREATE TABLE IF NOT EXISTS agent_events (
    id TEXT PRIMARY KEY,
    schema_version INTEGER NOT NULL,
    kind TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    trace_id TEXT,
    request_id TEXT,
    workflow_id TEXT,
    agent_id TEXT,
    tool_call_id TEXT,
    execution_id TEXT,
    approval_id TEXT,
    report_id TEXT,
    severity TEXT NOT NULL DEFAULT 'info',
    status TEXT,
    duration_ms INTEGER,
    payload_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_agent_events_kind_time
ON agent_events(kind, occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_agent_events_workflow_time
ON agent_events(workflow_id, occurred_at ASC);
CREATE INDEX IF NOT EXISTS idx_agent_events_execution
ON agent_events(execution_id, occurred_at ASC);

CREATE TABLE IF NOT EXISTS tool_audit_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    execution_id TEXT NOT NULL,
    request_id TEXT,
    workflow_id TEXT,
    agent_id TEXT,
    tool_call_id TEXT,
    actor TEXT NOT NULL,
    principal TEXT,
    tool TEXT NOT NULL,
    capability TEXT NOT NULL,
    required_scope TEXT NOT NULL,
    side_effect INTEGER NOT NULL,
    external INTEGER NOT NULL,
    action_hash TEXT NOT NULL,
    status TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    policy TEXT,
    reason TEXT,
    latency_ms INTEGER,
    result_summary TEXT,
    error TEXT,
    record_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tool_audit_execution
ON tool_audit_events(execution_id, id);
CREATE INDEX IF NOT EXISTS idx_tool_audit_status_time
ON tool_audit_events(status, timestamp DESC);
''',
    ),
    Migration(
        9002,
        "customer_research_workspaces",
        r'''
CREATE TABLE IF NOT EXISTS research_workspaces (
    id TEXT PRIMARY KEY,
    company TEXT NOT NULL,
    website TEXT NOT NULL DEFAULT '',
    contact TEXT NOT NULL DEFAULT '',
    active_report_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS workspace_sources (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES research_workspaces(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    source_type TEXT NOT NULL,
    content TEXT NOT NULL,
    url TEXT NOT NULL DEFAULT '',
    published_at TEXT,
    enabled INTEGER NOT NULL DEFAULT 1,
    fingerprint TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    UNIQUE(workspace_id, fingerprint)
);
CREATE TABLE IF NOT EXISTS workspace_reports (
    report_id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES research_workspaces(id) ON DELETE CASCADE,
    content_hash TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    source_map_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS workspace_messages (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES research_workspaces(id) ON DELETE CASCADE,
    question TEXT NOT NULL,
    answer TEXT NOT NULL,
    source_ids_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS workspace_deliveries (
    action_hash TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES research_workspaces(id) ON DELETE CASCADE,
    report_id TEXT NOT NULL,
    destination TEXT NOT NULL,
    result_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_workspace_sources ON workspace_sources(workspace_id, created_at);
CREATE INDEX IF NOT EXISTS idx_workspace_reports ON workspace_reports(workspace_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_workspace_messages ON workspace_messages(workspace_id, created_at);
''',
    ),
    Migration(
        9003,
        "automatic_partner_briefing",
        r'''
CREATE TABLE IF NOT EXISTS briefing_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS briefing_seen (
    gmail_id TEXT PRIMARY KEY,
    domain TEXT NOT NULL,
    reason TEXT NOT NULL,
    seen_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS briefing_jobs (
    id TEXT PRIMARY KEY,
    fingerprint TEXT NOT NULL UNIQUE,
    workspace_id TEXT NOT NULL REFERENCES research_workspaces(id),
    status TEXT NOT NULL DEFAULT 'queued',
    stage TEXT NOT NULL DEFAULT 'queued',
    workflow_id TEXT,
    report_id TEXT,
    email_ids_json TEXT NOT NULL,
    error TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_briefing_jobs_status ON briefing_jobs(status,created_at);
''',
    ),
    Migration(
        9004,
        "bounded_briefing_retries",
        r'''
ALTER TABLE briefing_jobs ADD COLUMN attempts INTEGER NOT NULL DEFAULT 0;
ALTER TABLE briefing_jobs ADD COLUMN next_attempt TEXT NOT NULL DEFAULT '';
''',
    ),
)


class SQLiteMigrationRunner:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def apply(self) -> list[int]:
        self.conn.execute(
            """CREATE TABLE IF NOT EXISTS schema_migrations (
                   version INTEGER PRIMARY KEY,
                   name TEXT NOT NULL,
                   applied_at TEXT NOT NULL
               )"""
        )
        applied = {r[0] for r in self.conn.execute("SELECT version FROM schema_migrations")}
        newly_applied: list[int] = []
        for migration in MIGRATIONS:
            if migration.version in applied:
                continue
            self.conn.executescript(migration.sql)
            self.conn.execute(
                "INSERT INTO schema_migrations(version, name, applied_at) VALUES(?,?,?)",
                (
                    migration.version,
                    migration.name,
                    datetime.now(UTC).isoformat(timespec="milliseconds"),
                ),
            )
            newly_applied.append(migration.version)
        self.conn.commit()
        return newly_applied
