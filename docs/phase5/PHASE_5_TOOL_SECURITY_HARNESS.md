# Phase 5 — Tool Harness + Security Harness

**Ngày:** 2026-10-04  
**Project:** `pu-agent`  
**Baseline đầu vào:** snapshot Phase 4 (`pu-agent-phase4.zip`)  
**Mục tiêu:** loại bỏ execution trực tiếp từ `ToolRegistry`, đưa toàn bộ agent tool-call qua một execution gateway có schema/auth/scope/capability/rate-limit/audit và Security Harness trước khi handler/API được gọi.

## 1. Kết luận

Phase 5 đã hoàn tất.

Runtime path hiện tại:

```text
Agent / AgentRuntime
      ↓
ToolRegistry.resolve()
      ↓
DefaultToolHarness
      ↓
Schema validation
      ↓
Principal authentication check
      ↓
User scope check
      ↓
Agent capability check
      ↓
Rate limit
      ↓
Durable audit: attempted
      ↓
DefaultSecurityHarness
  ├── SSRFPolicy
  ├── SecretEgressPolicy
  └── PromptInjectionPolicy
      ↓
Concrete Tool handler
      ↓
External API / local persistence
      ↓
ToolResult + audit finalize + telemetry
```

`ToolRegistry.execute()` không còn là runtime path. Method này chỉ còn fail-fast để phát hiện stale caller.

## 2. Các module mới

```text
pu/harness/
├── __init__.py
├── audit.py
├── security.py
└── tools.py
```

### `audit.py`

- `ToolAuditRecord`
- `ToolAuditSink`
- `JsonlToolAuditSink`
- `InMemoryToolAuditSink` cho deterministic tests
- canonical `action_hash`
- secret redaction cho cả sensitive key và secret-like value

### `security.py`

- `DefaultSecurityHarness`
- `SSRFPolicy`
- `SecretEgressPolicy`
- `PromptInjectionPolicy`

### `tools.py`

- `DefaultToolHarness`
- strict tool schema validation
- principal/scope/capability enforcement
- `InMemoryRateLimiter`
- audit attempt/finalize
- normalized execution errors

## 3. Tool Registry sau Phase 5

`pu/tools/registry.py` chỉ còn trách nhiệm catalog/metadata và resolve handler cho Harness.

API chính:

```text
resolve(name)
definitions()
schemas()
capabilities()
scopes()
```

Stale path:

```text
ToolRegistry.execute(...) → RuntimeError
```

Source scan xác nhận không còn callsite `registry.execute()` / `tools.execute()` trong product runtime.

## 4. ToolDefinition và ToolContext

`ToolDefinition` hiện mang:

```text
name
description
input_schema
capability
required_scope
side_effect
external
version
```

`ToolContext` hiện mang correlation + authorization context:

```text
actor
principal
request_id
workflow_id
agent_id
tool_call_id
scopes
capabilities
metadata
```

Agent turn tạo `request_id` mới; session id được dùng làm `workflow_id`; LLM tool call id được gắn vào context trước execution.

## 5. Capability mapping của product tools

| Tool | Capability / scope | Side effect | External |
|---|---|---:|---:|
| `create_event` | `calendar:write` | Yes | Có thể, khi Google Calendar bật |
| `list_events` | `calendar:read` | No | Yes |
| `read_gmail_today` | `email:read` | No | Yes |
| `search_web` | `web:read` | No | Yes |
| `search_knowledge` | `knowledge:read` | No | No |
| `save_note` | `memory:write` | Yes | No |
| `manage_memory` | `memory:write` | Yes | No |
| `update_soul` | `memory:write` | Yes | No |
| `create_skill` | `skill:write` | Yes | No |
| `send_message` | `message:draft` | Yes | No — local outbox |

Phase 5 vẫn cấp toàn bộ capability đang đăng ký cho single-agent hiện tại để không làm đổi behavior. Phase 6 sẽ tách proposal/approval/executor và thu hẹp write capability của business agents.

## 6. Schema / Auth / Scope / Capability enforcement

Harness validate trước execution:

1. argument root phải là object;
2. required fields;
3. JSON primitive types;
4. enum;
5. min/max string length khi schema khai báo;
6. unknown fields bị reject theo strict default;
7. phải có authenticated `principal`;
8. principal phải có `required_scope`;
9. agent phải có `capability`.

Mọi denial của tool đã resolve được trả về bằng normalized `ToolResult` và được audit thành `blocked`.

## 7. Rate limit

Phase 5 dùng local `InMemoryRateLimiter` theo `(principal, tool)`.

Default:

```text
PU_TOOL_RATE_LIMIT_PER_MINUTE=120
```

Đây là assumption phù hợp local/single-process. Production multi-instance cần shared rate-limit state/gateway.

## 8. Durable audit

Product composition root dùng:

```text
.pu/traces/tool-audit.jsonl
```

Mỗi execution có `execution_id` và canonical `action_hash`.

Lifecycle:

```text
attempted → succeeded
attempted → failed
attempted → blocked
```

Schema/auth/scope/capability/rate denials được audit trực tiếp thành `blocked`.

Audit fields chính:

```text
execution_id
request_id / workflow_id / agent_id / tool_call_id
actor / principal
tool / capability / scope
side_effect / external
action_hash
redacted arguments
status / policy / reason
latency_ms
result summary / error
```

