# Phase 2 — Scope Prune Report

**Ngày:** 2026-10-04  
**Project:** `pu-agent`  
**Baseline đầu vào:** snapshot Phase 1 (`pu-agent-phase1.zip`)  
**Mục tiêu:** giảm code surface theo requirements hiện tại nhưng **chưa refactor core execution**.

## 1. Kết luận

Phase 2 đã hoàn tất.

Codebase hiện chỉ giữ product capability cần cho PU Agent ở giai đoạn tiếp theo: **Anthropic + OpenAI**, CLI/dashboard, Gmail, Google Calendar, Google Drive, Tavily/Web Search, SQLite/FTS5 memory, graph triage, tracing và evaluation foundations.

Các gateway/provider/tool/backend/benchmark ngoài scope đã được xóa khỏi runtime, packaging, UI và test surface thay vì chỉ bị ẩn bằng flag.

Điểm quan trọng: Phase 2 **không** giả vờ rằng target security architecture đã tồn tại. Core path hiện vẫn là legacy path của baseline:

```text
Agent / Loop
    ↓
ToolRegistry.execute()
    ↓
tool function
    ↓
External API / local side effect
```

`ToolHarness`, `SecurityHarness`, `HumanApprovalService`, `ApprovedActionExecutor` và provider-neutral OOP runtime vẫn là công việc của các phase sau.

## 2. Assumptions

1. Snapshot Phase 1 là rollback/evidence đầy đủ nên capability ngoài scope có thể xóa thật, không cần giữ feature flag chết.
2. Product LLM providers chính thức là **Anthropic** và **OpenAI**.
3. Product integrations cần giữ ở baseline này là **Gmail, Google Calendar, Google Drive, Tavily và OpenTelemetry**.
4. SQLite + FTS5 là persistence baseline/local; hosted memory vendors không cần giữ trong product build.
5. Graph engine vẫn cần cho orchestration sau này, nhưng Phase 2 chỉ giữ workflow `triage`; workflow/demo `gather` upstream được bỏ để tránh mang dependency ngoài scope.
6. Source bundle không có `.git` metadata và sandbox không có package network. Vì vậy các kiểm tra phụ thuộc Git metadata hoặc SDK chưa cài phải được báo là `SKIP/BLOCKED_BY_DEPENDENCY`, không được làm xanh bằng fake production package.

## 3. Những gì đã giữ

### LLM

- `anthropic`
- `openai`

`pu/loop/models.py` hiện chỉ expose hai provider này. Provider-neutral OOP abstraction sẽ được làm ở phase riêng; Phase 2 chưa thay wire contract của legacy loop.

### Integrations

Registry hiện có 5 integration entries:

- `gmail`
- `google_calendar`
- `google_drive`
- `tavily`
- `otel`

Cộng với 2 provider cards Anthropic/OpenAI, Connections/Models surface có tổng cộng **7 entries**.

### Runtime / persistence / orchestration

- local CLI;
- browser dashboard + SSE;
- SQLite + FTS5;
- knowledge layer;
- graph engine;
- `triage` workflow;
- tracing / AgentOps foundation;
- deterministic evals;
- LLM-as-Judge seam;
- procedural skills.

## 4. Những gì đã prune

### Gateways / channels

Đã xóa:

- Telegram;
- Discord;
- WhatsApp;
- Voice;
- gateway runner/supervisor tương ứng.

Interactive surfaces còn lại là CLI và browser dashboard.

### Tools / integrations ngoài scope

Đã xóa:

- Apple tools / Apple Calendar;
- GitHub tool;
- YouTube tool;
- MCP client/runtime;
- experimental delegation/browser/cron/workspace tooling ngoài scope.

### Hosted memory backends

Đã xóa adapters/examples/tests cho:

- Mem0;
- Zep;
- LangMem;
- Supabase;
- Notion episodic store.

Production factory hiện dùng SQLite baseline.

### Providers ngoài product scope

Provider registry và provider logo/UI đã được thu gọn về:

- Anthropic;
- OpenAI.

Các provider-specific assets/config/tests cho những provider còn lại đã được loại khỏi product surface.

### Benchmark / demo surfaces

Đã xóa:

- model arena / compare;
- memory arena;
- compare history;
- coding benchmark;
- shootout scripts;
- gather demo workflow;
- các example/whiteboard upstream chỉ phục vụ những capability trên.

Deterministic scoring primitive và judge foundation vẫn được giữ vì chúng phục vụ Evaluation Harness mục tiêu.

## 5. UI prune

Dashboard vẫn giữ UX baseline của Archive nhưng navigation/data surface đã được thu gọn theo product scope.

Đã loại:

- Arena/Compare UI;
- Voice/microphone UI;
- gateway/channel status ngoài scope;
- Apple/hosted-memory connection cards;
- provider logos ngoài Anthropic/OpenAI;
- API calls/routes của các surface đã prune.

Connection groups hiện là:

```text
Email
Productivity
Knowledge
Research
Observability
```

Architecture whiteboard đã regenerate từ source Phase 2; bản PNG hiện tại không còn mô tả gateway/provider/backend đã xóa.

## 6. Packaging / dependency surface

Optional dependency groups còn:

```text
dev
drive
eval
gcal
gmail
tracing
```

Core dependencies vẫn có Anthropic và OpenAI SDK vì provider abstraction chưa được tách ở Phase 2.

CLI còn:

```text
pu
pu dashboard
pu connections
pu brief
pu skill install <url>
pu connect gmail
pu connect drive
```

