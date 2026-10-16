# PU AgentOps local stack

1. Start PU dashboard: `python -m pu.ops.dashboard`.
2. Set `OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317` before starting PU if trace export is desired.
3. Run `docker compose -f observability/docker-compose.yml up -d`.
4. Grafana: http://localhost:3000, Prometheus: http://localhost:9090, Tempo API: http://localhost:3200.

Prometheus scrapes PU's dependency-free `/metrics` endpoint. Structured AgentOps events and tool audit transitions remain durable in `.pu/state.db` even when the observability stack is off. JSONL traces/audit files are retained as human-readable mirrors.
