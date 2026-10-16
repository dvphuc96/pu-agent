# Security policy — PU Agent

PU Agent đang được refactor theo kiến trúc trong `ARCHITECTURE.md`. Tài liệu này mô tả security posture của **baseline sau Phase 2** và không được hiểu là các harness mục tiêu đã hoàn thành.

## Scope hiện tại

Các external integrations còn trong product scope là Anthropic, OpenAI, Gmail, Google Calendar, Google Drive, Tavily/Web Search và OpenTelemetry. Runtime interactive surfaces là CLI và local browser dashboard.

Secrets phải đi qua environment/configuration; không ghi API key, OAuth token hoặc credential vào source, trace, report hay UI. Dashboard chỉ được hiển thị trạng thái/masked value khi cần.

SQLite state dưới `.pu/` là dữ liệu local của người dùng. Không commit database, traces, outbox, OAuth token hoặc runtime artifacts vào repository.

## Boundary hiện tại và boundary mục tiêu

Phase 2 vẫn giữ legacy execution path của baseline: `Agent/Loop -> ToolRegistry -> tool`. Vì vậy write-capability security **chưa đạt target cuối cùng**.

Target bắt buộc của các phase sau là:

`Agent -> Tool -> Tool Harness -> Security Harness -> External API`

và với hành động có side effect:

`Agent -> ProposedAction -> Human Approval -> ApprovedActionExecutor -> Tool Harness -> Security re-check -> External API`

Không thêm external write tool mới bằng cách gọi SDK trực tiếp từ agent/loop. Capability mới phải được đưa qua boundary mục tiêu khi Tool/Security/Approval Harness được triển khai.

## Báo cáo lỗ hổng

Không đưa secrets, token hoặc dữ liệu người dùng thật vào issue công khai. Khi chưa có security contact/repository canonical được cấu hình, hãy báo trực tiếp cho maintainer của project trong kênh nội bộ đang dùng để phát triển PU Agent.
