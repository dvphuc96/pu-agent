# Contributing to PU Agent

PU Agent đang trong giai đoạn migration có kiểm soát từ baseline `Archive.zip`. Mọi thay đổi phải bám `ARCHITECTURE.md` và phase plan hiện hành.

## Nguyên tắc

1. Không big-bang rewrite. Mỗi phase phải giữ một characterization baseline có thể kiểm chứng.
2. Product LLM providers chỉ gồm Anthropic và OpenAI, đi qua provider abstraction ở phase refactor tương ứng.
3. Product integrations hiện gồm Gmail, Google Calendar, Google Drive, Tavily/Web Search và OpenTelemetry.
4. Không tái-introduce gateway/provider/tool đã prune ở Phase 2 nếu chưa có ADR/requirement mới.
5. Không cho agent gọi external write API trực tiếp. Target write path phải đi qua Human Approval + Tool/Security Harness.
6. Secrets không được commit hoặc ghi vào fixtures/logs.
7. Tests deterministic ưu tiên trước; LLM-as-Judge chỉ dùng cho tiêu chí chất lượng không thể kiểm tra deterministic.

## Trước khi gửi thay đổi

```bash
python -m compileall -q pu evals scripts examples
find pu/ops/static -name '*.js' -print0 | xargs -0 -n1 node --check
pytest -q evals/deterministic
```

Nếu môi trường thiếu Anthropic/OpenAI SDK hoặc integration extras, ghi rõ test nào bị `BLOCKED_BY_DEPENDENCY`; không thêm fake package vào production source chỉ để làm xanh suite.

## Skills

Bundled project skills nằm trong `skills/` và được force-include vào wheel. Personal/local skills phải nằm ngoài repository (ví dụ dưới runtime home `.pu/skills/`).
