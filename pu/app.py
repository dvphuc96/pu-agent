"""PU application facade.

Application facade with dependencies assembled by ``pu.core.container``.
All LLM behavior is consumed through the provider-neutral
``LLMProvider`` contract; vendor SDK/wire formats stay outside this module.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from pu.adapters.events import CallableEventPublisher, CompositeEventPublisher
from pu.agents import BaseAgent
from pu.core.interfaces.agents import AgentResult, AgentRunRequest
from pu.core.interfaces.llm import LLMRequest
from pu.core.interfaces.tools import ToolContext
from pu.loop.agent import LoopResult, Observer
from pu.tools.research import make_tool as make_research_tool

if TYPE_CHECKING:
    from pu.core.container import ApplicationDependencies


class PU(BaseAgent):
    def __init__(self, *, dependencies: ApplicationDependencies):
        """Application facade; object graph construction belongs to the composition root."""
        self.settings = dependencies.settings
        self.conn = dependencies.conn
        self.database = dependencies.database
        self.llm_provider = dependencies.llm_provider
        self.memory = dependencies.memory
        self.tools = dependencies.tools
        self.session = dependencies.session
        self.tracer = dependencies.tracer
        self.agent_runtime = dependencies.agent_runtime
        self.workflow_engine = dependencies.workflow_engine
        self.event_publisher = dependencies.event_publisher
        self.event_repository = dependencies.event_repository
        self.workflow_repository = dependencies.workflow_repository
        self.slo_evaluator = dependencies.slo_evaluator
        self.tool_harness = dependencies.tool_harness
        self.security_harness = dependencies.security_harness
        self.approval_service = dependencies.approval_service
        self.approved_action_executor = dependencies.approved_action_executor
        self.specialized_agents = dependencies.specialized_agents
        self.agent_orchestrator = dependencies.agent_orchestrator
        self.report_builder = dependencies.report_builder
        self.evaluation_harness = dependencies.evaluation_harness
        self.report_repository = dependencies.report_repository
        # Chat-triggered research: the registry is built before the
        # orchestrator exists (the orchestrator consumes tool subsets), so
        # the research tool is attached here with a lazy closure over it.
        # run_research_workflow already fans events out to BOTH the chat
        # observer (live streaming) and the durable AgentOps publisher.
        self.tools.register(
            make_research_tool(
                lambda prompt, observer=None: self.run_research_workflow(
                    prompt, observer=observer
                ),
                self.settings.home,
            )
        )

    def close(self) -> None:
        """Flush observability and release the shared local memory connection."""
        try:
            self.tracer.close()
        except Exception:
            pass
        try:
            self.conn.close()
        except Exception:
            pass

    def respond(
        self,
        user_message: str,
        observer: Observer | None = None,
        source: str = "cli",
        stream: bool = False,
    ) -> AgentResult:
        """One full turn: assemble working memory → run → persist/observe."""
        import time

        captured: dict = {}

        def _capture(kind, ev):
            if kind == "llm":
                usage = ev.get("usage") or {}
                total = captured.setdefault("usage", {"in": 0, "out": 0, "calls": 0})
                total["in"] += int(usage.get("in", 0) or 0)
                total["out"] += int(usage.get("out", 0) or 0)
                total["calls"] += 1
            if kind == "gate":
                captured["gate"] = {"decision": ev.get("decision"), "reason": ev.get("reason")}
            if kind == "route":
                captured["graph_route"] = {"target": ev.get("target"), "reason": ev.get("reason")}
            if kind == "triage":
                captured["triage_reason"] = ev.get("reason")
            if kind == "graph_end":
                captured["graph_path"] = ev.get("path")

        publisher = CompositeEventPublisher([
            CallableEventPublisher(observer),
            self.event_publisher,
            CallableEventPublisher(_capture),
        ])
        notify = publisher.publish
        t0 = time.perf_counter()

        with self.tracer.turn(user_message):
            result = None
            from pu.orchestration.inbox_chat import InboxChatBriefing, is_inbox_request
            if is_inbox_request(user_message):
                result = InboxChatBriefing(self).run(user_message, notify=notify)
            if result is None and self.settings.graph_workflows:
                try:
                    result = self._respond_via_graph(user_message, notify, stream)
                except Exception as exc:
                    notify("graph_end", {
                        "workflow": "triage", "ms": 0, "steps": 0, "path": [], "error": repr(exc),
                    })
                    result = None
            if result is None:
                result = self._run_full_turn(user_message, notify, stream)

            quick = captured.get("graph_route", {}).get("target") == "quick_reply"
            if is_inbox_request(user_message) and captured.get("usage"):
                result.usage = captured["usage"]

            def _status(out: str) -> str:
                low = (out or "").lower()
                return "error" if (
                    "failed" in low or "timed out" in low or low.startswith("error")
                ) else "ok"

            meta = {
                "gate": captured.get("gate"),
                "graph": ({
                    "workflow": "triage",
                    "route": "quick" if quick else "full",
                    "reason": captured.get("triage_reason", ""),
                    "path": captured.get("graph_path"),
                } if "graph_route" in captured else None),
                "iterations": result.iterations,
                "usage": result.usage,
                "latency_ms": int((time.perf_counter() - t0) * 1000),
                "tools": [
                    {
                        "tool": c["tool"],
                        "status": _status(c["output"]),
                        "approval_id": c.get("approval_id"),
                        "approval_status": c.get("approval_status"),
                        "action_tool": c.get("action_tool"),
                    }
                    for c in result.tool_calls
                ],
                "model": self.settings.small_model if quick else self.settings.model,
                "provider": self.settings.provider,
                "reports": result.reports,
                "inbox_validation": result.inbox_validation,
            }
            self.session.add_exchange(
                user_message,
                result.reply,
                tool_calls=result.tool_calls,
                source=source,
                meta=meta,
            )
            if self.memory is not None:
                self.memory.maybe_consolidate(notify=notify)
                self.memory.export_markdown()

        self.tracer.end_turn(result.reply, result.iterations)
        return result

    def run_research_workflow(self, prompt: str | None = None, *, observer=None, email_days: int = 7, max_emails: int = 20,
                              workspace_id: str | None = None, company: str = "", evidence_sources=None,
                              require_semantic_review: bool = False, inbox_fingerprint: str = "", partner_identity=None,
                              evidence_cache=None):
        """Run the deterministic multi-agent read-only research workflow.

        Workflow/agent/tool/LLM events fan out to the durable AgentOps publisher
        and to the caller observer, exactly like a normal chat turn.
        """
        from pu.core.interfaces.orchestration import ResearchWorkflowRequest
        publisher = CompositeEventPublisher([
            CallableEventPublisher(observer),
            self.event_publisher,
        ])
        return self.agent_orchestrator.run(
            ResearchWorkflowRequest(
                prompt=prompt or "Prepare my morning company research brief from recent email and calendar context.",
                email_days=email_days,
                max_emails=max_emails,
                workspace_id=workspace_id,
                company=company,
                evidence_sources=evidence_sources or [],
                require_semantic_review=require_semantic_review,
                inbox_fingerprint=inbox_fingerprint,
                partner_identity=partner_identity or {},
                evidence_cache=evidence_cache or {},
            ),
            observer=publisher.publish,
        )

    def resume_research_workflow(self, workflow_id: str, *, observer=None):
        """Resume a failed/interrupted research workflow from durable checkpoints."""
        publisher = CompositeEventPublisher([
            CallableEventPublisher(observer),
            self.event_publisher,
        ])
        return self.agent_orchestrator.resume(workflow_id, observer=publisher.publish)

    def _run_full_turn(self, user_message: str, notify, stream: bool) -> AgentResult:
        system = self.session.build_system(user_message, notify=notify)
        if self.tools.resolve("research_partner") is not None:
            # Tool descriptions alone don't make the model chain proactively:
            # read_gmail_today's own template ends the turn with "suggested
            # next actions", and conversation history reinforces that pattern.
            # The system prompt is the layer that overrides both.
            system += (
                "\nPartner research: after reading email, when you spot a "
                "message from a new partner or customer (a collaboration, "
                "sales, or meeting request from a company), call the "
                "research_partner tool in the same turn instead of only "
                "suggesting the user review it. Pass the website from the "
                "email signature and any meeting timing in the context. "
                "Tell the user the research has started; when it returns, "
                "present the FULL report inline in Vietnamese as your chat "
                "reply — sections, verdict, gaps, free time slots, sources — "
                "do not shorten it to a summary and do not send the user to "
                "the file (mention the archive copy in one line only). Then "
                "offer the approval choices (forward by email / save to "
                "knowledge)."
            )
        window = self.settings.history_turns * 2
        messages = self.session.history[-window:] + [{"role": "user", "content": user_message}]
        context = ToolContext(
            observer=notify,
            actor="agent",
            principal="local-user",
            request_id=str(uuid.uuid4()),
            workflow_id=self.session.session_id,
            agent_id="pu-agent",
            scopes=self.tools.scopes(),
            capabilities=self.tools.agent_capabilities(),
            metadata={"source": "agent_loop"},
        )
        return self.agent_runtime.run(
            AgentRunRequest(
                model=self.settings.model,
                system=system,
                messages=messages,
                tools=self.tools,
                max_iterations=self.settings.max_iterations,
                max_tokens=self.settings.max_tokens,
                stream=stream,
                tool_context=context,
            ),
            observer=notify,
        )

    def _respond_via_graph(self, user_message: str, notify, stream: bool) -> AgentResult | None:
        from pu.graph.workflows.triage import (
            QUICK_REPLY_PROMPT,
            build_triage_graph,
            classify_message,
            todays_events,
        )

        def quick_reply(state: dict) -> str:
            prompt = QUICK_REPLY_PROMPT.format(
                calendar=state.get("calendar", ""), message=state["message"]
            )
            response = self.llm_provider.complete(LLMRequest(
                model=self.settings.small_model,
                max_tokens=600,
                messages=[{"role": "user", "content": prompt}],
            ))
            notify("llm", {
                "iteration": 1,
                "stop_reason": response.finish_reason,
                "usage": {
                    "in": response.usage.input_tokens,
                    "out": response.usage.output_tokens,
                },
            })
            return response.text

        graph = build_triage_graph(
            classify_fn=lambda m: classify_message(self.llm_provider, self.settings.small_model, m),
            calendar_fn=lambda: todays_events(self.settings.home),
            quick_fn=quick_reply,
            full_fn=lambda state: self._run_full_turn(
                state["message"], state.get("_notify", notify), stream
            ),
        )
        state = self.workflow_engine.run(graph, {"message": user_message}, observer=notify)
        if isinstance(state.get("result"), LoopResult):
            return state["result"]
        if state.get("reply"):
            return AgentResult(
                reply=state["reply"],
                tool_calls=[],
                iterations=1,
                usage={"in": 0, "out": 0, "calls": 0},
            )
        return None
