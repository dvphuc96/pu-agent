# Design: Chat-triggered partner research + readable reports (HTML/PDF)

Ngày: 2026-10-12
Trạng thái: đã được user duyệt (phê duyệt từng phần qua hội thoại + diagram workflow + bảng chức năng 7 agents)

## Bối cảnh & mục tiêu

Hiện tại research workflow (7 agents: email → fanout company/web/calendar/memory → report → evaluation) chỉ kích hoạt được bằng nút Research trên dashboard. Đề bài gốc (`requirement.md`) yêu cầu hệ thống tự chạy khi phát hiện email từ đối tác mới, và report phải được người dùng đọc rồi phê duyệt trước khi chuyển tiếp.

Mục tiêu: khi user hỏi trong chat ("hôm nay có email mới nào không?"), agent tự đọc inbox → tự nhận diện email đối tác mới → tự chạy research workflow → trả report dễ đọc (PDF/HTML) → chờ human duyệt như hiện tại.

## Quyết định đã chốt với user

1. **Trigger**: chat-triggered (không làm scheduler mỗi sáng, không mở rộng triage graph, không dashboard auto-poll).
2. **Nhận diện "đối tác mới"**: LLM tự phán đoán từ nội dung email + người gửi (không rule theo domain).
3. **Cách tiếp cận**: tool mới `research_partner` cho agent loop tự gọi — tái dụng 100% workflow cũ.
4. **Đầu ra**: report render HTML + PDF để human đọc, thay vì chỉ `.json`/`.md` trong `.pu/reports/`.

## Thiết kế

### 1. Tool `research_partner` — `pu/tools/research.py`

- `make_tool(research_runner) -> Tool`; `research_runner` là callable `(prompt, observer) -> ResearchWorkflowResult` (inject từ app/container — tool không phụ thuộc app, giữ tách lớp như `calendar`/`gmail`).
- **Input schema**: `{company: string (bắt buộc), website?: string, sender_email?: string, context?: string}` — agent điền từ email nó vừa đọc.
- **Hành vi**: ghép prompt có cấu trúc từ input → gọi `research_runner` → từ `ResearchWorkflowResult` (structured_report, evaluation, report) trả về markdown tóm tắt cho chat: executive summary, verdict evaluation (đạt/không + score), đường dẫn file report (PDF nếu có, fallback HTML), `workflow_id`.
- **`wants_notify=True`**: forward event các stage (fanout, report, evaluation…) vào observer của chat → dashboard stream realtime khi workflow chạy.
- **Metadata**: `capability="research:run"`, `required_scope="research:run"`, `side_effect=False`, `external=False` (research chỉ đọc + ghi report nội bộ; các tool con trong workflow tự đi qua Tool Harness riêng).
- **Description dạy agent**: dùng sau khi đọc inbox và phát hiện email từ đối tác/khách hàng mới; luôn kết thúc bằng đề nghị user duyệt report (forward email / lưu knowledge) — theo pattern description của `read_gmail_today`, không đụng system prompt.
- **Human approval không đổi**: forward email / save knowledge vẫn là side-effect tools propose-only, ApprovedActionExecutor vẫn là nơi duy nhất được ghi.

### 2. Đăng ký — cuối `PU.__init__` (`pu/app.py`)

Container build registry **trước khi** orchestrator tồn tại (orchestrator lại phụ thuộc subset của registry) → thay vì thêm param vào `build_registry`, đăng ký tool ở cuối `PU.__init__` bằng `self.tools.register(...)` với runner là closure bọc `self.run_research_workflow` (tái dụng luôn CompositeEventPublisher: event đi cả vào chat observer lẫn AgentOps publisher). ToolRegistry hỗ trợ đăng ký muộn: `resolve`/`scopes`/`agent_capabilities` tính động mỗi turn.

### 3. Render HTML + PDF — `pu/reporting/render.py`

- `render_html(report: StructuredReport, evaluation=None) -> str`: template HTML cố định, CSS inline (không thêm jinja): tiêu đề, executive summary, các section theo schema, bảng sources/provenance, khung verdict evaluation. **HTML-escape toàn bộ nội dung** — report chứa dữ liệu web/email untrusted.
- `write_pdf(html: str, path: Path) -> Path | None`: dùng `xhtml2pdf` (optional extra `pdf` trong `pyproject.toml`; chọn vì pip-only, không cần system lib như weasyprint). Lib chưa cài → trả None, không crash (fail-open như pattern gmail/google_calendar).
- **Font tiếng Việt**: xhtml2pdf mặc định Helvetica Latin-1 làm vỡ dấu — `write_pdf` dò font Unicode theo thứ tự path hệ thống (macOS: `/System/Library/Fonts/Supplemental/Arial Unicode.ttf`; Linux: DejaVu), tìm thấy thì đăng ký TTF; không thì fallback mặc định + ghi chú trong output.

### 4. Tích hợp repository — `pu/reporting/repository.py`

`FileReportRepository.save()` hiện ghi `.json` + `.md` vào `.pu/reports/` → thêm `.html` (luôn) + `.pdf` (khi lib có). Đặt ở repository nên cả nút Research trên dashboard lẫn chat-triggered đều ra PDF. Renderer inject được (dependency injection) để test không cần lib.

### 5. Error handling

- Workflow fail/exception → tool trả chuỗi lỗi rõ ràng + `workflow_id` + gợi ý resume (đã có `resume_research_workflow`); chat turn không crash.
- Evaluation gate fail (score < ngưỡng) → vẫn trả report, ghi rõ "chưa đạt ngưỡng chất lượng" — agent nói thật với user.

## Luồng demo sau khi làm

```
User: "hôm nay có email mới nào không?"
Agent: [read_email_inbox] → bảng email
Agent: "Thấy email từ ProtonX — đối tác mới, em research giúp anh nhé."
Agent: [research_partner company=ProtonX website=protonx.co]
       ← dashboard stream: email stage → fanout 4 agents → report → evaluation
Agent: tóm tắt report + score + "report đầy đủ ở .pu/reports/report-xxx.pdf"
       "Anh muốn chuyển tiếp qua email hay lưu vào knowledge?" → user duyệt
```

Map đủ 7 agents của requirement: Email (read + LLM detect), Web Research + Company Info + Calendar + Report Generation + Memory (workflow fanout, sẵn có), Human Approval (propose-only + ApprovedActionExecutor, không đổi).

## Testing (TDD)

`evals/deterministic/test_research_tool.py`:
- schema + metadata đúng (tên, scope, side_effect, description dạy dùng sau inbox + đề nghị duyệt)
- fn gọi runner với prompt chứa company/website; forward notify qua wants_notify
- output có executive summary, evaluation verdict, đường dẫn report, workflow_id
- runner raise → tool trả chuỗi lỗi chứa workflow_id nếu có, không raise
- `create_application` (composition root) đăng ký tool vào registry; scope `research:run` xuất hiện trong `tools.scopes()`; tool không phải side-effect (agent thấy trực tiếp, không qua `propose_`)

`evals/deterministic/test_report_rendering.py`:
- `render_html` chứa title/executive summary/sections/sources/verdict; escape đúng nội dung có ký tự HTML
- `write_pdf` skipif khi xhtml2pdf chưa cài; có thì assert file tồn tại + magic bytes `%PDF`
- `FileReportRepository.save` ghi thêm `.html` (và `.pdf` khi renderer trả path) qua renderer stub

## Out of scope

- Scheduler "mỗi sáng" (cron)
- Node mới trong triage graph
- Thay đổi UI dashboard
- Tự động mở file PDF (user tự mở theo path agent trả)
