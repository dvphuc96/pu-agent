# Phase 4 — Provider-neutral Anthropic/OpenAI

**Ngày:** 2026-10-04  
**Project:** `pu-agent`  
**Baseline đầu vào:** snapshot Phase 3 (`pu-agent-phase3.zip`)  
**Mục tiêu:** đưa Anthropic và OpenAI về cùng một domain contract, không để agent/runtime/workflow/memory phụ thuộc vendor SDK hay wire format.

## 1. Kết luận

Phase 4 đã hoàn tất.

Provider selection hiện chỉ diễn ra ở composition/provider factory:

```text
Settings(PU_PROVIDER)
        ↓
create_llm_provider()
        ↓
      LLMProvider
       /      \
      /        \
Anthropic     OpenAI
Adapter       Adapter
      \        /
       \      /
      AgentRuntime
```

Agent loop, memory retrieval gate, consolidation, triage graph, quick reply và evaluation judge đều sử dụng `LLMProvider`.

## 2. Provider package mới

```text
pu/providers/llm/
├── base.py
├── catalog.py
├── factory.py
├── anthropic.py
├── openai.py
├── errors.py
├── compat.py
└── legacy.py
```

Vai trò:

- `catalog.py`: metadata của đúng hai product providers, model defaults, key env, model catalog URL.
- `factory.py`: resolve config và tạo provider concrete.
- `anthropic.py`: translate neutral request ↔ Anthropic Messages API.
- `openai.py`: translate neutral request ↔ OpenAI Chat Completions API.
- `errors.py`: normalize vendor failures.
- `compat.py`: migration adapter cho test/external caller Phase 3 còn truyền `.messages` client.
- `legacy.py`: deprecated import shim, không còn giữ OpenAI→Anthropic wire bridge.

## 3. Contract trung lập

Authoritative contract:

```text
pu/core/interfaces/llm.py
```

Các model chính:

```text
LLMProvider
LLMRequest
LLMResponse
LLMToolDefinition
LLMToolCall
LLMUsage
LLMProviderError
```

Một call thống nhất:

```text
provider.complete(
    LLMRequest(...),
    on_text=optional_stream_callback,
) -> LLMResponse
```

Agent không cần biết provider đang là Anthropic hay OpenAI.

## 4. Neutral message/tool representation

Working memory nội bộ không còn chứa Anthropic SDK block objects.

```text
assistant content:
  {type: text, text: ...}
  {type: tool_call, id, name, arguments, metadata?}

tool result:
  {role: tool, tool_call_id, content}
```

Anthropic adapter map sang `tool_use/tool_result`. OpenAI adapter map sang `assistant.tool_calls` và `role=tool`.

`metadata` là opaque. Nó chỉ phục vụ round-trip provider-specific metadata cần echo lại ở follow-up turn; agent không đọc/ra quyết định dựa trên nội dung đó.

## 5. Main loop

`pu/loop/agent.py` hiện chỉ biết:

```text
LLMProvider
LLMRequest
LLMResponse
ToolRegistry
```

Loop không còn:

```text
client.messages.create(...)
client.messages.stream(...)
response.content[type=tool_use]
```

Tool execution vẫn gọi `ToolRegistry.execute()` trực tiếp. Đây là intentional và sẽ được thay ở Phase 5 bằng Tool Harness + Security Harness.

## 6. Các LLM path đã migrate

- main agent loop;
- retrieval gate;
- memory consolidation;
- graph `llm_node`;
- graph `agent_node`;
- triage classifier;
- quick-reply path;
- application composition root;
- LLM-as-judge helper.

Source scan xác nhận direct vendor wire calls chỉ còn trong provider adapters và temporary compatibility adapter.

## 7. Anthropic adapter

`AnthropicLLMProvider` chịu trách nhiệm:

- neutral messages → Anthropic messages;
- `LLMToolDefinition` → Anthropic tool schema;
- Anthropic text/tool-use blocks → `LLMResponse`;
- Anthropic usage → `LLMUsage`;
- streaming `text_stream` → `on_text` callback;
- stop reason → normalized finish reason;
- exceptions → `LLMProviderError`.

SDK import được lazy-load trong adapter để module/domain layer vẫn import được khi sandbox chưa cài SDK.

## 8. OpenAI adapter

`OpenAILLMProvider` gọi OpenAI SDK native, không còn giả lập Anthropic-shaped client.

