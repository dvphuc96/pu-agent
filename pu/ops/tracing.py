"""Trace — one trace per run (the LLM-Ops box, first step).

Two outputs from the same events:

1. JSONL, always on: every turn appends readable lines to
   .pu/traces/<date>.jsonl. A trace is just "what happened, in order" —
   open the file and read your agent's mind. Zero dependencies.

2. OpenTelemetry spans, when OTEL_EXPORTER_OTLP_ENDPOINT is set: the same
   events as a span tree any OTel backend can render. PU ships a local
   Collector + Tempo + Grafana stack under ``observability/``:

       pip install 'pu-agent[tracing]'
       docker compose -f observability/docker-compose.yml up -d
       OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317 python -m pu dashboard

   The instrumentation remains backend-agnostic; any compatible OTLP endpoint
   can replace the local collector.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from pu.config import Settings


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


class TraceEncodingError(UnicodeError):
    """A non-UTF8 trace cannot be safely read or appended as UTF-8."""

    def __init__(self, path: Path):
        self.path = path
        super().__init__(
            f"Trace file is not valid UTF-8: {path}. It may have been written by an older "
            "PU version using the Windows system encoding. Move it out of the traces directory "
            "and keep it as a backup, then restart PU if it is today's trace. The file "
            "was not modified."
        )


def iter_trace_lines(path: Path) -> Iterator[str]:
    """Yield one UTF-8 trace line at a time with a useful encoding error."""
    try:
        with path.open("r", encoding="utf-8") as trace:
            yield from trace
    except UnicodeDecodeError as exc:
        raise TraceEncodingError(path) from exc


class Tracer:
    """Doubles as a loop Observer: pass `tracer.event` anywhere an observer
    goes and every loop step lands in the trace."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.path = settings.home / "traces" / f"{datetime.now().strftime('%Y-%m-%d')}.jsonl"
        self._otel_tracer = self._init_otel(settings)
        self._span_ctx = None
        self._workflow_spans = {}
        self._trace_api = None
        self._trace_encoding_checked = False

    def _init_otel(self, settings: Settings):
        if not settings.otel_endpoint:
            return None
        try:
            from opentelemetry import trace
            from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
            from opentelemetry.sdk.resources import Resource
            from opentelemetry.sdk.trace import TracerProvider
            from opentelemetry.sdk.trace.export import BatchSpanProcessor

            provider = TracerProvider(resource=Resource.create({"service.name": "pu-agent"}))
            provider.add_span_processor(
                BatchSpanProcessor(OTLPSpanExporter(endpoint=settings.otel_endpoint, insecure=True))
            )
            trace.set_tracer_provider(provider)
            self._otel_provider = provider
            self._trace_api = trace
            return trace.get_tracer("pu")
        except ImportError:
            print("(tracing) OTEL endpoint set but opentelemetry not installed — "
                  "pip install 'pu-agent[tracing]'. JSONL tracing still on.")
            return None

    def _write(self, record: dict) -> None:
        # An older Windows release may have created this daily file in GBK.
        # Refuse to make a mixed-encoding JSONL file: validate once, explain how
        # to preserve the old file, and never guess or rewrite user data.
        if not self._trace_encoding_checked:
            if self.path.exists():
                for _ in iter_trace_lines(self.path):
                    pass
            self._trace_encoding_checked = True
        record["ts"] = _now()
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")

    def _record_usage(self, event: dict) -> None:
        """Append one LLM call's token usage to a PERMANENT ledger (usage.jsonl).
        Unlike traces (which can be reset for a clean demo), this is the running
        record of what you've actually spent — never wiped, summarized per day on
        the dashboard. Tokens are the ground truth; dollar cost is derived from
        them (pricing can change), so we store tokens + provider/model."""
        usage = event.get("usage", {})
        record = {"ts": _now(), "provider": self.settings.provider,
                  "model": self.settings.model or "", "kind": "loop",
                  "in": usage.get("in", 0), "out": usage.get("out", 0)}
        with (self.settings.home / "usage.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")

    # ---- the Observer: called by the loop for every llm/tool/gate/... event
    def event(self, kind: str, event: dict) -> None:
        if kind == "text":
            return  # streaming token deltas are for the live UI, not the trace
        if kind == "llm":
            self._record_usage(event)
            # stamp WHICH model answered — required for multi-provider traces,
            # live model switching) a trace without the model is half a trace
            event = {"provider": self.settings.provider,
                     "model": self.settings.model or "", **event}
        self._write({"type": kind, **event})
        if self._otel_tracer:
            workflow_id = event.get("workflow_id")
            if kind in {"workflow_started", "workflow_resumed"} and workflow_id and workflow_id not in self._workflow_spans:
                self._workflow_spans[workflow_id] = self._otel_tracer.start_span(
                    f"workflow.{event.get('workflow', 'research')}",
                    attributes={"openinference.span.kind": "CHAIN", "pu.workflow_id": workflow_id},
                )
            parent = self._span_ctx or (self._workflow_spans.get(workflow_id) if workflow_id else None)
            context = self._trace_api.set_span_in_context(parent) if (parent is not None and self._trace_api) else None
            kwargs = {
                "attributes": {
                    "openinference.span.kind": {"llm": "LLM", "tool": "TOOL"}.get(kind, "CHAIN"),
                    **{f"pu.{k}": json.dumps(v, default=str) for k, v in event.items()},
                }
            }
            if context is not None:
                kwargs["context"] = context
            with self._otel_tracer.start_as_current_span(
                f"{kind}.{event.get('tool', event.get('decision', ''))}".rstrip("."),
                **kwargs,
            ):
                pass
            if kind in {"workflow_completed", "workflow_failed"} and workflow_id:
                root = self._workflow_spans.pop(workflow_id, None)
                if root is not None:
                    root.end()

    # ---- one run = one root span + turn_start/turn_end JSONL markers
    @contextmanager
    def turn(self, user_message: str):
        self._write({"type": "turn_start", "user_message": user_message})
        if self._otel_tracer:
            with self._otel_tracer.start_as_current_span(
                "agent_run",
                attributes={"openinference.span.kind": "AGENT", "pu.user_message": user_message},
            ) as span:
                self._span_ctx = span
                try:
                    yield self
                finally:
                    self._span_ctx = None
        else:
            yield self

    def end_turn(self, reply: str, iterations: int) -> None:
        self._write({"type": "turn_end", "reply": reply, "iterations": iterations})
        if getattr(self, "_otel_provider", None):
            # flush per turn: the trace should survive even a killed process
            self._otel_provider.force_flush(timeout_millis=2000)

    def close(self) -> None:
        """End unfinished workflow spans and flush the optional OTLP exporter."""
        for root in list(self._workflow_spans.values()):
            try:
                root.end()
            except Exception:
                pass
        self._workflow_spans.clear()
        provider = getattr(self, "_otel_provider", None)
        if provider is not None:
            try:
                provider.force_flush(timeout_millis=2000)
            except Exception:
                pass


def compose(*observers) -> callable:
    """Fan one loop event out to several observers (gateway display + tracer)."""
    active = [o for o in observers if o]
    def fanout(kind: str, event: dict) -> None:
        for obs in active:
            obs(kind, event)
    return fanout
