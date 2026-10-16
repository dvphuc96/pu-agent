# Integrations — Phase 2 baseline

Product scope hiện giữ hai LLM providers và năm external integrations.

| Nhóm | Integration | Vai trò baseline |
|---|---|---|
| LLM | Anthropic | provider cho agent loop |
| LLM | OpenAI | provider cho agent loop |
| Email | Gmail | đọc/sync inbox hiện tại |
| Productivity | Google Calendar | đọc/tạo calendar event theo capability baseline |
| Knowledge | Google Drive | import tài liệu vào local knowledge |
| Research | Tavily | web search |
| Observability | OpenTelemetry | optional OTLP trace export |

Phase 2 chỉ thu gọn capability surface. External write security và approval boundary cuối cùng chưa được hoàn thiện; xem `ARCHITECTURE.md` cho Tool Harness, Security Harness và Human Approval flow mục tiêu.
