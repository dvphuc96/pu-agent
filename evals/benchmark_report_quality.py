"""Summarize persisted report-workflow performance records offline.

Usage::

    python evals/benchmark_report_quality.py records.json

The input is deliberately limited to sanitized JSON (an array, an object with
``records``, or JSON Lines).  This command never contacts a model/provider and
does not open PU's private database.  Its numbers describe the recorded
``research_workflow`` scope only; they are not end-to-end product latency.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import defaultdict
from collections.abc import Iterable
from pathlib import Path
from typing import Any


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        value = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return value if math.isfinite(value) and value >= 0 else None


def _percentile(values: Iterable[float], percentile: float) -> float | None:
    """Nearest-rank percentile, matching the AgentOps aggregation convention."""

    values = sorted(value for value in values if _number(value) is not None)
    if not values:
        return None
    rank = max(1, math.ceil(len(values) * percentile))
    return values[rank - 1]


def _stats(records: list[dict[str, Any]]) -> dict[str, Any]:
    durations = [value for record in records if (value := _number(record.get("duration_seconds"))) is not None]
    return {
        "sample_count": len(durations),
        "p50_seconds": _percentile(durations, 0.50),
        "p95_seconds": _percentile(durations, 0.95),
    }


def _record_list(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        payload = payload.get("records", payload.get("performance", [payload]))
    if isinstance(payload, dict):
        payload = [payload]
    if not isinstance(payload, list):
        return []
    records: list[dict[str, Any]] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        nested = item.get("performance") or (item.get("metadata") or {}).get("performance")
        records.append(nested if isinstance(nested, dict) else item)
    return records


def load_records(path: str | Path) -> list[dict[str, Any]]:
    """Read a sanitized JSON or JSONL file, without touching application data."""

    text = sys.stdin.read() if str(path) == "-" else Path(path).read_text(encoding="utf-8")
    if not text.strip():
        return []
    try:
        return _record_list(json.loads(text))
    except json.JSONDecodeError:
        records: list[dict[str, Any]] = []
        for line in text.splitlines():
            if not line.strip():
                continue
            item = json.loads(line)
            records.extend(_record_list(item))
        return records


def _scope(records: list[dict[str, Any]]) -> str:
    scopes = {str(record.get("scope")) for record in records if record.get("scope")}
    return next(iter(scopes)) if len(scopes) == 1 else "research_workflow"


def _group_key(record: dict[str, Any]) -> str:
    provider = record.get("provider") or record.get("provider_id")
    model = record.get("model")
    # Snapshots may carry one observed provider/model in arrays.  A group is
    # reported only from values explicitly present in the persisted record.
    if provider is None and isinstance(record.get("providers"), list) and len(record["providers"]) == 1:
        provider = record["providers"][0]
    if model is None and isinstance(record.get("models"), list) and len(record["models"]) == 1:
        model = record["models"][0]
    return f"{provider or 'unknown'}/{model or 'unknown'}"


def benchmark(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Return deterministic latency summaries for sanitized snapshots."""

    valid = [
        {**record, "duration_seconds": _number(record.get("duration_seconds"))}
        for record in records
        if record.get("scope") == "research_workflow"
        and _number(record.get("duration_seconds")) is not None
    ]
    cold = [record for record in valid if record.get("cache_hit") is False]
    warm = [record for record in valid if record.get("cache_hit") is True]
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in valid:
        groups[_group_key(record)].append(record)
    return {
        "scope": _scope(valid),
        "scope_note": "Measurements cover the persisted research workflow only; they are not end-to-end application latency.",
        "sample_count": len(valid),
        "excluded_records": len(records) - len(valid),
        "p50_seconds": _percentile([record["duration_seconds"] for record in valid], 0.50),
        "p95_seconds": _percentile([record["duration_seconds"] for record in valid], 0.95),
        "cold": _stats(cold),
        "warm": _stats(warm),
        "by_provider_model": {key: _stats(items) for key, items in sorted(groups.items())},
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", help="sanitized JSON/JSONL records file, or - for stdin")
    args = parser.parse_args(argv)
    print(json.dumps(benchmark(load_records(args.input)), ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
