"""Offline orchestration tests for report quality repair and draft safety."""

from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace

from pu.core.interfaces.agents import AgentResult
from pu.core.interfaces.orchestration import ResearchWorkflowRequest
from pu.evaluation.models import DeterministicMetrics
from pu.orchestration.research import ResearchWorkflowOrchestrator
from pu.reporting.models import ReportClaim, ReportSource, StructuredReport


def _metrics() -> DeterministicMetrics:
    return DeterministicMetrics(
        completeness=1.0,
        source_coverage=1.0,
        source_diversity=1.0,
        freshness=1.0,
        unsupported_claims=0,
        invalid_source_refs=0,
        contradictions=0,
        source_count=1,
        unique_domains=1,
        official_or_internal_sources=1,
        freshness_known_sources=1,
        total_claims=1,
        overall=1.0,
    )


class FakeEvaluation:
    def __init__(self, report: StructuredReport, *, allowed: bool, evaluated: bool = True):
        self.report_id = report.report_id
        self.release_allowed = allowed
        self.evaluated = evaluated
        self.gate_status = "pass" if allowed else "fail"
        self.metrics = _metrics()
        self.findings = [] if allowed else ["fixture quality failure"]
        self.warnings = []
        self.judge = SimpleNamespace(reason="fixture judge found a quality issue")
        self.quality_policy_version = "report-quality-v2"
        self.status_details = {
            "semantic_review": "passed" if allowed else "failed"
        } if evaluated else {"semantic_review": "unavailable_or_incomplete"}

    def to_dict(self) -> dict:
        return {
            "report_id": self.report_id,
            "release_allowed": self.release_allowed,
            "evaluated": self.evaluated,
            "gate_status": self.gate_status,
            "quality_policy_version": self.quality_policy_version,
        }


class StubAgent:
    def __init__(self, agent_id: str, replies: list[str] | None = None):
        self.descriptor = SimpleNamespace(agent_id=agent_id)
        self.replies = list(replies or [f"{agent_id} evidence"])
        self.calls: list[dict] = []

    def respond(self, prompt, **kwargs):
        self.calls.append(kwargs)
        reply = self.replies.pop(0) if self.replies else self.descriptor.agent_id
        return AgentResult(
            reply=reply,
            tool_calls=[
                {
                    "tool": "fixture_source",
                    "output": "Acme has a sourced partner request.",
                    "ok": True,
                    "metadata": {},
                }
            ],
            iterations=1,
            usage={"in": 1, "out": 1, "calls": 1},
        )


class FixtureReportBuilder:
    def build_evidence(self, stages):
        # A fixed first-order source keeps the test about orchestration gates,
        # rather than about the production evidence parser.
        from pu.reporting.evidence import EvidenceBundle

        return EvidenceBundle(
            (
                ReportSource(
                    id="S1",
                    stage="email",
                    source_type="email",
                    title="Fixture email",
                    excerpt="Acme has a sourced partner request.",
                ),
            )
        )

    def generation_prompt(self, evidence):
        return "fixture report prompt"

    def assemble(self, raw_reply, evidence, *, workflow_id):
        good = str(raw_reply).strip() == "good"
        sections = {"company": "Acme [S1]"} if good or raw_reply in {"low", "bad"} else {}
        claims = [ReportClaim(id="C1", text="Acme sent a partner request.", section="company", source_ids=("S1",))] if sections else []
        report = StructuredReport(
            report_id=f"report-{workflow_id}-{raw_reply}",
            workflow_id=workflow_id,
            title="Acme fixture",
            executive_summary="Acme sent a partner request [S1].",
            sections=sections,
            claims=claims,
            sources=list(evidence.sources),
            schema_version=2,
            metadata={
                "quality_policy_version": "report-quality-v2",
                "fixture_quality": "good" if good else "bad",
            },
        )
        report.markdown = f"# Acme fixture\n\n{raw_reply} [S1]"
        return report

    def render_markdown(self, report):
        return report.markdown


class FixtureReportRepository:
    def __init__(self):
        self.saved: list[tuple[StructuredReport, FakeEvaluation]] = []

    def save(self, report, evaluation):
        self.saved.append((deepcopy(report), deepcopy(evaluation)))


