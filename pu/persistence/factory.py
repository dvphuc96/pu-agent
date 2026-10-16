"""Composition boundary for operational persistence.

All application code consumes repository contracts; only this module chooses
SQLite today. A PostgreSQL deployment adapter can replace this factory output
without changing agents, orchestrators, ToolHarness or approval policy.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pu.approval.repository import SQLiteApprovalRepository
from pu.persistence.database import SQLiteDatabase
from pu.persistence.events import SQLiteAgentEventRepository, SQLiteToolAuditSink
from pu.persistence.workflows import SQLiteWorkflowRepository
from pu.persistence.migrations import SQLiteMigrationRunner


@dataclass(frozen=True)
class OperationalPersistence:
    database: SQLiteDatabase
    workflows: SQLiteWorkflowRepository
    events: SQLiteAgentEventRepository
    tool_audit: SQLiteToolAuditSink
    approvals: SQLiteApprovalRepository


def build_operational_persistence(home: Path) -> OperationalPersistence:
    database = SQLiteDatabase.from_home(home)
    conn = database.connect(check_same_thread=False)
    try:
        SQLiteMigrationRunner(conn).apply()
    finally:
        conn.close()
    return OperationalPersistence(
        database=database,
        workflows=SQLiteWorkflowRepository(database),
        events=SQLiteAgentEventRepository(database),
        tool_audit=SQLiteToolAuditSink(database),
        approvals=SQLiteApprovalRepository(database),
    )
