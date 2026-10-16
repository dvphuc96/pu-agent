# Phase 3 — OOP Domain Contracts + Composition Root

**Ngày:** 2026-10-04  
**Project:** `pu-agent`  
**Baseline đầu vào:** snapshot Phase 2 (`pu-agent-phase2.zip`)  
**Mục tiêu:** tạo OOP seams và composition root nhưng không thay đổi business behavior/execution policy.

## 1. Kết luận

Phase 3 đã hoàn tất.

Runtime hiện có một dependency direction rõ hơn:

```text
Entry Point
   ↓
create_application()
   ↓
Application Composition Root
   ↓
PU application facade
   ├── AgentRuntime
   ├── WorkflowEngine
   ├── EventPublisher
   ├── MemoryRepository-compatible facade
   ├── ToolRegistry
   └── legacy ToolHarness seam
```

Điểm quan trọng nhất: product entry points không còn tự dựng `PU()` và các dependency. `PU` cũng không còn trực tiếp tạo DB/client/memory/tools/session/tracer trong constructor chính. Object graph được dựng tập trung tại `pu/core/container.py` rồi inject vào application facade.

Phase 3 **không** thay execution/security semantics. Legacy agent loop và direct tool execution vẫn tồn tại có chủ đích để khóa behavior trước Phase 4/5.

## 2. OOP contracts đã thêm

### Agent

- `BaseAgent`
- `AgentRuntime`
- `AgentRunRequest`
- `AgentResult`

`PU` hiện implement `BaseAgent`. Legacy loop được wrap bởi `LegacyAgentRuntimeAdapter`.

### LLM

Provider-neutral contracts đã được định nghĩa:

- `LLMProvider`
- `LLMRequest`
- `LLMResponse`
- `LLMToolDefinition`
- `LLMToolCall`
- `LLMUsage`

Phase 3 chỉ tạo contract. Hai implementation provider-neutral thật sự thuộc Phase 4.

### Tool

- `ToolDefinition`
- `ToolContext`
- `ToolResult`
- `ToolContract`
- `ToolHarness`

`ToolRegistry` hiện hỗ trợ:

```text
resolve(name)
definitions()
schemas()      # compatibility
execute()      # legacy direct path, tạm thời
```

Metadata/capability có thể được resolve mà không chạy tool. Đây là seam cần thiết cho Security/Approval ở các phase sau.

### Security

Đã tạo contract:

- `SecurityDecision`
- `SecurityPolicy`
- `SecurityHarness`

**Chưa có enforcement** trong Phase 3. Không được hiểu các interface này là security đã hoàn thành.

### Repository

Đã tạo:

- `MemoryRepository`
- `ConnectionFactory`

`Memory` SQLite facade hiện tại structurally compatible với `MemoryRepository`. Persistence split/hardening thực sự thuộc Phase 9.

### Workflow

- `WorkflowEngine`
- `GraphWorkflowEngineAdapter`

Graph engine hiện tại được giữ nguyên và wrap bằng adapter.

### AgentOps events

- `AgentEvent`
- `EventPublisher`
- `CallableEventPublisher`
- `CompositeEventPublisher`

Observer + tracer callbacks cũ được fan-out qua OOP publisher seam nhưng thứ tự event vẫn giữ như baseline.

## 3. Composition root

File authoritative:

```text
pu/core/container.py
```

Object graph hiện được dựng tại một chỗ:

```text
Settings
  ↓
SQLite connection
  ↓
Legacy provider client
  ↓
Memory
  ↓
ToolRegistry
  ↓
Session
  ↓
Tracer
  ↓
LegacyAgentRuntimeAdapter
  ↓
GraphWorkflowEngineAdapter
  ↓
EventPublisher
  ↓
PU
```

Các entry point chính đã chuyển sang:

```python
create_application(...)
```

thay vì:

```python
PU(...)
```

Áp dụng cho:

- CLI;
- morning brief;
- browser/dashboard agent;
- deterministic eval helper.

Constructor `PU(settings=..., client=..., conn=...)` được giữ tạm để compatibility, nhưng nó cũng delegate ngay về `build_dependencies()`; không còn object graph thứ hai.

## 4. Provider boundary

Legacy provider infrastructure đã được chuyển:

```text
pu/loop/models.py
        ↓ compatibility shim
pu/providers/llm/legacy.py
```

Source scan xác nhận SDK imports:

```text
anthropic
openai
```

chỉ còn nằm trong:

```text
pu/providers/llm/legacy.py
```

`pu/app.py`, `pu/core`, `pu/agents`, `pu/loop/agent.py`, `pu/memory/*` không còn import vendor SDK trực tiếp.

Đây mới chỉ là **dependency boundary**, chưa phải provider-neutral runtime. Legacy provider vẫn trả Anthropic-shaped client để giữ behavior. Phase 4 sẽ xóa shape này khỏi loop.

## 5. Legacy adapters

### `LegacyAgentRuntimeAdapter`

Wrap `run_loop()` và giữ:

- max iterations;
- streaming fallback;
- tool call loop;
- usage accumulation;
- observer events;
- `LoopResult` compatibility.

### `GraphWorkflowEngineAdapter`

Wrap `run_graph()` hiện tại; graph semantics không đổi.

