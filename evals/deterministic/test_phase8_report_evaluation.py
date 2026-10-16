"""Phase 8 gates: structured report provenance + deterministic Evaluation Harness."""
from __future__ import annotations

import json
from pathlib import Path

from evals.helpers import ScriptedProvider
from pu.config import Settings
from pu.core.container import create_application
from pu.core.interfaces.agents import AgentResult
from pu.core.interfaces.llm import LLMResponse, LLMUsage
from pu.evaluation import (
    DefaultEvaluationHarness,
    DeterministicReportEvaluator,
    LLMReportJudge,
)
from pu.reporting import EvidenceNormalizer, FileReportRepository, StructuredReportBuilder
from pu.reporting.models import ReportClaim, ReportSource, StructuredReport


def stage_result(reply: str, tool: str | None = None, output: str = "") -> AgentResult:
    calls = [] if not tool else [{"tool": tool, "args": {}, "output": output, "ok": True, "metadata": {}}]
    return AgentResult(reply=reply, tool_calls=calls, iterations=1, usage={"in": 1, "out": 1, "calls": 1})


def strong_report() -> StructuredReport:
    sources = [
        ReportSource(id="S1", stage="email", source_type="email", title="Inbox", excerpt="Alice at Acme requested Q4 planning.", tool_name="read_email_inbox"),
        ReportSource(id="S2", stage="company", source_type="internal_knowledge", title="CRM note", excerpt="Acme is an existing customer.", tool_name="search_knowledge"),
        ReportSource(id="S3", stage="web", source_type="web", title="Acme site", excerpt="Acme launched Product X.", tool_name="search_web", url="https://acme.example/news", domain="acme.example", external=True, published_at="2026-09-25T00:00:00+00:00"),
    ]
    claims = [
        ReportClaim(id="C1", text="Acme is an existing customer.", section="company", source_ids=("S2",)),
        ReportClaim(id="C2", text="Alice requested Q4 planning.", section="email_need", source_ids=("S1",)),
        ReportClaim(id="C3", text="Acme launched Product X.", section="recent_updates", source_ids=("S3",)),
    ]
    sections = {
        "company": "Acme", "contacts": "Alice", "email_need": "Q4 planning",
        "meeting_context": "Meeting prep", "public_research": "Product X",
        "risks_opportunities": "Validate scope",
    }
    return StructuredReport(
        report_id="report-test", workflow_id="wf-test", title="Acme prep",
        executive_summary="Prepare for Acme.", sections=sections, claims=claims, sources=sources,
    )


def test_evidence_normalizer_uses_tool_execution_as_first_order_provenance():
    stages = {
        "email": stage_result("email synthesis", "read_email_inbox", "Alice <alice@acme.example> | Q4 prep"),
        "web": stage_result("web synthesis", "search_web", "1. Acme news\n Launch\n https://acme.example/news"),
    }
    bundle = EvidenceNormalizer().normalize(stages)
    primary = [s for s in bundle.sources if not s.derived]
    derived = [s for s in bundle.sources if s.derived]
    assert {s.source_type for s in primary} == {"email", "web"}
    assert any(s.url == "https://acme.example/news" and s.domain == "acme.example" for s in primary)
    assert len(derived) == 2


def test_report_builder_rejects_invented_source_ids_and_renders_citations():
    stages = {"company": stage_result("company synthesis", "search_knowledge", "Acme is an existing customer")}
    builder = StructuredReportBuilder()
    evidence = builder.build_evidence(stages)
    raw = json.dumps({
        "title": "Acme", "executive_summary": "Summary",
        "sections": {"company": "Acme"},
        "claims": [{"text": "Acme is a customer", "section": "company", "source_ids": ["S1", "S999"]}],
        "missing_information": [], "contradictions": [],
    })
    report = builder.assemble(raw, evidence, workflow_id="wf")
    assert report.claims[0].source_ids == ("S1",)
    assert report.claims[0].invalid_source_ids == ("S999",)
    assert "invalid=S999" in report.markdown
    assert "[Kiến thức nội bộ]" in report.markdown
    assert report.metadata["citation_presentation"]["S1"]["label"] == "Kiến thức nội bộ"


def test_deterministic_evaluator_passes_supported_diverse_complete_report():
    result = DeterministicReportEvaluator().evaluate(strong_report())
    assert result.release_allowed is True
    assert result.gate_status in {"pass", "pass_with_warning"}
    assert result.metrics.source_coverage == 1.0
    assert result.metrics.unsupported_claims == 0
    assert result.metrics.unique_domains == 1
    assert result.metrics.overall >= 0.72


