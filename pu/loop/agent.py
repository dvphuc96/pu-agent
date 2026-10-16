"""Provider-neutral observe → reason → act loop.

The loop knows only the LLMProvider domain contract and the ToolHarness
execution boundary. ToolRegistry is catalog/metadata only.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
import time
from typing import Any

from pu.core.interfaces.agents import AgentResult
from pu.core.interfaces.llm import LLMProvider, LLMRequest, LLMResponse, LLMToolDefinition
from pu.core.interfaces.tools import ToolContext, ToolHarness
from pu.tools.registry import ToolRegistry

LoopEvent = dict[str, Any]
Observer = Callable[[str, LoopEvent], None]
LoopResult = AgentResult

# A reasoning model can spend its whole output budget thinking and come back
# with no text, no tool call, finish_reason=max_tokens — nothing usable. The
# nudge asks for the answer within the remaining budget; the fallback is what
# the user hears if the budget dies again. Silence is never a final reply.
_MAX_TOKENS_NUDGE = (
    "You hit the output token limit before writing anything. Answer now, as "
    "concisely as possible: lead with the direct answer, and if the full list "
    "does not fit, show the most important items and offer to continue."
)
_MAX_TOKENS_FALLBACK = (
    "(I ran out of output tokens before I could answer — try a smaller "
    "request, e.g. fewer items or a shorter summary.)"
)


def _tool_definitions(tools: ToolRegistry) -> list[LLMToolDefinition]:
    return [
        LLMToolDefinition(
            name=item.name,
            description=item.description,
            input_schema=item.input_schema,
        )
        for item in tools.agent_definitions()
    ]


def _assistant_message(response: LLMResponse) -> dict[str, Any]:
    blocks: list[dict[str, Any]] = []
    if response.text:
        blocks.append({"type": "text", "text": response.text})
    for call in response.tool_calls:
        block: dict[str, Any] = {
            "type": "tool_call",
            "id": call.id,
            "name": call.name,
            "arguments": call.arguments,
        }
        if call.metadata:
            block["metadata"] = call.metadata
        blocks.append(block)
    return {"role": "assistant", "content": blocks}


def run_loop(
    provider: LLMProvider,
    model: str,
    system: str,
    messages: list[dict],
    tools: ToolRegistry,
    tool_harness: ToolHarness,
    max_iterations: int = 10,
    max_tokens: int = 2048,
    observer: Observer | None = None,
    stream: bool = False,
    tool_context: ToolContext | None = None,
) -> LoopResult:
    """Run one agent turn through the provider-neutral + enforced tool boundaries."""
    notify = observer or (lambda kind, ev: None)
    result = LoopResult(reply="")
    definitions = _tool_definitions(tools)
    base_context = tool_context or ToolContext()
    if base_context.observer is not notify:
        base_context = replace(base_context, observer=notify)

    nudged = False
    for iteration in range(1, max_iterations + 1):
        result.iterations = iteration
        request = LLMRequest(
            model=model,
            system=system,
            messages=messages,
            tools=definitions,
            max_tokens=max_tokens,
            stream=stream,
        )

        def on_text(delta: str) -> None:
            notify("text", {"delta": delta})

        llm_started = time.perf_counter()
        try:
            response = provider.complete(request, on_text=on_text if stream else None)
        except Exception:
            if not stream:
                raise
            response = provider.complete(
                LLMRequest(
                    model=model,
                    system=system,
                    messages=messages,
                    tools=definitions,
                    max_tokens=max_tokens,
                    stream=False,
                )
            )
        llm_latency_ms = int((time.perf_counter() - llm_started) * 1000)

        notify(
            "llm",
            {
                "iteration": iteration,
                "stop_reason": response.finish_reason,
                "usage": {
                    "in": response.usage.input_tokens,
                    "out": response.usage.output_tokens,
                },
                "latency_ms": llm_latency_ms,
                "request_id": base_context.request_id,
                "workflow_id": base_context.workflow_id,
                "agent_id": base_context.agent_id,
            },
        )
        result.usage["in"] += response.usage.input_tokens
        result.usage["out"] += response.usage.output_tokens
        result.usage["calls"] += 1

        if (not response.text and not response.tool_calls
                and response.finish_reason == "max_tokens"):
            # Reasoning burned the budget mid-thought: no text, no call. Nudge
            # once for a concise answer; if it dies again, end the turn out
            # loud — never by returning reply="" (which reads as silence).
            if nudged:
                result.reply = _MAX_TOKENS_FALLBACK
                return result
            nudged = True
            messages.append({"role": "user", "content": _MAX_TOKENS_NUDGE})
            continue

        messages.append(_assistant_message(response))

        if not response.tool_calls:
            result.reply = response.text
            return result

        for call in response.tool_calls:
            call_context = replace(base_context, tool_call_id=call.id)
            tool_result = tool_harness.execute(call.name, call.arguments, call_context)
            output = tool_result.output
            event = {
                "tool": call.name,
                "args": call.arguments,
                "output": output,
                "ok": tool_result.ok,
                "error": tool_result.error,
                "execution_id": tool_result.metadata.get("execution_id"),
                "metadata": dict(tool_result.metadata),
                "approval_id": tool_result.metadata.get("approval_id"),
                "approval_status": tool_result.metadata.get("approval_status"),
                "action_tool": tool_result.metadata.get("action_tool"),
            }
            result.tool_calls.append(event)
            notify("tool", event)
            messages.append(
                {"role": "tool", "tool_call_id": call.id, "content": output}
            )

    result.reply = (
        "(I hit my iteration limit before finishing — try breaking the request into smaller steps.)"
    )
    return result