Không lưu full tool output vào audit; result summary chỉ giữ kích thước output để tránh nhân bản email/document/user data vào security log.

Sensitive keys và secret-like values (`Bearer`, API key, token...) được redact trước khi ghi JSONL.

## 9. Security Harness

### SSRFPolicy

URL fields bị chặn nếu:

- không dùng `http/https`;
- localhost;
- private IP;
- loopback;
- link-local;
- multicast/reserved/unspecified.

### SecretEgressPolicy

Với `external=True`, arguments được scan trước outbound execution. Secret/token/API-key-like content bị block.

### PromptInjectionPolicy

Policy chỉ kích hoạt mạnh khi caller đánh dấu provenance `untrusted_input=True`.

- read-only tool vẫn có thể nghiên cứu nội dung prompt injection;
- side-effect tool bị block khi untrusted content chứa high-confidence instruction-like patterns.

Policy này không thay thế capability/approval boundary. Phase 6 mới là enforcement quyết định cho sensitive writes.

## 10. Runtime integration

`pu/loop/agent.py` hiện chỉ gọi:

```text
ToolHarness.execute(...)
```

Nó không gọi registry execution.

`LegacyAgentRuntimeAdapter` chỉ còn tên migration; product composition inject `DefaultToolHarness` vào runtime.

`LegacyDirectToolHarness` cũng đã được đổi thành compatibility wrapper qua enforced harness, không còn bypass Registry → handler.

## 11. Observability / UI

Harness emit:

```text
tool_attempt
tool_blocked
security
tool_complete
tool
```

Các event đi qua observer/Tracer hiện tại nên có JSONL/OTel visibility.

Dashboard thay đổi:

- Tools page hiển thị capability, scope, side-effect/read-only, external/local;
- thêm Security page;
- Security page hiển thị số audit event, số block, policy block counts và recent audit chain;
- wording Phase-2 legacy đã được loại bỏ.

## 12. Deterministic tests mới

`evals/deterministic/test_phase5_tool_security_harness.py` kiểm tra:

- Registry metadata-only;
- direct execute fail-fast;
- schema validation;
- authentication;
- scope enforcement;
- capability enforcement;
- rate limiting;
- audit attempt-before-execution;
- finalize success;
- correlation ids + action hash;
- secret redaction trong JSONL;
- secret-egress block;
- SSRF block;
- prompt-injection block cho untrusted side effect;
- loop chỉ gọi ToolHarness;
- composition root dùng durable Harness + Security Harness;
- source scan không còn direct registry execution callsite.

Targeted Phase-3/4/5 + dashboard/static checks: **PASS**.

## 13. Regression gate

Full deterministic suite:

```text
310 passed
13 skipped
30 failed
```

30 failures trùng đúng baseline Phase 4:

- source ZIP không có `.git` / `.env.example`, nên runtime-data/git-ignore tests thiếu fixture;
- 4 stale product-scope assertions;
- 1 stale triage-event assertion.

Green gate sau khi loại đúng baseline fixtures/assertions:

```text
310 passed
13 skipped
5 deselected
```

So với Phase 4 green gate `299 passed`, Phase 5 thêm **11 parametrized test passes** mà không thêm regression failure.

## 14. Static gates

| Gate | Kết quả |
|---|---|
| `python -m compileall -q pu evals` | PASS |
| `node --check` toàn bộ JS | PASS |
| `python -m pu --help` | PASS |
| Product runtime registry-execute callsites | 0 |
| Direct concrete `tool.execute()` ngoài Harness | 0 |
| Phase-5 harness tests | 11/11 PASS |
| Dashboard/static targeted gate | PASS |
| Architecture whiteboard regeneration + visual QA | PASS |

## 15. Assumptions / giới hạn còn lại

1. App hiện local-first/single-user; `principal="local-user"` là trusted application identity, chưa phải browser/session OAuth identity.
2. Rate limiter đang in-memory, chưa phù hợp multi-instance production.
3. Prompt-injection detection là một policy signal, không được coi là cơ chế bảo vệ duy nhất.
4. Human Approval / approval hash binding **chưa** implement trong Phase 5; đây là Phase 6.
5. Business agents chưa tách riêng nên single agent hiện vẫn sở hữu toàn bộ registered capabilities để giữ baseline behavior.
6. Dashboard/admin endpoints cũ không phải agent tool-call path; Phase 5 exit criterion tập trung loại bypass trong Agent/Loop. Các admin write actions sẽ được rà lại khi Approval/Security UI được harden ở phase sau.

## 16. Exit criteria

- ✅ `Agent → Tool → Tool Harness → Security → Handler/API` được enforce trong runtime.
- ✅ Tool Registry không còn runtime execution responsibility.
- ✅ Schema/auth/scope/capability/rate-limit chạy trước side effect.
- ✅ Durable audit attempt xảy ra trước Security/handler execution.
- ✅ Security Harness có SSRF, secret-egress và untrusted prompt-injection policies.
- ✅ Tool calls có request/workflow/agent/tool-call correlation ids.
- ✅ Dashboard có visibility cho capability/security/audit.
- ✅ Không có regression mới so với Phase 4 baseline.

Phase tiếp theo: **Phase 6 — Human Approval + write capability boundary**.
