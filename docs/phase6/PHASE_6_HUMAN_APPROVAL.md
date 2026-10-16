# Phase 6 — Human Approval + Write Capability Boundary

> Ngày thực hiện: 2026-10-04  
> Baseline đầu vào: Phase 5 — Tool Harness + Security Harness  
> Mục tiêu: agent thường không thể thực thi side effect trực tiếp; mọi write action phải trở thành proposal, được human duyệt chính xác payload, rồi mới được một executor riêng thực thi qua Tool Harness + Security Harness.

## 1. Kết quả

Phase 6 đã hoàn tất boundary bắt buộc:

```text
Agent / AgentRuntime
  ↓ propose_<tool>
ToolRegistry
  ↓ resolve metadata
DefaultToolHarness
  ↓ validate + auth + scope + rate + audit
  ↓ Security Harness (proposal-time)
ProposedAction
  ↓ SQLite
HumanApprovalService
  ↓ PENDING → APPROVED / REJECTED / EXPIRED
ApprovedActionExecutor
  ↓ atomic one-shot claim
Tool Harness
  ↓ approval/hash binding
  ↓ Security Harness (execution-time re-check)
Concrete Tool Handler
  ↓
External API / local persistence
```

Human approval không phải là prompt instruction. Đây là capability boundary được enforce bằng code và durable state.

## 2. Capability model

Concrete tool definitions vẫn giữ quyền thật, ví dụ:

| Tool | Target capability | Agent thấy | Side effect |
|---|---|---|---:|
| `create_event` | `calendar:write` | `propose_create_event` | Yes |
| `list_events` | `calendar:read` | `list_events` | No |
| `save_note` | `memory:write` | `propose_save_note` | Yes |
| `send_message` | `message:draft` | `propose_send_message` | Yes |
| `search_web` | `web:read` | `search_web` | No |
| `search_knowledge` | `knowledge:read` | `search_knowledge` | No |
| `manage_memory` | `memory:write` | `propose_manage_memory` | Yes |
| `update_soul` | `memory:write` | `propose_update_soul` | Yes |
| `create_skill` | `skill:write` | `propose_create_skill` | Yes |

Agent capabilities hiện tại chỉ gồm:

```text
action:propose
calendar:read
knowledge:read
web:read
```

Không có concrete write capability trong agent context.

Để không tạo một migration gãy behavior cho model cũ, stale call dùng tên write cũ như `create_event` cũng được Tool Harness chuyển thành proposal khi actor là agent. Nó không execute handler.

## 3. Durable approval model

Thêm SQLite table `proposed_actions`. State machine:

```text
PENDING ──approve──→ APPROVED ──claim──→ EXECUTING ──→ EXECUTED
   │                    │                         └──→ FAILED
   ├──reject──────────→ REJECTED
   └──TTL─────────────→ EXPIRED
```

`APPROVED` có thể expire trước execution.

Mỗi row giữ exact action payload và correlation data gồm action id, tool, arguments, hash, approval hash, principal, agent/request/workflow/tool-call ids, capability/scope, risk, timestamps, human actor/note, execution id/result/error và revision.

Default TTL:

```text
PU_APPROVAL_TTL_MINUTES=60
```

## 4. Exact payload binding

Approval được bind với:

```text
action_hash = hash(tool_name + normalized_arguments)
```

Khi human approve:

```text
approved_action_hash == action_hash
```

Nếu payload được edit:

1. backend validate lại payload theo exact tool schema;
2. hash đổi;
3. revision tăng;
4. status quay lại `PENDING`;
5. approved hash bị xóa;
6. bắt buộc re-approve.

Executor từ chối hash mismatch trước khi concrete handler có thể chạy.

## 5. One-shot execution

`SQLiteApprovalRepository.claim_execution()` dùng transaction `BEGIN IMMEDIATE` và compare-and-set `APPROVED → EXECUTING`.

Điều này chặn:

- double click `Approve & execute`;
- HTTP retry trùng;
- hai worker cùng claim một approval;
- replay một approval đã execute.

Concrete write handler chỉ có thể chạy sau khi một execution id claim thành công.

## 6. Security re-check

Human approve không bypass security.

