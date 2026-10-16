# PU Agent

Partner briefing in chat: run `pu dashboard`, open **Trò chuyện**, and ask **“Hôm nay có email gì mới?”**. PU reads the inbox, identifies new partners, researches them and returns full reports with this week’s available appointments in the same conversation. The optional `pu worker` handles background processing. See [setup, processing and limitations](docs/automatic-briefing.md).

PU Agent là codebase đang được tái cấu trúc thành một hệ thống multi-agent cho email, web research, company intelligence, calendar, report generation, memory, human approval, security/evaluation harness và AgentOps.

## Trạng thái migration

- **Phase 0:** freeze baseline + characterization inventory.
- **Phase 1:** đổi identity thành `pu-agent` / package `pu` / `PU_*`.
- **Phase 2:** prune capability ngoài scope.
- **Phase 3:** OOP domain contracts + composition root.
- **Phase 4:** provider-neutral LLM runtime (ban đầu Anthropic/OpenAI; sau Phase 10 đã mở rộng thêm OpenRouter/DeepSeek/GLM/Gemini).
- **Phase 5:** Tool Harness + Security Harness, Registry trở thành metadata-only.
- **Phase 6:** durable Human Approval + write capability boundary.
- **Phase 7:** specialized read-only agents + deterministic research orchestrator.
- **Phase 8:** structured grounded reports + Evaluation Harness.
- **Phase 9:** durable workflow persistence + AgentOps/SLO/observability hardening.
- **Phase 10:** remove migration shims/dead paths và khóa target architecture; deterministic suite không còn baseline failure.

Runtime chat chung vẫn có read capabilities + `action:propose`; mọi side effect trở thành `ProposedAction`. Riêng Phase-7 research workflow dùng 6 specialized agents read-only với exact scoped tool catalogs, nên các agent này không có write capability và cũng không có `action:propose`. Concrete write vẫn chỉ được chạy bởi `ApprovedActionExecutor` sau human approval, exact payload/hash binding và Security Harness re-check.

## Target product scope

Giữ lại:

- LLM providers: **Anthropic, OpenAI, OpenRouter, DeepSeek, GLM (Z.AI) và Gemini**;
- local CLI + browser dashboard;
- Gmail read/sync;
- Google Calendar;
- Google Drive knowledge import;
- web search (Tavily với fallback hiện có);
- SQLite + FTS5 memory/knowledge;
- graph engine + triage workflow;
- tracing / LLM Ops / deterministic + judge evaluation foundations.

Đã loại khỏi product baseline: Telegram, Discord, WhatsApp, Voice, Apple tools/calendar, GitHub tool, YouTube, MCP, experimental delegation/browser/cron, hosted memory vendors, memory/model arena, shootout/coding benchmark.

## Chạy local

```bash
python -m pu
python -m pu dashboard
python -m pu connections
python -m pu brief       # multi-agent morning research workflow
```

Provider được chọn bằng `PU_PROVIDER`. Các key mặc định:

| Provider | `PU_PROVIDER` | API key env | Base URL override |
|---|---|---|---|
| Anthropic | `anthropic` | `ANTHROPIC_API_KEY` | `PU_ANTHROPIC_BASE_URL` |
| OpenAI | `openai` | `OPENAI_API_KEY` | `PU_OPENAI_BASE_URL` |
| OpenRouter | `openrouter` | `OPENROUTER_API_KEY` | `PU_OPENROUTER_BASE_URL` |
| DeepSeek | `deepseek` | `DEEPSEEK_API_KEY` | `PU_DEEPSEEK_BASE_URL` |
| GLM / Z.AI | `glm` | `ZAI_API_KEY` | `PU_GLM_BASE_URL` |
| Gemini | `gemini` | `GEMINI_API_KEY` | `PU_GEMINI_BASE_URL` |

Ví dụ:

```bash
PU_PROVIDER=openrouter
OPENROUTER_API_KEY=...
python -m pu
```

OpenRouter, DeepSeek, GLM và Gemini đi qua OpenAI-compatible wire adapter nhưng vẫn giữ `provider_id` riêng cho tracing, errors, usage và AgentOps.

Xem `ARCHITECTURE.md` để biết living architecture; các implementation report theo phase nằm trong `docs/phase*/`.

## Phase 8: grounded reports và evaluation

Research workflow tạo structured report artifact trước khi render Markdown. Evidence được normalize thành source id bất biến; factual claim phải liên kết tới source id có thật. Evaluation Harness kiểm completeness, source coverage, unsupported/invalid claims, diversity và freshness. LLM-as-Judge chỉ là lớp chấm bổ sung, không thể override deterministic failure hoặc authorize side effect. Report + evaluation artifacts được lưu dưới `.pu/reports/` và hiển thị trong Dashboard **Reports** / **Evaluations**.


## Phase 9: durable persistence and AgentOps

Operational state giờ có versioned SQLite migrations và connection-per-operation repositories cho workflow checkpoints, structured AgentOps events và durable tool audit. Research workflow checkpoint sau từng stage; nếu process restart, `resume` chỉ chạy các stage chưa hoàn tất. Side-effect đang ở trạng thái `EXECUTING` khi crash được **fail-closed** và không tự retry vì outcome bên ngoài có thể không chắc chắn. External read tools có thể retry transient failure theo `PU_TOOL_READ_RETRIES`; write tools không bao giờ auto-retry.

AgentOps dùng `AgentEvent` schema v2 với correlation IDs xuyên workflow/request/agent/tool/execution/approval/report. SQLite là durable ledger; JSONL vẫn là human-readable mirror. Dashboard expose `/metrics` theo Prometheus text format và local observability stack nằm trong `observability/` (OpenTelemetry Collector + Tempo + Prometheus + Grafana).

```bash
OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317 python -m pu dashboard
docker compose -f observability/docker-compose.yml up -d
```

SLO mặc định có thể cấu hình bằng `PU_SLO_LLM_P95_MS`, `PU_SLO_TOOL_ERROR_RATE`, `PU_SLO_WORKFLOW_SUCCESS_RATE`, `PU_SLO_APPROVAL_FAILURE_RATE`, `PU_SLO_SECURITY_BLOCKS_MAX`. SQLite là adapter được ship trong Phase 9; repository/factory boundary đã sẵn sàng để bổ sung PostgreSQL adapter sau này, nhưng Phase 9 **không tuyên bố PostgreSQL runtime support**.


## Phase 10: target architecture locked

Migration-only provider/runtime/tool shims đã được xóa. Product có một composition path qua `create_application()`, một tool execution path qua `DefaultToolHarness → SecurityHarness`, và một provider-neutral LLM contract. Anthropic dùng native adapter; OpenAI dùng native OpenAI adapter; OpenRouter/DeepSeek/GLM/Gemini dùng chung OpenAI-compatible adapter nhưng giữ identity/cấu hình riêng. `search_memory` là read-only; `manage_memory` chỉ còn write (`update|delete`) và bắt buộc đi qua approval boundary. Endpoint override luôn provider-scoped; global `PU_BASE_URL` không còn được hỗ trợ.

Source distribution ship `.gitignore` và generated `.env.example`, nên privacy/config tests chạy được cả từ checkout lẫn ZIP artifact.
