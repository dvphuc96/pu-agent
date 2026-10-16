from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from pu.core.interfaces.agents import AgentResult


@dataclass(frozen=True)
class ResearchWorkflowRequest:
    prompt: str = "Prepare my morning company research brief from recent email and calendar context."
    email_days: int = 7
    max_emails: int = 20
    principal: str = "local-user"
    workspace_id: str | None = None
    company: str = ""
    evidence_sources: list[dict[str, Any]] = field(default_factory=list)
    require_semantic_review: bool = False
    inbox_fingerprint: str = ""
    partner_identity: dict[str, Any] = field(default_factory=dict)
    evidence_cache: dict[str, Any] = field(default_factory=dict)

@dataclass
class ResearchWorkflowResult:
    workflow_id: str
    stages: dict[str, AgentResult] = field(default_factory=dict)
    report: AgentResult | None = None
    structured_report: Any | None = None
    evaluation: Any | None = None
    status: str = "completed"

class AgentOrchestrator(Protocol):
    def run(self, request: ResearchWorkflowRequest, observer=None) -> ResearchWorkflowResult: ...
    def resume(self, workflow_id: str, observer=None) -> ResearchWorkflowResult: ...
