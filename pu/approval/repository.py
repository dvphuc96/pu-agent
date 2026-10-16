"""SQLite persistence for proposed actions and approval decisions."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from typing import Any

from pu.approval.models import ApprovalStatus, ProposedAction


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def _status(value: str) -> ApprovalStatus:
    try:
        return ApprovalStatus(value)
    except ValueError:
        return ApprovalStatus.FAILED


def _row_to_action(row: sqlite3.Row | None) -> ProposedAction | None:
    if row is None:
        return None
    try:
        arguments = json.loads(row["arguments_json"] or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        arguments = {}
    try:
        metadata = json.loads(row["metadata_json"] or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        metadata = {}
    return ProposedAction(
        id=row["id"],
        tool_name=row["tool_name"],
        arguments=arguments,
        action_hash=row["action_hash"],
        status=_status(row["status"]),
        principal=row["principal"],
        agent_id=row["agent_id"],
        request_id=row["request_id"],
        workflow_id=row["workflow_id"],
        tool_call_id=row["tool_call_id"],
        capability=row["capability"],
        required_scope=row["required_scope"],
        side_effect=bool(row["side_effect"]),
        external=bool(row["external"]),
        risk=row["risk"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        expires_at=row["expires_at"],
        approved_action_hash=row["approved_action_hash"],
        decision_note=row["decision_note"],
        decided_by=row["decided_by"],
        decided_at=row["decided_at"],
        execution_id=row["execution_id"],
        executed_at=row["executed_at"],
        result_summary=row["result_summary"],
        error=row["error"],
        revision=int(row["revision"] or 1),
        metadata=metadata,
    )


class SQLiteApprovalRepository:
    """Durable approval repository using the operational database boundary."""

    def __init__(self, database):
        self._database = database

    def _connection(self):
        return self._database.connection()

    def create(self, action: ProposedAction) -> ProposedAction:
        with self._connection() as conn:
            conn.execute(
                """INSERT INTO proposed_actions (
                       id, tool_name, arguments_json, action_hash, status,
                       principal, agent_id, request_id, workflow_id, tool_call_id,
                       capability, required_scope, side_effect, external, risk,
                       created_at, updated_at, expires_at, approved_action_hash,
                       decision_note, decided_by, decided_at, execution_id, executed_at,
                       result_summary, error, revision, metadata_json
                   ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    action.id,
                    action.tool_name,
                    json.dumps(action.arguments, sort_keys=True, ensure_ascii=False, default=str),
                    action.action_hash,
                    action.status.value,
                    action.principal,
                    action.agent_id,
                    action.request_id,
                    action.workflow_id,
                    action.tool_call_id,
                    action.capability,
                    action.required_scope,
                    int(action.side_effect),
                    int(action.external),
                    action.risk,
                    action.created_at,
                    action.updated_at,
                    action.expires_at,
                    action.approved_action_hash,
                    action.decision_note,
                    action.decided_by,
                    action.decided_at,
                    action.execution_id,
                    action.executed_at,
                    action.result_summary,
                    action.error,
                    action.revision,
                    json.dumps(action.metadata, sort_keys=True, ensure_ascii=False, default=str),
                ),
            )
            conn.commit()
        return action

    def get(self, action_id: str) -> ProposedAction | None:
        with self._connection() as conn:
            row = conn.execute(
                "SELECT * FROM proposed_actions WHERE id=?", (action_id,)
            ).fetchone()
        return _row_to_action(row)

    def list(
        self,
        *,
        limit: int = 100,
        status: ApprovalStatus | None = None,
    ) -> list[ProposedAction]:
        with self._connection() as conn:
            if status is None:
                rows = conn.execute(
                    "SELECT * FROM proposed_actions ORDER BY created_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM proposed_actions WHERE status=? ORDER BY created_at DESC LIMIT ?",
                    (status.value, limit),
                ).fetchall()
        return [item for row in rows if (item := _row_to_action(row)) is not None]

    def edit_pending(
        self,
        action_id: str,
        arguments: dict[str, Any],
        action_hash: str,
        *,
        expires_at: str,
    ) -> ProposedAction | None:
        now = _now()
        with self._connection() as conn:
            cur = conn.execute(
                """UPDATE proposed_actions
                   SET arguments_json=?, action_hash=?, approved_action_hash=NULL,
                       status='pending', decision_note=NULL, decided_by=NULL, decided_at=NULL,
                       execution_id=NULL, executed_at=NULL, result_summary=NULL, error=NULL,
                       revision=revision+1, updated_at=?, expires_at=?
                   WHERE id=? AND status IN ('pending','approved')""",
                (
                    json.dumps(arguments, sort_keys=True, ensure_ascii=False, default=str),
                    action_hash,
                    now,
                    expires_at,
                    action_id,
                ),
            )
            conn.commit()
            changed = cur.rowcount == 1
        return self.get(action_id) if changed else None

    def approve(
        self,
        action_id: str,
        *,
        expected_hash: str,
        note: str = "",
        decided_by: str = "local-human",
    ) -> ProposedAction | None:
        now = _now()
        with self._connection() as conn:
            cur = conn.execute(
                """UPDATE proposed_actions
                   SET status='approved', approved_action_hash=action_hash,
                       decision_note=?, decided_by=?, decided_at=?, updated_at=?
                   WHERE id=? AND status='pending' AND action_hash=? AND expires_at>?""",
                (note or None, decided_by, now, now, action_id, expected_hash, now),
            )
            conn.commit()
            changed = cur.rowcount == 1
        return self.get(action_id) if changed else None

    def reject(
        self,
        action_id: str,
        *,
        note: str = "",
        decided_by: str = "local-human",
    ) -> ProposedAction | None:
        now = _now()
        with self._connection() as conn:
            cur = conn.execute(
                """UPDATE proposed_actions
                   SET status='rejected', decision_note=?, decided_by=?, decided_at=?, updated_at=?
                   WHERE id=? AND status IN ('pending','approved')""",
                (note or None, decided_by, now, now, action_id),
            )
            conn.commit()
            changed = cur.rowcount == 1
        return self.get(action_id) if changed else None

    def mark_expired(self, action_id: str) -> ProposedAction | None:
        now = _now()
        with self._connection() as conn:
            conn.execute(
                """UPDATE proposed_actions SET status='expired', updated_at=?
                   WHERE id=? AND status IN ('pending','approved') AND expires_at<=?""",
                (now, action_id, now),
            )
            conn.commit()
        return self.get(action_id)

    def claim_execution(
        self,
        action_id: str,
        *,
        expected_hash: str,
        execution_id: str,
    ) -> ProposedAction | None:
        """Atomically claim an approved action. Exactly one concurrent caller wins."""
        now = _now()
        with self._connection() as conn:
            try:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute(
                    "SELECT * FROM proposed_actions WHERE id=?", (action_id,)
                ).fetchone()
                action = _row_to_action(row)
                if (
                    action is None
                    or action.status is not ApprovalStatus.APPROVED
                    or action.action_hash != expected_hash
                    or action.approved_action_hash != expected_hash
                    or action.expires_at <= now
                ):
                    conn.rollback()
                    return None
                cur = conn.execute(
                    """UPDATE proposed_actions
                       SET status='executing', execution_id=?, updated_at=?
                       WHERE id=? AND status='approved'
                         AND action_hash=? AND approved_action_hash=?""",
                    (execution_id, now, action_id, expected_hash, expected_hash),
                )
                if cur.rowcount != 1:
                    conn.rollback()
                    return None
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return self.get(action_id)

    def finalize_execution(
        self,
        action_id: str,
        *,
        execution_id: str,
        ok: bool,
        result_summary: str | None = None,
        error: str | None = None,
    ) -> ProposedAction | None:
        now = _now()
        status = ApprovalStatus.EXECUTED.value if ok else ApprovalStatus.FAILED.value
        with self._connection() as conn:
            cur = conn.execute(
                """UPDATE proposed_actions
                   SET status=?, executed_at=?, updated_at=?, result_summary=?, error=?
                   WHERE id=? AND status='executing' AND execution_id=?""",
                (status, now, now, result_summary, error, action_id, execution_id),
            )
            conn.commit()
            changed = cur.rowcount == 1
        return self.get(action_id) if changed else None

    def recover_inflight(self) -> list[ProposedAction]:
        """Fail closed after restart for actions left in ``EXECUTING``.

        A process can die after an external side effect but before finalization,
        so automatic retry would risk duplicate writes. Mark the outcome
        uncertain and require a fresh human-reviewed proposal.
        """
        now = _now()
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT id FROM proposed_actions WHERE status='executing'"
            ).fetchall()
            ids = [row[0] for row in rows]
            if ids:
                conn.execute(
                    """UPDATE proposed_actions
                       SET status='failed', updated_at=?, executed_at=?,
                           error=COALESCE(
                             error,
                             'process restarted while action was executing; outcome may be uncertain; review before retry'
                           )
                       WHERE status='executing'""",
                    (now, now),
                )
                conn.commit()
        return [item for action_id in ids if (item := self.get(action_id)) is not None]