def test_deterministic_evaluator_fails_unsupported_or_derived_only_claims():
    report = strong_report()
    report.sources.append(ReportSource(id="S4", stage="web", source_type="agent_output", title="synthesis", excerpt="unsupported", derived=True))
    report.claims.append(ReportClaim(id="C4", text="Unsupported claim", section="company", source_ids=("S4",)))
    result = DeterministicReportEvaluator().evaluate(report)
    assert result.release_allowed is False
    assert result.gate_status == "fail"
    assert result.metrics.unsupported_claims == 1


def test_missing_publication_dates_are_warning_not_fake_freshness():
    report = strong_report()
    report.sources = [ReportSource(**{**s.__dict__, "published_at": None}) for s in report.sources]
    result = DeterministicReportEvaluator().evaluate(report)
    assert result.metrics.freshness == 0.5
    assert result.metrics.freshness_known_sources == 0
    assert any("freshness is unknown" in w for w in result.warnings)


class JudgeProvider:
    provider_id = "fake"
    def __init__(self): self.requests = []
    def complete(self, request, on_text=None):
        self.requests.append(request)
        data = json.loads(request.messages[0]["content"].rsplit("\n", 1)[-1])
        return LLMResponse(
            text=json.dumps({"score": 8.5, "reason": "Grounded and clear", "criteria": {"factual_support": 9, "completeness": 8, "relevance": 9, "meeting_readiness": 9, "nondup": 9},
                             "claim_verdicts": [{"id": key, "supported": True, "reason": "English fixture supports its Vietnamese paraphrase."} for key in data.get("semantic_pending_claims", [])],
                             "field_verdicts": [{"id": key, "supported": True, "reason": "Supported by the fixture evidence."} for key in data.get("semantic_pending_fields", [])]}),
            usage=LLMUsage(10, 5), finish_reason="end_turn",
        )


def test_llm_judge_is_provider_neutral_and_cannot_override_deterministic_failure():
    provider = JudgeProvider()
    harness = DefaultEvaluationHarness(
        deterministic=DeterministicReportEvaluator(),
        judge=LLMReportJudge(provider, "judge-model"),
    )
    report = strong_report()
    report.claims.append(ReportClaim(id="C4", text="No evidence", section="company"))
    result = harness.evaluate(report, run_judge=True)
    assert result.judge.score == 8.5
    assert result.gate_status == "fail"
    assert result.release_allowed is False
    assert provider.requests[0].model == "judge-model"


def test_file_report_repository_persists_json_markdown_and_evaluation(tmp_path):
    report = strong_report()
    report.markdown = "# Acme prep\n"
    evaluation = DeterministicReportEvaluator().evaluate(report)
    repo = FileReportRepository(tmp_path)
    repo.save(report, evaluation)
    assert (tmp_path / "reports" / "report-test.json").exists()
    assert (tmp_path / "reports" / "report-test.md").read_text() == report.markdown
    saved = repo.list()
    assert saved[0]["report_id"] == "report-test"
    assert saved[0]["evaluation"]["gate_status"] in {"pass", "pass_with_warning"}