Adapter chịu trách nhiệm:

- neutral message → OpenAI chat messages;
- tool definitions → function tools;
- OpenAI tool calls → `LLMToolCall`;
- streamed tool-call fragments → reassembled arguments;
- usage/finish reason normalization;
- preserve opaque `extra_content` khi endpoint reasoning-compatible yêu cầu echo lại;
- fallback `max_completion_tokens → max_tokens` chỉ khi endpoint báo incompatibility của parameter.

## 9. Normalized error taxonomy

Application code nhận `LLMProviderError` với:

```text
provider_id
category
retryable
cause
```

Categories hiện tại:

```text
authentication
rate_limit
timeout
invalid_request
connection
server
unknown
```

Retry/policy layer sau này không cần import exception class của Anthropic/OpenAI.

## 10. Provider conformance suite

Cùng một suite kiểm tra cả hai provider về:

- text output;
- token usage;
- normalized finish reason;
- tool-call id/name/arguments;
- tool schema serialization;
- tool-result round trip;
- opaque metadata round trip;
- streaming text callback;
- streaming usage;
- normalized errors.

Kết quả:

```text
12 passed
```

Provider/factory + Phase-3 architecture compatibility checkpoint:

```text
26 passed
```

## 11. Regression gate

Full deterministic suite:

```text
299 passed
13 skipped
30 failed
```

30 failures là đúng nhóm baseline đã có trước Phase 4:

- source ZIP không có `.git` và `.env.example`, làm runtime-data/git-ignore tests không có đủ fixture;
- 4 assertions product-scope cũ từ Phase 2;
- 1 assertion triage event cũ.

Không có Phase-4-specific failure mới.

Green gate khi loại đúng baseline cases:

```text
299 passed
13 skipped
5 deselected
```

So với Phase 3 green gate `284 passed`, Phase 4 bổ sung thêm provider/migration tests nhưng không làm thay đổi business behavior.

## 12. Static gates

| Gate | Kết quả |
|---|---|
| `python -m compileall -q pu evals` | PASS |
| `node --check` toàn bộ JS | PASS |
| `python -m pu --help` | PASS |
| Vendor SDK import ngoài concrete adapters | 0 offender |
| Vendor wire call ngoài provider boundary | 0 offender |
| Provider branching trong app/loop/graph/memory | 0 offender |
| Architecture whiteboard regeneration | PASS |

## 13. Compatibility boundary

`compat.py` không phải product provider thứ ba. Nó chỉ là migration seam cho deterministic test fixtures và external callers cũ.

Product catalog vẫn chính xác:

```text
anthropic
openai
```

`pu/loop/models.py` và `pu/providers/llm/legacy.py` được giữ tạm như import compatibility shim; target Phase 10 có thể remove khi không còn caller cũ.

## 14. Assumption / limitation

Sandbox hiện tại không cài Anthropic/OpenAI SDK và không có package network, nên Phase 4 không thực hiện live API call tới hai vendor. Concrete adapters được test bằng injected fake SDK clients ở đúng boundary. `pyproject.toml` vẫn khai báo `anthropic` và `openai` là runtime dependencies, nên environment cài project đầy đủ sẽ có SDK thật.

Không sử dụng fake SDK trong production source hoặc snapshot.

## 15. Những gì cố ý chưa làm

Phase 4 chưa thay đổi tool execution policy:

```text
Agent → ToolRegistry.execute()
```

vẫn còn tạm thời.

Phase 5 sẽ chuyển thành:

```text
Agent
  ↓
Tool Registry
  ↓
Tool Harness
  ↓
Security Harness
  ↓
Integration Adapter
  ↓
External API
```

Human Approval/write capability boundary vẫn thuộc Phase 6.

## 16. Exit criteria

- [x] Anthropic và OpenAI implement cùng `LLMProvider` contract.
- [x] Provider được chọn từ config/composition root.
- [x] Agent/runtime/workflow/memory không branch theo provider.
- [x] Main loop không dùng vendor SDK blocks.
- [x] Tool calls, usage, finish reason, streaming và errors được normalize.
- [x] Cùng một conformance suite chạy cho cả hai adapters.
- [x] Không có regression mới so với baseline failure set.

**Phase 4 hoàn tất. Phase tiếp theo: Phase 5 — Tool Harness + Security Harness.**