`python -m pu --help` trả exit code **0**.

## 7. Giảm code surface

So với Phase 1:

| Surface | Phase 1 | Phase 2 | Thay đổi |
|---|---:|---:|---:|
| Python source (`pu/`) | 88 files / 15,330 LOC | 59 files / 7,919 LOC | -29 files / **-48.3% LOC** |
| Eval Python (`evals/`) | 72 files / 10,847 LOC | 51 files / 5,616 LOC | -21 files / **-48.2% LOC** |
| Frontend HTML/CSS/JS | 12 files / 4,541 LOC | 11 files / 3,138 LOC | -1 file / **-30.9% LOC** |

Byte-level/file inventory so với snapshot Phase 1 (không tính artifact mới dưới `docs/phase2/`):

- **118 files removed**;
- **75 files modified**;
- **0 source files mới** ngoài Phase-2 evidence/report artifacts.

Danh sách exact nằm trong `docs/phase2/inventory.json`.

## 8. Test surface sau prune

Static inventory hiện có:

- **45 deterministic test files**;
- **271 deterministic test functions**;
- **2 judge test files / 5 judge test functions**.

Những test cho capability đã xóa cũng được xóa hoặc rewrite sang contract còn giữ. Ví dụ provider tests chỉ pin Anthropic/OpenAI; connection tests pin đúng integration registry mới; UI tests có negative assertions để các surface đã prune không quay lại ngoài ý muốn.

## 9. Validation gate

### Static/runtime-independent gates

| Gate | Kết quả |
|---|---|
| Python `compileall` | **PASS** |
| JavaScript `node --check` | **PASS** |
| Static local-import scan | **PASS**, không còn dangling `pu.*` import tới module đã xóa |
| Removed-module reference scan | **PASS** trong executable source |
| CLI `python -m pu --help` | **PASS**, exit `0` |
| Architecture whiteboard regeneration | **PASS** |

### Phase-2 scope test gate

Bộ test không yêu cầu SDK runtime đầy đủ:

```text
92 passed
2 skipped
1 dependency-blocked test được deselect có chủ đích
```

Hai `SKIP` là guard dùng Git tracking metadata cho bundled skills. Source bundle không chứa `.git`, nên assertion đó không thể đánh giá trong sandbox; test giờ skip rõ lý do thay vì báo false failure.

Case dependency-blocked là:

```text
test_dashboard_reports_legacy_non_utf8_trace_without_modifying_it
```

Test đi qua `dashboard.collect()` rồi import `pu.memory`, trong khi `pu.memory` baseline vẫn eager-import `anthropic`. Sandbox không có SDK nên case này dừng trước logic cần test.

### Full deterministic collection

```text
178 tests collected
15 collection errors
```

Cả 15 lỗi collection hiện đều bắt nguồn từ missing `anthropic` SDK qua eager imports trong baseline. Kiểm tra environment xác nhận cả `anthropic` và `openai` package đều chưa được cài và sandbox không có outbound package network.

Không thêm stub/fake SDK vào production source để làm xanh test. Eager vendor imports sẽ được xử lý đúng kiến trúc ở phase provider/OOP refactor.

## 10. Security / trust boundary status sau Phase 2

Phase 2 chỉ giảm attack/capability surface bằng cách xóa những integration không cần thiết. Nó **chưa** hoàn thành security model mục tiêu.

Target vẫn là:

```text
Agent
  ↓
Tool Request
  ↓
Tool Registry
  ↓
Tool Harness
  ↓
Security Harness
  ↓
External Integration Adapter
  ↓
External API
```

Với side effect:

```text
Agent
  ↓
ProposedAction
  ↓
HumanApprovalService
  ↓
ApprovedActionExecutor
  ↓
Tool Harness
  ↓
Security re-check
  ↓
External API
```

Không được hiểu việc prune gateway/tool ở Phase 2 là security replacement cho hai boundary trên.

## 11. Tài liệu / provenance

- `ARCHITECTURE.md` đã đánh dấu Phase 2 hoàn tất.
- `README.md`, `SECURITY.md`, `CONTRIBUTING.md`, `docs/README.md` và `docs/integrations.md` đã được đồng bộ với product scope mới.
- Whiteboard upstream và tài liệu về capability đã prune được xóa khỏi working tree; snapshot Phase 1 vẫn giữ nguyên để tra cứu lịch sử.
- `UPSTREAM_NOTICE.md` vẫn giữ provenance/attribution của `Archive.zip`.

## 12. Exit criteria

- [x] Chỉ còn Anthropic/OpenAI trong provider registry.
- [x] Gmail/Google Calendar/Google Drive/Tavily/OTEL còn trong integration surface.
- [x] SQLite + FTS5 là memory persistence baseline.
- [x] Gateway/channel ngoài scope đã xóa.
- [x] Tool/integration ngoài scope đã xóa.
- [x] Hosted memory backend ngoài scope đã xóa.
- [x] Arena/shootout/coding benchmark/gather demo đã xóa.
- [x] UI/routes/assets tương ứng đã prune.
- [x] Test surface được thu gọn và có negative guarantees.
- [x] Python compile + JS syntax pass.
- [x] Không còn dangling import tới module đã prune.
- [x] Architecture whiteboard được regenerate.
- [x] Core execution chưa bị refactor ngoài phạm vi phase.

**Kết luận:** Phase 2 đạt mục tiêu. Codebase hiện đủ nhỏ và đúng scope để bước vào **Phase 3 — OOP domain contracts + composition root** mà không phải kéo theo các capability upstream không còn dùng.
