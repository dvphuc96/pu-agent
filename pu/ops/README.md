# `pu/ops` — operations and evaluation surface

This package contains the product operations surface around the core agent runtime.

| File | Responsibility |
|---|---|
| `dashboard.py` | stdlib HTTP server, JSON/SSE routes, data collection |
| `browser_agent.py` | shared browser-facing `PU` instance and session |
| `catalog.py` | provider model catalog + pinned shortlist |
| `pricing.py` | supported model pricing/cutoff metadata |
| `settings_api.py` | graph toggle + model pins |
| `tracing.py` | JSONL tracing + optional OpenTelemetry export |
| `show_trace.py` | terminal trace viewer |
| `judge.py` | provider-neutral LLM-as-judge helper |
| `scoring.py` | deterministic completion scoring |
| `release_gate.py` | release/evaluation gate |
| `brief.py` | multi-agent morning research brief entry point |
| `triage.py` | binder for the deterministic triage graph |
| `whiteboard/` | architecture diagram generator |