### `LegacyDirectToolHarness`

Wrap `ToolRegistry.execute()` thành `ToolHarness` contract.

Tên `LegacyDirect...` là intentional: adapter này **không validate auth/policy/approval** và không phải security boundary. Nó chỉ tạo seam để Phase 5 thay execution path mà không phải sửa tool implementations lần nữa.

## 6. Event flow sau Phase 3

Trước Phase 3:

```text
observer callback
 + tracer.event
 + capture callback
```

Sau Phase 3:

```text
CompositeEventPublisher
  ├── CallableEventPublisher(observer)
  ├── Tracer publisher adapter
  └── CallableEventPublisher(turn capture)
```

Event semantics và ordering được giữ nguyên.

## 7. Những gì cố ý chưa làm

Phase 3 không implement:

- `AnthropicLLMProvider` thật;
- `OpenAILLMProvider` thật;
- provider-neutral agent loop;
- Tool Harness enforcement;
- Security Harness enforcement;
- Human Approval;
- specialized multi-agent orchestration;
- repository split/migrations.

Execution path vẫn là:

```text
LegacyAgentRuntimeAdapter
   ↓
run_loop()
   ↓
ToolRegistry.execute()
   ↓
Tool function
```

Target Phase 5 vẫn là:

```text
Agent
  ↓
Tool Registry
  ↓
Tool Harness
  ↓
Security Harness
  ↓
External Adapter/API
```

## 8. Metrics

So với Phase 2:

| Surface | Phase 2 | Phase 3 |
|---|---:|---:|
| Python runtime | 59 files / 7,919 LOC | **81 files / 8,508 LOC** |
| Eval Python | 51 files / 5,616 LOC | **52 files / 5,768 LOC** |
| Frontend | 11 files / 3,138 LOC | **11 files / 3,138 LOC** |
| Deterministic test files | 45 | **46** |
| Deterministic test functions | 271 | **279** |

Phần tăng runtime chủ yếu là contracts/adapters/composition root; frontend không đổi behavior.

## 9. Validation

### Static gates

| Gate | Kết quả |
|---|---|
| `python -m compileall -q pu evals` | **PASS** |
| `node --check` toàn bộ JS | **PASS** |
| `python -m pu --help` | **PASS** |
| Vendor SDK boundary scan | **PASS — 0 offender** |
| Direct `PU()` constructor scan ngoài composition root | **PASS — 0 offender** |
| Phase-3 architecture tests | **8 passed** |
| Architecture whiteboard regeneration | **PASS** |

`ruff` không có trong sandbox hiện tại nên lint bằng Ruff không thể chạy; không cài fake/offline replacement.

### Green deterministic gate trong sandbox

Loại đúng các baseline cases đã biết không thuộc Phase 3:

- file runtime-data/git ignore tests cần `.git` + `.env.example` không có trong source ZIP;
- 4 stale product-scope assertions từ Phase 2;
- 1 stale triage event assertion;
- browser-agent cases cần Anthropic SDK package mà sandbox không cài.

Kết quả:

```text
284 passed
13 skipped
5 deselected
```

### Regression comparison Phase 2 ↔ Phase 3

Để phân biệt regression thật với missing SDK, một **test-only stub** được đặt ngoài repository tại `/mnt/data/teststubs`; stub không nằm trong source hoặc snapshot.

Cùng một environment/stub:

```text
Phase 2: 279 passed, 13 skipped, 30 failed
Phase 3: 287 passed, 13 skipped, 30 failed
```

30 failures của hai phase là cùng baseline failure set. Phase 3 thêm đúng 8 architecture tests và cả 8 đều pass.

Không có regression mới được phát hiện do composition/OOP refactor.

## 10. Architecture whiteboard

Whiteboard đã được regenerate để thể hiện:

- `core/container.py → pu/app.py`;
- provider infrastructure boundary;
- ToolRegistry metadata + legacy execution;
- ToolHarness/SecurityHarness contracts đã tồn tại nhưng enforcement chưa active;
- EventPublisher seam.

Artifacts:

```text
docs/whiteboards/pu-architecture.excalidraw
docs/architecture-whiteboard.png
```

## 11. Exit criteria

| Criteria | Status |
|---|---|
| Entry points dựng application qua composition root | ✅ |
| `PU` nhận dependency graph thay vì tự wire infrastructure | ✅ |
| Vendor-free domain/application contracts | ✅ |
| SDK imports confined to provider infrastructure | ✅ |
| Agent runtime được adapter hóa | ✅ |
| Workflow engine được adapter hóa | ✅ |
| Event publishing có OOP seam | ✅ |
| Tool metadata resolve không cần execution | ✅ |
| Legacy behavior không bị regression | ✅ |
| Security enforcement được bật | ❌ — Phase 5 |
| Provider-neutral runtime được bật | ❌ — Phase 4 |

## 12. Bước tiếp theo

Phase 4 nên thay `pu/providers/llm/legacy.py` bằng hai implementation ngang hàng:

```text
LLMProvider
├── AnthropicLLMProvider
└── OpenAILLMProvider
```

và đổi `LegacyAgentRuntimeAdapter`/loop sang `LLMRequest → LLMResponse` provider-neutral, bao gồm streaming, tool calls, finish reason, usage và error normalization.