class FixtureEvaluationHarness:
    def __init__(self, *, unavailable: bool = False):
        self.unavailable = unavailable
        self.calls: list[StructuredReport] = []

    def evaluate(self, report, *, run_judge=False):
        self.calls.append(deepcopy(report))
        allowed = report.metadata.get("fixture_quality") == "good" and not self.unavailable
        return FakeEvaluation(report, allowed=allowed, evaluated=not self.unavailable)


def make_orchestrator(report_replies, *, unavailable=False):
    report = StubAgent("report-agent", report_replies)
    agents = [
        StubAgent("email-agent"),
        StubAgent("company-agent"),
        StubAgent("web-agent"),
        StubAgent("calendar-agent"),
        StubAgent("memory-agent"),
        report,
    ]
    repository = FixtureReportRepository()
    evaluation = FixtureEvaluationHarness(unavailable=unavailable)
    orchestrator = ResearchWorkflowOrchestrator(
        email=agents[0],
        company=agents[1],
        web=agents[2],
        calendar=agents[3],
        memory=agents[4],
        report=agents[5],
        report_builder=FixtureReportBuilder(),
        evaluation_harness=evaluation,
        report_repository=repository,
    )
    return orchestrator, report, evaluation, repository


def request(**kwargs):
    return ResearchWorkflowRequest(
        prompt="Prepare the fixture partner brief.",
        company="Acme",
        require_semantic_review=True,
        **kwargs,
    )


def test_low_quality_first_pass_gets_one_quality_repair_and_improves():
    orchestrator, report_agent, evaluator, repository = make_orchestrator(["low", "good"])

    result = orchestrator.run(request())

    assert len(report_agent.calls) == 2
    assert [item.metadata["fixture_quality"] for item in evaluator.calls] == ["bad", "good"]
    assert result.structured_report.metadata["quality_repair_attempts"] == 1
    assert result.evaluation.release_allowed is True
    assert repository.saved[-1][0].to_dict() == result.structured_report.to_dict()


def test_persistent_bad_report_has_one_repair_then_remains_draft():
    orchestrator, report_agent, _evaluator, repository = make_orchestrator(["bad", "bad"])

    result = orchestrator.run(request())

    assert len(report_agent.calls) == 2
    assert result.structured_report.metadata["quality_repair_attempts"] == 1
    assert result.evaluation.release_allowed is False
    assert repository.saved[-1][1].release_allowed is False


def test_structural_repair_consumes_shared_budget_and_skips_quality_repair():
    # "empty" is structurally invalid; the next response is valid but still
    # fails evaluation.  It must not trigger a third generation.
    orchestrator, report_agent, _evaluator, _repository = make_orchestrator(["empty", "low", "good"])

    result = orchestrator.run(request())

    assert len(report_agent.calls) == 2
    assert result.structured_report.metadata["quality_repair_attempts"] == 1
    assert result.evaluation.release_allowed is False


def test_evaluator_unavailable_stays_draft_and_records_evaluation_timing():
    orchestrator, report_agent, _evaluator, _repository = make_orchestrator(["good"], unavailable=True)

    result = orchestrator.run(request())

    assert result.evaluation.evaluated is False
    assert result.evaluation.release_allowed is False
    assert len(report_agent.calls) == 1
    performance = result.structured_report.metadata["performance"]
    assert performance["scope"] == "research_workflow"
    assert performance["phases"]["evaluation"] >= 0.0


def test_conflicting_identity_prevents_pointless_quality_retry():
    orchestrator, report_agent, _evaluator, _repository = make_orchestrator(["low", "good"])

    result = orchestrator.run(
        request(partner_identity={"status": "conflicting", "name": "Acme"})
    )

    assert len(report_agent.calls) == 1
    assert result.structured_report.metadata["quality_repair_attempts"] == 0
    assert result.evaluation.release_allowed is False


def test_quality_repair_provider_error_preserves_readable_draft():
    orchestrator, report_agent, _evaluator, repository = make_orchestrator(["low"])
    original = report_agent.respond

    def respond(prompt, **kwargs):
        if report_agent.calls:
            raise RuntimeError("offline provider interruption during repair")
        return original(prompt, **kwargs)

    report_agent.respond = respond
    result = orchestrator.run(request())
    assert result.evaluation.release_allowed is False
    assert result.structured_report.sections
    assert result.structured_report.metadata["quality_repair_attempts"] == 1
    assert "quality_repair" in result.structured_report.metadata["performance"]["stages"]
    assert repository.saved[-1][0].report_id == result.structured_report.report_id

