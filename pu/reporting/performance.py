"""Small, local-only performance measurements for research workflows.

The workflow observer already has a stable event protocol.  This module keeps
the performance part deliberately boring: it measures elapsed wall-clock time
with :func:`time.perf_counter`, records stage reuse, and copies usage values
only when an event supplied them.  It does not call a provider, inspect the
database, or estimate missing timings.

``WorkflowPerformance`` is intended to be owned by the orchestration layer.
Wrap the existing observer with ``tracker.observe`` and put the resulting
``snapshot()`` under report metadata when the workflow finishes.
"""

from __future__ import annotations

import math
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any


def _finite_number(value: Any) -> float | None:
    """Return a finite float, or ``None`` for absent/invalid measurements."""

    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _duration(value: Any, *, milliseconds: bool = False) -> float | None:
    number = _finite_number(value)
    if number is None:
        return None
    if milliseconds:
        number /= 1000.0
    # A clock should never go backwards.  Clamping protects serialized output
    # when a caller supplies a custom clock or a malformed persisted event.
    return max(0.0, number)


class WorkflowPerformance:
    """Collect actual timings and usage for one research workflow execution.

    ``clock`` is injectable for deterministic tests.  It must return seconds
    on a monotonic scale, like ``time.perf_counter``.  The public output uses
    seconds and contains only finite numeric values.
    """

    SCOPE = "research_workflow"

    def __init__(self, *, clock=None, scope: str = SCOPE) -> None:
        self._clock = clock or time.perf_counter
        self._started = _finite_number(self._clock())
        if self._started is None:
            self._started = 0.0
        # stage -> pending start times; a list handles retry/resume events.
        self._stage_starts: dict[str, list[float]] = {}
        self._stages: dict[str, dict[str, Any]] = {}
        self._phases: dict[str, float] = {}
        self._cache_hit = False
        self._attempts = 0
        self._failures = 0
        self._observed_events = 0
        self._usage: dict[str, int | float] = {}
        self._tool_calls = 0
        self._tool_failures = 0
        self._models: set[str] = set()
        self._providers: set[str] = set()
        self._evidence_cache_hit = False
        self.scope = scope or self.SCOPE

    def set_evidence_cache_hit(self, reused: Any) -> WorkflowPerformance:
        """Record the explicit evidence-cache result separately from stages."""

        if isinstance(reused, bool):
            self._evidence_cache_hit = reused
        return self

    def _now(self) -> float:
        value = _finite_number(self._clock())
        # A broken injected clock must not leak NaN into output.  Returning the
        # start point gives a zero duration and keeps observation best-effort.
        return value if value is not None else self._started

    @staticmethod
    def _stage_name(event: dict[str, Any]) -> str | None:
        value = event.get("stage") or event.get("node")
        if value is None:
            return None
        name = str(value).strip()
        return name or None

    def _put_stage(self, name: str, seconds: float, *, reused: bool) -> None:
        seconds = _duration(seconds) or 0.0
        current = self._stages.get(name)
        # A reused checkpoint is the only truthful timing for this run: no
        # local work was performed, so its stage duration is zero.
        if reused:
            self._stages[name] = {"duration_seconds": 0.0, "reused": True}
            return
        if current and not current.get("reused"):
            seconds += _duration(current.get("duration_seconds")) or 0.0
        self._stages[name] = {"duration_seconds": seconds, "reused": False}

    def record(self, name: str, duration: Any) -> WorkflowPerformance:
        """Record an already measured phase duration in seconds.

        Invalid, infinite, and missing values are ignored because a fabricated
        duration is worse than an absent one in a benchmark.
        """

        phase = str(name).strip() if name is not None else ""
        seconds = _duration(duration)
        if phase and seconds is not None:
            self._phases[phase] = self._phases.get(phase, 0.0) + seconds
        return self

    @contextmanager
    def measure(self, name: str) -> Iterator[WorkflowPerformance]:
        """Measure a synchronous phase and store its actual duration."""

        started = self._now()
        try:
            yield self
        finally:
            self.record(name, max(0.0, self._now() - started))

    def _observe_usage(self, event: dict[str, Any]) -> None:
        usage = event.get("usage")
        if not isinstance(usage, dict):
            usage = {}
        aliases = {
            "input_tokens": ("input_tokens", "in", "input"),
            "output_tokens": ("output_tokens", "out", "output"),
            "calls": ("calls",),
        }
        for key, raw_keys in aliases.items():
            # Providers use both compact (in/out) and canonical token names.
            # Prefer one value per metric; never add aliases that describe the
            # same API response twice.
            raw = next((usage[name] for name in raw_keys if name in usage), None)
            if raw is None:
                raw = next((event[name] for name in raw_keys if name in event), None)
            number = _finite_number(raw)
            if number is not None and number >= 0:
                self._usage[key] = self._usage.get(key, 0) + number

        model = event.get("model")
        provider = event.get("provider") or event.get("provider_id")
        if model:
            self._models.add(str(model))
        if provider:
            self._providers.add(str(provider))

    def observe(self, kind: str, event: dict[str, Any] | None = None) -> None:
        """Observe one workflow event without changing workflow behavior."""

        event = event if isinstance(event, dict) else {}
        self._observed_events += 1
        self._observe_usage(event)
        if kind in {"workflow_started", "workflow_resumed", "workflow_attempt_started"}:
            self._attempts += 1

        stage = self._stage_name(event)
        if kind == "workflow_stage_started" and stage:
            self._stage_starts.setdefault(stage, []).append(self._now())
            return
        if kind == "workflow_stage_reused" and stage:
            self._cache_hit = True
            self._put_stage(stage, 0.0, reused=True)
            return
        if kind in {"workflow_stage_completed", "workflow_stage_failed", "workflow_failed"} and stage:
            starts = self._stage_starts.get(stage) or []
            started = starts.pop() if starts else None
            elapsed = (
                _duration(self._now() - started)
                if started is not None
                else _duration(event.get("duration_seconds"))
            )
            if elapsed is None:
                elapsed = _duration(event.get("duration_ms"), milliseconds=True)
            if elapsed is not None:
                self._put_stage(stage, elapsed, reused=False)
            if kind in {"workflow_stage_failed", "workflow_failed"}:
                self._failures += 1
            return
        if kind in {"workflow_failed", "workflow_attempt_failed"}:
            self._failures += 1
        if kind in {"tool", "tool_complete", "tool_failed"}:
            self._tool_calls += 1
            if kind == "tool_failed" or event.get("ok") is False:
                self._tool_failures += 1

    def snapshot(self) -> dict[str, Any]:
        """Return a JSON-safe snapshot containing observed facts only."""

        duration = _duration(self._now() - self._started) or 0.0
        result: dict[str, Any] = {
            "scope": self.scope,
            "duration_seconds": duration,
            "stages": {
                name: {
                    "duration_seconds": _duration(value.get("duration_seconds")) or 0.0,
                    "reused": bool(value.get("reused", False)),
                }
                for name, value in self._stages.items()
            },
            "cache_hit": self._cache_hit,
            "evidence_cache_hit": self._evidence_cache_hit,
            "attempts": self._attempts,
        }
        if self._failures:
            result["failures"] = self._failures
        if self._phases:
            result["phases"] = {
                name: _duration(seconds) or 0.0 for name, seconds in self._phases.items()
            }
        if self._usage:
            result["usage"] = {
                key: int(value) if float(value).is_integer() else value
                for key, value in self._usage.items()
            }
        if self._tool_calls:
            result["tools"] = {"calls": self._tool_calls, "failures": self._tool_failures}
        if self._models:
            result["models"] = sorted(self._models)
        if self._providers:
            result["providers"] = sorted(self._providers)
        return result


__all__ = ["WorkflowPerformance"]
