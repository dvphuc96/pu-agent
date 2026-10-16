"""Durable workflow runs and stage checkpoints."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any

from pu.core.interfaces.agents import AgentResult
from pu.core.interfaces.orchestration import ResearchWorkflowRequest
from pu.persistence.database import SQLiteDatabase


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def _agent_result_to_dict(result: AgentResult) -> dict[str, Any]:
    return {
        "reply": result.reply,
        "tool_calls": result.tool_calls,
        "iterations": result.iterations,
        "usage": result.usage,
    }


def _agent_result_from_dict(data: dict[str, Any]) -> AgentResult:
    return AgentResult(
        reply=data.get("reply", ""),
        tool_calls=list(data.get("tool_calls") or []),
        iterations=int(data.get("iterations") or 0),
        usage=dict(data.get("usage") or {"in": 0, "out": 0, "calls": 0}),
    )


@dataclass(frozen=True)
class WorkflowRun:
    id: str
    workflow_type: str
    status: str
    request: dict[str, Any]
    current_stage: str | None
    report_id: str | None
    error: str | None
    attempt: int
    created_at: str
    updated_at: str
    completed_at: str | None


@dataclass(frozen=True)
class WorkflowCheckpoint:
    workflow_id: str
    stage_name: str
    status: str
    result: AgentResult | None
    error: str | None
    updated_at: str


class SQLiteWorkflowRepository:
    """Connection-per-operation workflow store; safe across app restarts."""

    def __init__(self, database: SQLiteDatabase):
        self.database = database

    def create(self, workflow_id: str, request: ResearchWorkflowRequest) -> WorkflowRun:
        now = _now()
        payload = json.dumps(asdict(request), ensure_ascii=False, sort_keys=True)
        with self.database.connection() as conn:
            conn.execute(
                """INSERT INTO workflow_runs(
                       id, workflow_type, status, request_json, created_at, updated_at
                   ) VALUES(?, 'morning_research', 'running', ?, ?, ?)""",
                (workflow_id, payload, now, now),
            )
        return self.get(workflow_id)

    def get(self, workflow_id: str) -> WorkflowRun | None:
        with self.database.connection() as conn:
            row = conn.execute("SELECT * FROM workflow_runs WHERE id=?", (workflow_id,)).fetchone()
        if row is None:
            return None
        return WorkflowRun(
            id=row["id"], workflow_type=row["workflow_type"], status=row["status"],
            request=json.loads(row["request_json"]), current_stage=row["current_stage"],
            report_id=row["report_id"], error=row["error"], attempt=int(row["attempt"]),
            created_at=row["created_at"], updated_at=row["updated_at"],
            completed_at=row["completed_at"],
        )

    def request(self, workflow_id: str) -> ResearchWorkflowRequest:
        run = self.get(workflow_id)
        if run is None:
            raise KeyError("workflow not found")
        return ResearchWorkflowRequest(**run.request)

    def list(self, *, limit: int = 50, status: str | None = None) -> list[WorkflowRun]:
        sql = "SELECT id FROM workflow_runs"
        params: tuple[Any, ...] = ()
        if status:
            sql += " WHERE status=?"
            params = (status,)
        sql += " ORDER BY updated_at DESC LIMIT ?"
        params += (limit,)
        with self.database.connection() as conn:
            ids = [r[0] for r in conn.execute(sql, params).fetchall()]
        return [run for wid in ids if (run := self.get(wid)) is not None]

    def start_attempt(self, workflow_id: str) -> WorkflowRun:
        now = _now()
        with self.database.connection() as conn:
            cur = conn.execute(
                """UPDATE workflow_runs
                   SET status='running', error=NULL, attempt=attempt+1, updated_at=?
                   WHERE id=? AND status IN ('failed','interrupted','running')""",
                (now, workflow_id),
            )
            if cur.rowcount != 1:
                raise ValueError("workflow cannot be resumed from its current state")
        return self.get(workflow_id)

    def set_current_stage(self, workflow_id: str, stage_name: str) -> None:
        with self.database.connection() as conn:
            conn.execute(
                "UPDATE workflow_runs SET current_stage=?, updated_at=? WHERE id=?",
                (stage_name, _now(), workflow_id),
            )

    def save_checkpoint(self, workflow_id: str, stage_name: str, result: AgentResult) -> None:
        now = _now()
        payload = json.dumps(_agent_result_to_dict(result), ensure_ascii=False, default=str)
        with self.database.connection() as conn:
            conn.execute(
                """INSERT INTO workflow_stage_checkpoints(
                       workflow_id, stage_name, status, result_json, error, updated_at
                   ) VALUES(?, ?, 'completed', ?, NULL, ?)
                   ON CONFLICT(workflow_id, stage_name) DO UPDATE SET
                     status='completed', result_json=excluded.result_json,
                     error=NULL, updated_at=excluded.updated_at""",
                (workflow_id, stage_name, payload, now),
            )
            conn.execute(
                "UPDATE workflow_runs SET current_stage=?, updated_at=? WHERE id=?",
                (stage_name, now, workflow_id),
            )

    def checkpoint_failure(self, workflow_id: str, stage_name: str, error: str) -> None:
        now = _now()
        with self.database.connection() as conn:
            conn.execute(
                """INSERT INTO workflow_stage_checkpoints(
                       workflow_id, stage_name, status, result_json, error, updated_at
                   ) VALUES(?, ?, 'failed', NULL, ?, ?)
                   ON CONFLICT(workflow_id, stage_name) DO UPDATE SET
                     status='failed', error=excluded.error, updated_at=excluded.updated_at""",
                (workflow_id, stage_name, error, now),
            )

    def checkpoints(self, workflow_id: str) -> dict[str, WorkflowCheckpoint]:
        with self.database.connection() as conn:
            rows = conn.execute(
                "SELECT * FROM workflow_stage_checkpoints WHERE workflow_id=? ORDER BY updated_at",
                (workflow_id,),
            ).fetchall()
        out: dict[str, WorkflowCheckpoint] = {}
        for row in rows:
            data = json.loads(row["result_json"]) if row["result_json"] else None
            out[row["stage_name"]] = WorkflowCheckpoint(
                workflow_id=row["workflow_id"], stage_name=row["stage_name"],
                status=row["status"], result=_agent_result_from_dict(data) if data else None,
                error=row["error"], updated_at=row["updated_at"],
            )
        return out

    def mark_failed(self, workflow_id: str, error: str) -> WorkflowRun:
        with self.database.connection() as conn:
            conn.execute(
                "UPDATE workflow_runs SET status='failed', error=?, updated_at=? WHERE id=?",
                (error[:2000], _now(), workflow_id),
            )
        return self.get(workflow_id)

    def mark_completed(self, workflow_id: str, *, report_id: str | None = None) -> WorkflowRun:
        now = _now()
        with self.database.connection() as conn:
            conn.execute(
                """UPDATE workflow_runs
                   SET status='completed', report_id=?, error=NULL,
                       current_stage='completed', updated_at=?, completed_at=?
                   WHERE id=?""",
                (report_id, now, now, workflow_id),
            )
        return self.get(workflow_id)
