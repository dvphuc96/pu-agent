# Phase 9 — Persistence + AgentOps Hardening

## 1. Mục tiêu

Phase 9 làm cho PU Agent có **operational state durable và recoverable sau restart**, đồng thời biến tracing hiện có thành một AgentOps layer có structured event ledger, correlation IDs, SLO projection và optional OpenTelemetry/Prometheus/Tempo/Grafana stack.

Phase này giữ nguyên các boundary đã khóa ở Phase 3–8: provider-neutral LLM, Tool/Security Harness, Human Approval, specialized agents, deterministic orchestrator và report quality gate.

## 2. Operational persistence boundary

Package mới `pu/persistence/` gồm:

- `SQLiteDatabase`: connection-per-operation, `busy_timeout`, foreign keys và WAL khi khả dụng;
- `SQLiteMigrationRunner`: versioned/idempotent migrations;
- `SQLiteWorkflowRepository`: workflow run + stage checkpoint;
- `SQLiteAgentEventRepository`: durable structured event ledger;
- `SQLiteToolAuditSink`: durable tool audit;
- `OperationalPersistence` / `build_operational_persistence()`: infrastructure composition seam, bao gồm workflow/event/tool-audit/approval repositories.

SQLite là adapter thực tế được ship trong Phase 9. Product composition cũng dựng `SQLiteApprovalRepository` từ operational database boundary thay vì shared connection cũ; raw connection chỉ còn được hỗ trợ để tương thích test/legacy. Factory/repository boundary cho phép thêm PostgreSQL adapter về sau mà không sửa agent/harness/orchestrator, nhưng **PostgreSQL runtime adapter chưa được implement trong Phase 9**.

## 3. Versioned migrations

`schema_migrations` là migration ledger. Migration `9001` tạo:

- `workflow_runs`;
- `workflow_stage_checkpoints`;
- `agent_events`;
- `tool_audit_events`;
- các index cho status/time/workflow/execution lookup.

Migration được chạy cả qua legacy `connect()` lẫn operational persistence factory để local baseline và code mới nhìn cùng một schema. Test xác nhận migration idempotent.

## 4. Workflow restart recovery

`ResearchWorkflowOrchestrator` persist request, current stage và `AgentResult` sau mỗi stage hoàn tất. `resume(workflow_id)` đọc checkpoint và reuse stage đã hoàn thành thay vì gọi agent lại.

Ví dụ đã được test:

```text
Process 1
Email ✅ → Company ✅ → Web ❌

Process 2
Email reuse → Company reuse → Web → Calendar → Memory → Report ✅
```

Kết quả:

- Email/Company không bị gọi lại;
- attempt tăng từ 1 → 2;
- report id được persist khi completed;
- failed stage/current stage/error đều query được sau restart.

## 5. Idempotency và retry policy

Retry được phân biệt theo side-effect semantics:

- external read, `side_effect=False`: có thể auto-retry transient `TimeoutError`, `ConnectionError`, `OSError`;
- số retry cấu hình bằng `PU_TOOL_READ_RETRIES`, mặc định `2`;
- mọi retry tạo `tool_retry` AgentOps event + `retrying` audit transition;
- side-effect/write tool: **không bao giờ auto-retry trong Tool Harness**.

Boundary này tránh việc một timeout sau external write tạo duplicate mutation.

## 6. Approval recovery fail-closed

Nếu process chết trong lúc approval đang `EXECUTING`, hệ thống không thể biết chắc external API đã nhận side effect hay chưa. Vì vậy startup recovery:

1. tìm approval còn `EXECUTING`;
2. chuyển thành `FAILED`;
3. ghi error `outcome may be uncertain`;
4. phát `approval_recovery_failed_closed`;
5. không tự execute lại.

Human phải review trước khi tạo execution mới. Đây là intentional fail-closed behavior.

## 7. AgentEvent schema v2

`AgentEvent` hiện mang envelope thống nhất:

- `id`, `kind`, `occurred_at`, `schema_version`;
- `trace_id`;
- `request_id`;
- `workflow_id`;
- `agent_id`;
- `tool_call_id`;
- `execution_id`;
- `approval_id`;
- `report_id`;
- `severity`, `status`, `duration_ms`;
- `payload`.

`AgentOpsHarness` persist event vào SQLite durable ledger và mirror sang existing `Tracer`. Tool Harness và LLM loop enrich correlation IDs để có thể truy ngược workflow → agent → tool/execution.

## 8. Durable tool audit

Tool audit dùng `CompositeToolAuditSink`:

```text
Tool Harness
   ├── SQLiteToolAuditSink → state.db/tool_audit_events
   └── JsonlToolAuditSink  → .pu/traces/tool-audit.jsonl
```

SQLite là durable/queryable ledger; JSONL là human-readable mirror. Existing redaction của secret keys/secret-like values vẫn được giữ trước persistence.

