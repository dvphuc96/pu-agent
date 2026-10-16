"""WorkflowEngine adapter backed by PU's deterministic graph engine."""

from __future__ import annotations

from pu.core.interfaces.workflow import WorkflowEngine
from pu.graph.engine import run_graph


class GraphWorkflowEngine(WorkflowEngine):
    def run(self, workflow, state: dict, observer=None, max_steps: int = 25) -> dict:
        return run_graph(workflow, state, observer=observer, max_steps=max_steps)