Proposal được Security Harness kiểm tra trước khi vào queue. Sau approval, `ApprovedActionExecutor` tạo một execution context mới, chỉ cấp đúng scope/capability của action, bind approval id/hash, rồi gọi lại Tool Harness. Security Harness được chạy lần thứ hai ngay trước handler.

Nếu execution-time policy block, approval kết thúc ở `FAILED`; handler không chạy.

## 7. Approval UI/API

Dashboard theo visual language của Archive có thêm `Approvals` view:

- pending counter;
- queue + detail;
- exact JSON payload;
- tool, target capability, scope;
- risk + external/local;
- action hash;
- expiry + revision;
- human decision actor;
- execution result/error;
- `Save edit → re-approve`;
- `Approve & execute`;
- `Reject`.

Frontend chỉ POST decision/edit tới `/api/approvals`. Browser không gọi external integration trực tiếp.

Backend edit path validate lại JSON payload bằng schema của concrete tool trước khi update proposal.

## 8. Observability

Bổ sung correlation/event data:

```text
approval_requested
approval_edited
approval_approved
approval_rejected
approval_id
approval_status
action_tool
action_hash
```

Durable tool audit vẫn ghi attempt trước execution và ghi policy/security block cùng approval-bound context.

## 9. Security assumptions và limitation

### 9.1 Local human identity

Phase 6 vẫn là local-first/single-user. Dashboard decision actor hiện là:

```text
local-dashboard-user
```

Đây là trusted application identity, chưa phải end-user auth. Production multi-user cần lấy actor từ authenticated session/SSO và authorize quyền approve theo tenant/workspace.

### 9.2 Approval payload là sensitive application data

`proposed_actions.arguments` giữ exact payload để human review và hash binding có ý nghĩa. Dữ liệu này không được xem là telemetry. Production cần retention/encryption/access policy phù hợp.

Tool audit vẫn dùng redacted arguments.

### 9.3 Mixed-purpose memory tool

`manage_memory` hiện có cả semantics đọc/ghi nhưng tool definition được đánh `side_effect=True`, nên Phase 6 conservatively approval-gate toàn bộ tool. Phase 7 nên split read/write memory capability để UX chính xác hơn.

### 9.4 Email write

Source sau Phase 2 chủ yếu có Gmail read/sync; chưa có concrete Gmail send/reply product tool. Khi bổ sung ở specialized Email Agent, write tool phải inherit boundary Phase 6, không được có direct execution path riêng.

## 10. Validation

### Targeted architecture/approval/tool-trigger gate

```text
48 passed
11 skipped
```

### Green deterministic regression gate

```text
322 passed
13 skipped
5 deselected
```

### Full deterministic suite

```text
322 passed
13 skipped
30 baseline failures
```

30 failure là đúng nhóm baseline đã biết từ Phase 4/5: source ZIP không có Git metadata/`.env.example` và 5 stale assertions ngoài Phase 6. Không có failure mới do approval migration.

### Static/package checks

- Python `compileall`: PASS.
- JavaScript `node --check`: PASS.
- `python -m pu --help`: PASS.
- direct `ToolRegistry.execute()` product callsite: 0.
- runtime cấp `tools.capabilities()` trực tiếp cho agent: 0.
- Phase-6 tests khóa write boundary: 12 tests.

## 11. Exit criteria

- ✅ Agent không sở hữu concrete write capability.
- ✅ Side-effect call từ agent chỉ tạo durable proposal.
- ✅ Stale direct write call không bypass proposal path.
- ✅ Approval bind exact payload bằng hash.
- ✅ Edit bắt buộc re-approve.
- ✅ Reject/expire không execute.
- ✅ Execution one-shot/atomic.
- ✅ Approved executor chỉ nhận least-privilege scope/capability của một action.
- ✅ Security Harness chạy lại sau approval.
- ✅ Approval UI không gọi external API trực tiếp.
- ✅ Regression gate không có failure mới.

## 12. Phase tiếp theo

Phase 7 sẽ tách specialized agents và deterministic orchestrator trên các boundary đã khóa:

- Email Agent;
- Web Research Agent;
- Company Info Agent;
- Calendar Agent;
- Memory Agent;
- Report Generation Agent;
- orchestration fan-out/fan-in;
- split mixed read/write capabilities nơi cần thiết.

Human Approval tiếp tục là application service/capability boundary; LLM không quyết định approve/reject.
