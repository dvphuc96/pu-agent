# Phase 8 — Report + Evaluation Harness

## 1. Mục tiêu

Phase 8 chuyển report của workflow research từ Markdown tự do sang **structured artifact có provenance**, sau đó đưa artifact qua **Evaluation Harness** trước khi coi report là sẵn sàng để sử dụng tiếp.

Phase này không thay đổi security/write boundary của Phase 5–6 và không thay đổi orchestration shape của Phase 7.

## 2. Structured Report

Các domain model mới nằm trong `pu/reporting/`:

- `ReportSource`
- `ReportClaim`
- `ReportContradiction`
- `StructuredReport`
- `EvidenceBundle`
- `StructuredReportBuilder`
- `FileReportRepository`

Evidence được normalize **trước khi** gọi Report Generation Agent. Source id (`S1`, `S2`, …) do code tạo từ actual agent/tool outputs; LLM không được phép tự tạo URL/source id.

Mỗi claim chứa:

- claim text;
- section;
- valid source ids;
- invalid/invented source ids;
- confidence;
- importance.

Report Agent trả JSON theo schema. Markdown được render deterministic sau parse/validation. Nếu model không trả schema hợp lệ, hệ thống tạo fallback artifact và quality gate sẽ fail thay vì âm thầm coi prose là grounded report.

## 3. Provenance policy

`EvidenceNormalizer` tạo first-order source từ Tool Harness executions, ví dụ:

- `read_email_inbox` → `email`;
- `search_knowledge` → `internal_knowledge`;
- `search_memory` → `memory`;
- `list_events` → `calendar`;
- `search_web` → `web` + URL/domain;
- agent synthesis → `agent_output` với `derived=true`.

Một factual claim chỉ được tính là supported nếu có ít nhất một **first-order, non-derived source**. Citation chỉ vào một agent synthesis không đủ để vượt deterministic grounding gate.

## 4. Evaluation Harness

Package mới `pu/evaluation/` gồm:

- `ReportQualityPolicy`
- `DeterministicReportEvaluator`
- `LLMReportJudge`
- `DefaultEvaluationHarness`
- `ReportEvaluation`
- `DeterministicMetrics`
- `JudgeScore`

Deterministic metrics hiện gồm:

- completeness;
- source coverage;
- source diversity;
- freshness;
- unsupported claims;
- invalid source references;
- contradiction count;
- source count;
- unique domains;
- internal/official evidence count;
- freshness-known source count;
- overall score.

Gate có ba trạng thái:

- `pass`;
- `pass_with_warning`;
- `fail`.

Unsupported claim hoặc invented source id là hard failure. Freshness không có publication date được ghi là **unknown**, không tự coi là fresh.

## 5. LLM-as-Judge

`LLMReportJudge` dùng provider-neutral `LLMProvider` contract. Judge chỉ chấm qualitative quality và không có tool/capability.

Invariant:

```text
LLM Judge score cao
        ≠
Override deterministic failure
        ≠
Authorize write/release side effect
```

`PU_REPORT_JUDGE=1` bật judge cho research workflow. Mặc định tắt để tránh thêm cost/latency vào local baseline. `PU_REPORT_JUDGE_MODEL` có thể override model judge.

## 6. Workflow mới

```text
Email Agent
   ↓
Company / Web / Calendar / Memory
   ↓
EvidenceNormalizer
   ↓
source ids + excerpts
   ↓
Report Generation Agent
   ↓ JSON only
StructuredReportBuilder
   ↓
Deterministic Evaluation Harness
   ├── optional LLM-as-Judge
   ↓
Quality Gate
   ↓
FileReportRepository
   ↓
.pu/reports/<report-id>.json
.pu/reports/<report-id>.md
```

Workflow events mới:

- `evidence_normalized`;
- `report_generated`;
- `evaluation_completed`.

## 7. UI

Dashboard Archive-style được bổ sung:

- **Reports** — xem report artifacts + metrics;
- **Evaluations** — xem report quality gate history;
- Research result hiển thị gate status, overall score, source coverage.

Browser vẫn chỉ gọi backend `/api/research`; mọi generation/evaluation/persistence chạy ở backend.

## 8. Golden dataset / Regression

Thêm `evals/datasets/report_quality.jsonl` với các category:

- grounding;
- freshness;
- prompt injection;
- contradiction handling.

Thêm `test_phase8_report_evaluation.py` cho deterministic behavior và `evals/judge/test_report_quality.py` cho live qualitative judge khi có API key.

Release gate giờ ghi riêng `report_quality` suite bên cạnh deterministic và judge suites để grounding regression dễ nhìn trong history.

## 9. Configuration

Các env mới:

```text
PU_REPORT_QUALITY_MIN_SCORE=0.72
PU_REPORT_MIN_COMPLETENESS=0.65
PU_REPORT_MIN_SOURCE_COVERAGE=0.80
PU_REPORT_FRESHNESS_DAYS=30
PU_REPORT_JUDGE=0
PU_REPORT_JUDGE_MODEL=
```

## 10. Validation

Phase-8 deterministic suite:

```text
10 passed
```

Phase 3–8 architecture/provider/security/approval/report targeted gate:

```text
61 passed
```

Green deterministic regression gate:

```text
340 passed
13 skipped
5 deselected
```

Full deterministic suite:

```text
340 passed
13 skipped
30 baseline failures
```

30 failures là cùng baseline đã biết từ Phase 7: 25 case phụ thuộc Git metadata/`.env.example` không có trong source ZIP và 5 stale assertions ngoài Phase 8. Không có failure mới do report/evaluation migration.

Live Phase-8 judge test trong sandbox:

```text
1 skipped
```

Lý do: không có provider API key. Không có fake live score được đưa vào snapshot.

Static gates:

- Python `compileall`: PASS;
- JavaScript `node --check`: PASS;
- `python -m pu --help`: PASS;
- architecture whiteboard regenerate + visual QA: PASS.

## 11. Assumptions / giới hạn còn lại

1. Search tool hiện chủ yếu có snippet + URL; publication date thường không có. Vì vậy freshness có thể ở trạng thái unknown. Không suy diễn date từ snippet.
2. Full-page fetch/extraction chưa được thêm. Benchmark quality ở Phase 8 tạo dữ liệu để quyết định có cần capability này không.
3. `FileReportRepository` là local/dev adapter. Phase 9 sẽ harden repository/migrations và có thể thêm PostgreSQL adapter.
4. Quality gate cho phép `pass_with_warning` đối với internal draft. Bất kỳ external delivery/write nào vẫn phải đi qua Human Approval + Security Harness.

## 12. Exit criteria

- ✅ structured report schema tồn tại;
- ✅ claim/source linkage được enforce;
- ✅ invented source id bị phát hiện;
- ✅ first-order provenance được phân biệt với derived synthesis;
- ✅ completeness/source coverage/diversity/freshness metrics tồn tại;
- ✅ deterministic quality gate chạy trong workflow;
- ✅ LLM-as-Judge tách riêng và không override deterministic failure;
- ✅ report/evaluation artifacts được persist;
- ✅ golden report dataset tồn tại;
- ✅ Dashboard có Reports/Evaluations views;
- ✅ release gate có explicit report-quality record;
- ✅ không có regression mới so với Phase 7.
