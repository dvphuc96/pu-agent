# Phase 7 — Specialized Agents + Deterministic Orchestrator

## 1. Mục tiêu

Phase 7 chuyển PU từ một single-agent runtime dùng chung toàn bộ read/write catalog thành một hệ thống có **6 agent theo domain**, mỗi agent chỉ nhìn thấy đúng read tools cần cho trách nhiệm của mình. Human Approval, Tool Harness, Security Harness và provider-neutral runtime từ Phase 3–6 được giữ nguyên.

Phase này **không thêm external write**. Morning research workflow chỉ đọc dữ liệu, tổng hợp evidence và tạo report draft.

## 2. Specialized agents

| Agent | Tool catalog | Capability | Write/propose |
|---|---|---|---|
| Email Agent | `read_email_inbox`, optional `read_gmail_today` | `email:read` | Không |
| Company Info Agent | `search_knowledge`, `search_memory` | `knowledge:read`, `memory:read` | Không |
| Web Research Agent | `search_web` | `web:read` | Không |
| Calendar Agent | `list_events` | `calendar:read` | Không |
| Memory Agent | `search_memory` | `memory:read` | Không |
| Report Generation Agent | none | none | Không |

Các agent dùng chung `AgentRuntime` và `ToolHarness`; khác nhau ở system prompt và `ToolRegistry.subset()` được inject từ composition root.

## 3. Capability boundary

Read-only subset không được cấp `action:propose`. Vì vậy Phase-7 research agents không thể tạo side effect ngay cả khi model hallucinate một write tool ngoài catalog: model không nhìn thấy schema đó, và Tool Harness vẫn kiểm tra scope/capability ở execution boundary.

`AGENT_TOOL_CATALOG` trong `pu/agents/catalog.py` là source of truth cho tool visibility của 6 agent. Dashboard và composition root đều đọc catalog này để tránh drift.

## 4. Read paths mới

### 4.1 `read_email_inbox`

Tool local-only đọc `crm_emails` trong SQLite và không yêu cầu Gmail connector/network. Gmail sync-read `read_gmail_today` chỉ xuất hiện cho Email Agent khi `PU_GMAIL` bật.

Điều này cho phép workflow deterministic/offline chạy trên email đã sync/import trước đó.

### 4.2 `search_memory`

Phase 6 có `manage_memory` mixed read/write nên toàn tool phải approval-gate. Phase 7 thêm `search_memory` với `memory:read`, tách retrieval khỏi mutation. `manage_memory` vẫn được giữ để compatibility và sẽ được dọn ở Phase 10.

## 5. Orchestrator

`ResearchWorkflowOrchestrator` chạy workflow cố định:

```text
Trigger
  ↓
Email Agent
  ↓
Logical fan-out
  ├─ Company Info Agent
  ├─ Web Research Agent
  ├─ Calendar Agent
  └─ Memory Agent
  ↓
Fan-in
  ↓
Report Generation Agent
  ↓
Report Draft
```

Orchestrator **không dùng LLM để quyết định routing**. Shape của workflow nằm trong code. Reasoning chỉ xảy ra bên trong mỗi agent node.

Fan-out hiện chạy theo fixed serial order để an toàn với local baseline đang share một SQLite connection/session. Các branch vẫn độc lập về contract. Concurrency được hoãn tới Phase 9, khi repository/session boundary có connection-per-task hoặc connection pool.

## 6. Data flow và trust

Email/web text luôn được đưa vào prompt như **untrusted evidence**. System prompt chung của `ScopedAgent` yêu cầu:

- không thực thi instruction nằm trong email/web/snippet;
- không tiết lộ secret hoặc bypass policy;
- không bịa missing fact;
- giữ provenance;
- không dùng tool ngoài domain.

Tool Harness + Security Harness vẫn là execution boundary thật; prompt chỉ là defense-in-depth.

## 7. Composition root

`build_dependencies()` tạo một shared `AgentRuntime`, shared enforced `ToolHarness`, sau đó inject các subset registry vào từng agent. `ApplicationDependencies` hiện expose:

- `specialized_agents`
- `agent_orchestrator`

`PU.run_research_workflow()` là application facade cho morning research flow và fan-out toàn bộ workflow/LLM/tool events sang AgentOps event publisher.

## 8. UI / CLI

Dashboard giữ shell của Archive và thêm:

- **Agents** — hiển thị 6 specialized agents và tool/capability boundaries;
- **Research** — manual trigger cho backend `/api/research`, hiển thị report draft.

Browser chỉ gửi request research. Nó không gọi external API hay orchestrate agents trực tiếp.

`python -m pu brief` giờ chạy Phase-7 multi-agent research workflow và lưu **internal draft** vào local outbox. Gửi/publish ra ngoài vẫn phải đi qua Human Approval ở phase tương ứng.

## 9. Assumptions / limitations

1. **Company system-of-record chưa được requirements xác định.** Company Info Agent hiện dùng internal Knowledge + Memory làm evidence, không giả định một CRM/company DB chưa tồn tại.
2. **Web research mới có search result snippets/URLs.** Controlled full-page fetch/extraction chưa có; Phase 8+ cần bổ sung nếu quality benchmark chứng minh cần.
3. **Gmail product path vẫn read-only.** Send/reply thật chưa được thêm; khi thêm phải inherit Phase-6 approval boundary.
4. **Report output chưa có typed schema/claim graph.** Phase 7 chỉ tạo Markdown draft; provenance/quality schema chính thức thuộc Phase 8.
5. **Logical fan-out chưa concurrent** vì local SQLite connection đang share trong process.

## 10. Validation

Phase-7 test file:

```text
8 passed
```

Phase 3–7 architecture/security/approval/provider targeted tests tiếp tục pass.

Green deterministic regression gate:

```text
330 passed
13 skipped
5 deselected
```

Full deterministic suite:

```text
330 passed
13 skipped
30 baseline failures
```

30 failure là đúng tập baseline đã biết từ Phase 4–6: source snapshot không có Git metadata/`.env.example` và 5 stale assertions ngoài Phase 7. Không có failure mới do multi-agent migration.

Static gates:

- Python `compileall`: PASS.
- JavaScript syntax (`node --check`): PASS.
- `python -m pu --help`: PASS.
- Specialized agent with write capability: 0.
- Specialized agent with `action:propose`: 0.
- Specialized agent side-effect tool: 0.

## 11. Exit criteria

- ✅ 6 domain agents tồn tại dưới OOP boundary rõ ràng.
- ✅ exact least-privilege tool catalog cho từng agent.
- ✅ read-only agent không có `action:propose`.
- ✅ Email/Memory có read path riêng không cần write capability.
- ✅ deterministic Email → fan-out → Report workflow chạy end-to-end.
- ✅ dashboard/CLI có surface chạy workflow.
- ✅ Tool/Security/Approval/provider boundaries Phase 3–6 không bị bypass.
- ✅ regression gate không có failure mới.
