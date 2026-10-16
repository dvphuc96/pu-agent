"""Deterministic workflow engine contract."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class WorkflowEngine(Protocol):
    def run(
        self,
        workflow: Any,
        state: dict[str, Any],
        observer=None,
        max_steps: int = 25,
    ) -> dict[str, Any]: ...
