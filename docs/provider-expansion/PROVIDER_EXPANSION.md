# Provider Expansion — OpenRouter, DeepSeek, GLM, Gemini

Ngày cập nhật: 2026-10-05

## Mục tiêu

Mở rộng provider registry sau Phase 10 mà không thay đổi `LLMProvider` contract, agent runtime, Tool Harness, Security Harness, approval, evaluation hay AgentOps boundaries.

## Provider hiện hỗ trợ

| Provider | ID | API key env | Default base URL | Main model | Small model |
|---|---|---|---|---|---|
| Anthropic | `anthropic` | `ANTHROPIC_API_KEY` | native | `claude-sonnet-5` | `claude-haiku-4-5-20251001` |
| OpenAI | `openai` | `OPENAI_API_KEY` | native | `gpt-5.5` | `gpt-4.1-mini` |
| OpenRouter | `openrouter` | `OPENROUTER_API_KEY` | `https://openrouter.ai/api/v1` | `nvidia/nemotron-3-super-120b-a12b:free` | `google/gemma-4-26b-a4b-it:free` |
| DeepSeek | `deepseek` | `DEEPSEEK_API_KEY` | `https://api.deepseek.com` | `deepseek-v4-pro` | `deepseek-v4-pro` |
| GLM / Z.AI | `glm` | `ZAI_API_KEY` | `https://api.z.ai/api/paas/v4` | `glm-5.2` | `glm-5-turbo` |
| Gemini | `gemini` | `GEMINI_API_KEY` | `https://generativelanguage.googleapis.com/v1beta/openai/` | `gemini-3.5-flash` | `gemini-3.1-flash-lite` |

GLM hỗ trợ thêm Coding Plan endpoint `https://api.z.ai/api/coding/paas/v4` trong Models UI.

## Implementation

- Thêm `OpenAICompatibleLLMProvider` để tái sử dụng OpenAI Chat Completions translation cho OpenRouter/DeepSeek/GLM/Gemini.
- Mỗi provider vẫn giữ `provider_id` riêng cho error normalization, tracing, usage/cost và AgentOps.
- Không có branch vendor mới trong agent/runtime/workflow/memory.
- Provider-specific key/base URL/default models/model catalogs nằm ở `pu/providers/llm/catalog.py`.
- Model picker gọi `/api/models?provider=<id>`; nếu live catalog lỗi, UI dùng known defaults và vẫn cho nhập model id thủ công.
- Slash-qualified model ids (`provider/model`) được coi là OpenRouter slug và không carry sang direct provider khi switch.
- Bổ sung card logo cho cả bốn provider mới.
- Models edit modal sửa Base URL control: text input cho endpoint override thường, dropdown cho provider có nhiều endpoint (GLM).

## Validation

- Provider-focused deterministic gate: `56 passed`.
- Full deterministic suite: `388 passed, 13 skipped, 0 failed`.
- Full eval tree trong môi trường không có live credentials: `388 passed, 41 skipped, 0 failed`.
- Python compile: PASS.
- JavaScript syntax: PASS.
- Generated `.env.example`: PASS.
- CLI help: PASS.
- Dashboard data smoke: trả đủ 6 provider cards với display names đúng.

Live API calls không được chạy vì artifact không chứa user credentials. Các endpoint/default model được cấu hình theo tài liệu provider tại thời điểm 2026-10-05; Models UI vẫn cho phép chọn catalog live hoặc nhập model id khác.