class Phase8Runtime:
    def __init__(self): self.requests = []
    def run(self, request, observer=None):
        self.requests.append(request)
        aid = request.tool_context.agent_id
        if aid == "email-agent":
            return stage_result("Acme email", "read_email_inbox", "Alice <alice@acme.example> | Q4 planning")
        if aid == "company-info-agent":
            return stage_result("Acme customer", "search_knowledge", "Acme is an existing customer in the software sector, serving enterprise markets with 120 employees.")
        if aid == "web-research-agent":
            return stage_result("Acme public", "search_web", "Acme software company website: Acme launched Product X for enterprise customers on a digital platform. https://acme.example/news")
        if aid == "calendar-agent":
            return stage_result("Meeting tomorrow", "list_events", "Acme review | 2026-10-05 09:00")
        if aid == "memory-agent":
            return stage_result("Prior renewal", "search_memory", "Acme renewal discussed last quarter")
        # With the deterministic source ordering above, first-order evidence ids are S1,S3,S5,S7,S9.
        payload = {
            "schema_version": 2,
            "title": "Chuẩn bị trao đổi với Acme", "executive_summary": "Acme đã đề nghị trao đổi về kế hoạch Q4; cần xác nhận phạm vi và bước tiếp theo [S1].",
            "sections": {
                "company": "Acme là khách hàng hiện hữu [S3].", "contacts": "Alice là người gửi email; vai trò và quyền đại diện cần được xác nhận [S1].", "email_need": "Theo email, Alice đề nghị trao đổi về kế hoạch Q4 [S1].",
                "meeting_context": "Cuộc trao đổi cần làm rõ phạm vi kế hoạch Q4 và bước tiếp theo.", "public_research": "Acme đã công bố một sản phẩm mới [S5].",
                "recent_updates": "Trang công khai ghi nhận một lần ra mắt sản phẩm [S5].", "risks_opportunities": "Cần xác nhận phạm vi gia hạn và mục tiêu buổi trao đổi [S1][S9].",
                "company_scale": "Chưa tìm thấy dữ kiện đủ tin cậy về quy mô trong bộ nguồn.",
                "business_context": "Nguồn công khai cho thấy Acme có hoạt động ra mắt sản phẩm [S5].",
                "meeting_preparation": "Đề xuất hỏi về mục tiêu Q4, phạm vi, tiêu chí thành công, timeline và người ra quyết định; đây là các câu hỏi chuẩn bị, không phải cam kết của Acme.",
            },
            "claims": [
                {"text": "Theo email, Alice đề nghị trao đổi về kế hoạch Q4.", "section": "email_need", "source_ids": ["S1"]},
                {"text": "Acme là khách hàng hiện hữu.", "section": "company", "source_ids": ["S3"]},
                {"text": "Acme có thông tin công khai về một lần ra mắt sản phẩm.", "section": "recent_updates", "source_ids": ["S5"]},
            ],
            "information_coverage": [
                {"field": "website", "status": "supported", "source_ids": ["S5"], "summary": "Website Acme có trang công khai."},
                {"field": "sector", "status": "supported", "source_ids": ["S3"], "summary": "Acme hoạt động trong software sector."},
                {"field": "products", "status": "supported", "source_ids": ["S5"], "summary": "Acme có Product X."},
                {"field": "scale", "status": "supported", "source_ids": ["S3"], "summary": "Acme có 120 employees."},
                {"field": "markets", "status": "supported", "source_ids": ["S3"], "summary": "Acme phục vụ enterprise markets."},
                {"field": "digital_context", "status": "supported", "source_ids": ["S5"], "summary": "Acme có digital platform."},
                {"field": "contact", "status": "supported", "source_ids": ["S1"], "summary": "Alice là người gửi email."},
                {"field": "request", "status": "supported", "source_ids": ["S1"], "summary": "Alice nêu Q4 planning."},
                {"field": "meeting_preparation", "status": "supported", "source_ids": ["S1", "S9"], "summary": "Alice và Acme đã trao đổi về Q4 planning."},
                {"field": "calendar", "status": "supported", "source_ids": ["S7"], "summary": "Lịch Acme review 2026-10-05 đã được kiểm tra."},
            ],
            "missing_information": ["Mục tiêu chính xác của cuộc họp cần xác nhận với Alice."], "contradictions": [],
        }
        return AgentResult(reply=json.dumps(payload), iterations=1, usage={"in": 1, "out": 1, "calls": 1})


def test_generation_prompt_and_report_agent_require_vietnamese_output():
    """The chat relays the report verbatim — it must be born in Vietnamese."""
    builder = StructuredReportBuilder()
    evidence = builder.build_evidence({"email": stage_result("s", "read_email_inbox", "x")})
    prompt = builder.generation_prompt(evidence)
    assert "Vietnamese" in prompt
    from pu.agents.report import ReportGenerationAgent
    assert "Vietnamese" in ReportGenerationAgent.instructions


def test_research_workflow_produces_structured_report_quality_gate_and_artifact(tmp_path):
    runtime = Phase8Runtime()
    app = create_application(
        settings=Settings(home=tmp_path, api_key="offline", gmail=False, graph_workflows=False),
        llm_provider=JudgeProvider(), agent_runtime=runtime,
    )
    result = app.run_research_workflow("Prepare Acme")
    assert result.structured_report is not None
    assert result.evaluation is not None
    assert result.structured_report.claims[0].source_ids == ("S1",)
    assert result.evaluation.release_allowed is True
    assert result.evaluation.metrics.source_coverage == 1.0
    assert "Đối chiếu dữ kiện" in result.report.reply
    assert (tmp_path / "reports" / f"{result.structured_report.report_id}.json").exists()


def test_phase8_contract_is_wired_through_composition_root(tmp_path):
    app = create_application(
        settings=Settings(home=tmp_path, api_key="offline", graph_workflows=False),
        llm_provider=ScriptedProvider([]),
    )
    assert app.report_builder is not None
    assert app.evaluation_harness is not None
    assert app.report_repository is not None


def test_golden_report_dataset_exists_and_has_security_case():
    root = Path(__file__).parents[2]
    path = root / "evals/datasets/report_quality.jsonl"
    rows = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]
    assert len(rows) >= 3
    assert any(row.get("category") == "prompt_injection" for row in rows)