## 9. SLO và Prometheus projection

`SLOEvaluator` aggregate durable events thành:

| Signal | Default threshold |
|---|---:|
| LLM p95 latency | ≤ 15,000 ms |
| Tool error rate | ≤ 5% |
| Workflow success rate | ≥ 95% |
| Approval execution failure rate | ≤ 5% |
| Security blocks/window | ≤ 25 |

Env tương ứng:

- `PU_SLO_LLM_P95_MS`;
- `PU_SLO_TOOL_ERROR_RATE`;
- `PU_SLO_WORKFLOW_SUCCESS_RATE`;
- `PU_SLO_APPROVAL_FAILURE_RATE`;
- `PU_SLO_SECURITY_BLOCKS_MAX`.

Dashboard expose `/metrics` theo Prometheus text exposition mà không buộc runtime cài `prometheus_client`.

## 10. OTel / Tempo / Prometheus / Grafana

`observability/` chứa local stack:

- OpenTelemetry Collector;
- Tempo;
- Prometheus;
- Grafana;
- datasource provisioning;
- PU AgentOps dashboard provisioning.

Chạy local:

```bash
OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317 python -m pu dashboard
docker compose -f observability/docker-compose.yml up -d
```

Prometheus scrape `host.docker.internal:9000/metrics`; OTLP traces đi qua collector tới Tempo. Stack optional: durable SQLite events/audit vẫn hoạt động khi stack tắt.

Sandbox dùng để build Phase 9 không có Docker executable nên compose stack **không được runtime-start trong sandbox**; config được ship như deployment artifact, còn core persistence/metrics/tracing integration được deterministic-test tại application boundary.

## 11. OTel workflow correlation

`Tracer` giữ workflow root span từ `workflow_started` hoặc `workflow_resumed` tới `workflow_completed`/`workflow_failed`. Runtime events có `workflow_id` được tạo child span dưới workflow root khi OTel SDK/exporter khả dụng.

Nếu OTel package/exporter không khả dụng, JSONL + SQLite AgentOps vẫn là fail-safe observability path.

## 12. Dashboard/API

Dashboard Phase 9 bổ sung:

- AgentOps/SLO health;
- durable workflow list;
- current stage/status/attempt/report id;
- resume workflow từ checkpoint;
- Security view đọc SQL audit ledger;
- `/metrics` Prometheus endpoint.

Research API khi workflow fail trả `workflow_id`, `failed_stage`, `recoverable=true`. Resume được thực hiện ở backend; browser không tự orchestration.

## 13. Testing

Phase-9 deterministic suite có 7 case mới:

1. migration versioned + idempotent;
2. workflow restart recovery không rerun completed stages;
3. correlated durable AgentOps events + SLO/Prometheus projection;
4. durable redacted tool audit;
5. composition root expose operational repositories/SLO evaluator;
6. transient external-read retry, write không auto-retry;
7. restart fail-closed cho in-flight approved action.

Kết quả cuối:

```text
Phase 9                         7 passed
Phase 3 → Phase 9 targeted    68 passed
Green deterministic          347 passed, 13 skipped, 5 deselected
Full deterministic           347 passed, 13 skipped, 30 baseline failures
Python compileall             PASS
JavaScript syntax             PASS
PU CLI                         PASS
```

30 full-suite failures là đúng baseline đã tồn tại từ các phase trước: source ZIP không có Git metadata/`.env.example` và 5 stale assertions ngoài Phase 9. Không có regression mới.

## 14. Assumptions / Deferred

- SQLite vẫn là source-of-truth adapter được ship trong Phase 9.
- PostgreSQL mới ở **repository/factory-ready boundary**; adapter/driver/deployment migration cụ thể được defer cho khi production requirement chốt.
- Orchestrator vẫn execute deterministic serial order. Connection-per-operation đã bỏ persistence blocker cho concurrency, nhưng Phase 9 không thay scheduling behavior.
- In-flight external write sau crash có outcome không chắc chắn nên fail-closed; auto reconciliation với provider-specific idempotency keys chưa được implement.
- Observability Docker stack không được start trong build sandbox vì Docker không có sẵn.

## 15. Exit criteria

- ✅ versioned migrations;
- ✅ durable workflow runs/checkpoints;
- ✅ restart/resume không rerun completed stages;
- ✅ external-read retry policy;
- ✅ no automatic side-effect retry;
- ✅ fail-closed uncertain write recovery;
- ✅ durable structured AgentOps event ledger;
- ✅ durable + mirrored redacted tool audit;
- ✅ correlation IDs xuyên workflow/agent/tool/execution;
- ✅ OTel workflow spans;
- ✅ Prometheus `/metrics` + SLO projection;
- ✅ optional Tempo/Prometheus/Grafana stack;
- ✅ regression gates không phát sinh failure mới.
