# Phase 10 — Target Architecture Lock-in

Ngày hoàn tất: **2026-10-04**

## 1. Mục tiêu

Phase 10 là phase cleanup cuối của chuỗi refactor từ `Archive` sang `pu-agent`. Mục tiêu không phải thêm capability mới mà là loại bỏ các đường migration/compatibility đã hết nhiệm vụ, xóa dead code, làm cho test/artifact tự đủ, và khóa dependency graph về đúng target architecture đã thiết kế trong `ARCHITECTURE.md`.

Nguyên tắc của phase này:

- production code không giữ shim chỉ để chiều test cũ;
- một responsibility chỉ có một execution path chính thức;
- LLM runtime/test đều dùng provider-neutral contract;
- Tool Registry chỉ là catalog, mọi execution đi qua Tool Harness;
- write path tiếp tục bắt buộc Human Approval + Security re-check;
- cleanup phải được bảo vệ bằng architecture tests để legacy path không quay lại.

## 2. Kết quả chính

### 2.1 Xóa migration-only compatibility paths

Đã xóa khỏi source tree:

```text
pu/adapters/legacy/
pu/providers/llm/compat.py
pu/providers/llm/legacy.py
pu/loop/models.py
sql/init_supabase.sql
```

Các replacement chính thức hiện là:

```text
DefaultAgentRuntime
GraphWorkflowEngine
AnthropicLLMProvider
OpenAILLMProvider
DefaultToolHarness
OperationalPersistence repositories
```

`PU` chỉ còn constructor nhận `ApplicationDependencies`; product entry points phải đi qua `create_application()` / `build_dependencies()`.

### 2.2 Provider-neutral contract được khóa hoàn toàn

Test fixtures trước đây còn mô phỏng Anthropic-shaped client (`messages.create`) đã được chuyển sang `ScriptedProvider` implement `LLMProvider.complete()`.

Không còn:

```text
coerce_llm_provider
pu.providers.llm.compat
pu.providers.llm.legacy
pu.loop.models
client= vendor-shaped composition injection
```

Vendor SDK import chỉ được phép trong:

```text
pu/providers/llm/anthropic.py
pu/providers/llm/openai.py
```

Endpoint override cũng được tách theo provider:

```text
PU_ANTHROPIC_BASE_URL
PU_OPENAI_BASE_URL
```

Global `PU_BASE_URL` đã bị loại để không mang nhầm endpoint khi đổi provider.

### 2.3 Tool execution chỉ còn một boundary

`ToolRegistry.execute()` đã bị xóa hoàn toàn. Registry chỉ còn metadata/catalog/resolve/subset.

Runtime path chính thức:

```text
AgentRuntime
   ↓
ToolHarness.execute(...)
   ↓
Tool Registry resolve
   ↓
Schema/Auth/Scope/Capability/Rate Limit/Audit
   ↓
Security Harness
   ↓
Tool.execute(...)
```

Write path tiếp tục là:

```text
Agent
  ↓
ProposedAction
  ↓
HumanApprovalService
  ↓
ApprovedActionExecutor
  ↓
ToolHarness
  ↓
Security re-check
  ↓
Concrete handler
```

Source scan xác nhận không có `registry.execute(...)` callsite. Concrete `Tool.execute(...)` chỉ được gọi bên trong `DefaultToolHarness`.

### 2.4 Memory read/write boundary được hoàn thiện

`search_memory` là canonical read path:

```text
capability = memory:read
side_effect = false
```

`manage_memory` hiện chỉ có:

```text
update
delete
```

và là write-only:

```text
capability = memory:write
side_effect = true
```

Compatibility action `search` trong `manage_memory` đã bị xóa. Vì vậy Memory Agent không cần mang write capability chỉ để retrieval.

### 2.5 Approval persistence chỉ dùng operational database boundary

`SQLiteApprovalRepository` không còn nhận raw `sqlite3.Connection`. Product composition đưa approval/workflow/event/tool-audit repositories qua cùng operational persistence factory và connection-per-operation boundary.

Điều này giảm coupling giữa application logic và SQLite connection lifecycle, đồng thời giữ seam để thêm PostgreSQL adapter sau này nếu requirements cần.

### 2.6 Hosted-memory/dead backend remnants được xóa

`FactStore` protocol đã được thu về đúng API local product thực sự dùng:

```text
add
search
list
search_with_ids
update
delete
```

Các method phục vụ hosted-memory backend đã prune từ Phase 2 (`env_or`, `settle`) bị xóa. `sql/init_supabase.sql` cũng bị loại.

### 2.7 Artifact privacy/config trở thành self-contained

Repo giờ ship:

```text
.gitignore
.env.example
```

`.env.example` được generate/check bởi:

```bash
python scripts/generate_env_example.py --check
```

Privacy tests không còn phụ thuộc thư mục `.git`, nên cùng một test suite chạy được từ source checkout và từ ZIP bàn giao.

### 2.8 Trả hết baseline test debt

Các assertion cũ còn mô tả product trước Phase 2–9 đã được cập nhật theo behavior hiện tại, bao gồm Gmail/Knowledge group, calendar copy, triage semantics, provider model field và privacy packaging.

