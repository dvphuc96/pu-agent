"""Default provider-neutral AgentRuntime implementation."""

from __future__ import annotations

from dataclasses import replace

from pu.core.interfaces.agents import AgentRunRequest, AgentRuntime, AgentResult
from pu.core.interfaces.llm import LLMProvider
from pu.core.interfaces.tools import ToolContext, ToolHarness
from pu.loop.agent import run_loop


class DefaultAgentRuntime(AgentRuntime):
    """Run the shared provider-neutral loop through an injected ToolHarness."""

    def __init__(self, provider: LLMProvider, tool_harness: ToolHarness):
        self.provider = provider
        self.tool_harness = tool_harness

    def run(self, request: AgentRunRequest, observer=None) -> AgentResult:
        context = request.tool_context
        if context is None:
            context = ToolContext(
                observer=observer,
                actor="agent",
                principal="local-user",
                agent_id="pu-agent",
                scopes=request.tools.scopes(),
                capabilities=request.tools.agent_capabilities(),
            )
        elif context.observer is None:
            context = replace(context, observer=observer)
        return run_loop(
            provider=self.provider,
            model=request.model,
            system=request.system,
            messages=request.messages,
            tools=request.tools,
            tool_harness=self.tool_harness,
            max_iterations=request.max_iterations,
            max_tokens=request.max_tokens,
            observer=observer,
            stream=request.stream,
            tool_context=context,
        )
