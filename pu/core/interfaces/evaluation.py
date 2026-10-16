"""Evaluation Harness contract. Deterministic quality remains authoritative."""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from pu.evaluation.models import ReportEvaluation
from pu.reporting.models import StructuredReport


@runtime_checkable
class EvaluationHarness(Protocol):
    def evaluate(self, report: StructuredReport, *, run_judge: bool = False) -> ReportEvaluation: ...