Kết quả full deterministic suite hiện tại:

```text
382 passed
13 skipped
0 failed
```

Không còn khái niệm `30 baseline failures` hoặc `5 deselected` để green gate.

## 3. Diff so với Phase 9

So với working snapshot Phase 9 trước Phase 10:

```text
8 files removed
5 files added
86 files modified
```

8 file/path bị xóa gồm:

```text
pu/adapters/legacy/__init__.py
pu/adapters/legacy/agent_runtime.py
pu/adapters/legacy/tool_harness.py
pu/adapters/legacy/workflow.py
pu/loop/models.py
pu/providers/llm/compat.py
pu/providers/llm/legacy.py
sql/init_supabase.sql
```

Các file mới quan trọng:

```text
.gitignore
.env.example
pu/runtime/agent.py
pu/adapters/workflow.py
evals/deterministic/test_phase10_target_architecture.py
```

## 4. Architecture guard mới

`evals/deterministic/test_phase10_target_architecture.py` khóa các invariants sau:

1. migration-only modules đã xóa không được quay lại;
2. `PU` chỉ được dựng qua dependency graph;
3. `ToolRegistry` không có execution method;
4. source runtime không import provider compatibility shims;
5. endpoint override chỉ provider-scoped;
6. memory read/write capability tách biệt;
7. approval repository dùng database boundary;
8. source distribution có privacy/config artifacts;
9. product provider catalog đúng `{anthropic, openai}`.

Phase-10 architecture guard: **9/9 PASS**.

## 5. Validation

### Static/runtime gates

```text
Python compileall                  PASS
JavaScript node --check           PASS
python -m pu --help               PASS
.env.example generator --check    PASS
stale removed-path scan           0 offender
direct registry.execute callsite  0
vendor import outside adapters    0
```

### Test gates

```text
Phase 3 → Phase 10 targeted     77 passed
Full deterministic             382 passed, 13 skipped, 0 failed
Judge suite                    28 skipped (không có live provider credentials)
```

### Observability config

```text
All observability YAML   parse PASS
Grafana dashboard JSON   parse PASS
```

Docker executable không có trong sandbox, do đó Phase 10 không tuyên bố OTel/Tempo/Prometheus/Grafana stack đã được runtime-start trong môi trường này.

### Tooling limitation

`ruff` không được cài trong sandbox nên lint gate không chạy được. Python compile + deterministic tests + source boundary scans vẫn chạy đầy đủ. Anthropic/OpenAI SDK cũng không được cài trong sandbox; provider adapter/conformance tests dùng injected fake SDK boundary và không gọi live API.

## 6. Target dependency graph sau Phase 10

```text
CLI / Dashboard / Brief
          ↓
   Composition Root
          ↓
       PU facade
          │
          ├── AgentOrchestrator
          │      ├── EmailAgent
          │      ├── CompanyInfoAgent
          │      ├── WebResearchAgent
          │      ├── CalendarAgent
          │      ├── MemoryAgent
          │      └── ReportGenerationAgent
          │
          ├── DefaultAgentRuntime
          │      ↓
          │   LLMProvider
          │    ├── AnthropicLLMProvider
          │    └── OpenAILLMProvider
          │
          ├── scoped ToolRegistry
          │      ↓
          ├── DefaultToolHarness
          │      ↓
          ├── SecurityHarness
          │      ↓
          ├── Integration adapters
          │
          ├── HumanApprovalService
          │      ↓
          ├── ApprovedActionExecutor
          │
          ├── EvaluationHarness
          │
          └── OperationalPersistence + AgentOps
```

Không còn đường song song kiểu “legacy client → legacy runtime → direct registry execute”.

## 7. Những gì cố tình chưa làm

Phase 10 là cleanup/lock-in, không mở scope mới. Các điểm sau vẫn là quyết định tương lai chứ không phải thiếu sót bị che giấu:

- PostgreSQL adapter chưa được implement; SQLite là adapter production-shaped hiện ship.
- Web research chưa có controlled full-page fetch/extraction layer; hiện dựa trên search integration đã giữ từ baseline.
- Gmail write/send concrete capability cần tiếp tục kế thừa approval boundary khi bổ sung/hoàn thiện.
- Multi-user auth/SSO/tenant authorization chưa nằm trong local-first single-user scope hiện tại.
- Parallel fan-out executor chưa bật; orchestrator vẫn deterministic serial dù persistence đã đủ seam cho concurrency.
- Live Anthropic/OpenAI/Gmail/Calendar/Drive/Tavily integration tests không chạy trong sandbox do không có credentials/network package environment.

## 8. Kết luận

Phase 10 hoàn tất migration từ codebase `Archive` sang target architecture của **PU Agent**. Từ thời điểm này, các phase 0–10 được xem là **foundation refactor complete**: OOP composition, provider-neutral LLM, specialized agents, Tool/Security Harness, Human Approval, grounded reports/evaluation, durable persistence và AgentOps đều nằm trên một dependency graph duy nhất và được deterministic tests bảo vệ.

Các thay đổi sau Phase 10 nên được coi là product capability work / hardening / deployment work, không còn là cleanup của baseline Archive.
