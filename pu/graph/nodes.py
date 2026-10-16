"""Node factories for deterministic workflows around provider-neutral agents."""

from __future__ import annotations

from collections.abc import Callable, Iterable

from pu.core.interfaces.llm import LLMProvider, LLMRequest
from pu.core.interfaces.tools import ToolContext, ToolHarness
from pu.harness import DefaultSecurityHarness, DefaultToolHarness, InMemoryToolAuditSink
from pu.graph.engine import Node, NodeFn, RouteFn
from pu.loop.agent import run_loop
from pu.tools.registry import ToolRegistry


def tool_node(name: str, fn: Callable[..., object], in_keys: Iterable[str],
              out_key: str, **node_kwargs) -> Node:
    keys = list(in_keys)

    def run(state: dict) -> dict:
        return {out_key: fn(*[state.get(k) for k in keys])}

    return Node(name=name, fn=run, kind="tool", **node_kwargs)


def llm_node(name: str, prompt_template: str, out_key: str, *,
             provider: LLMProvider, model: str,
             max_tokens: int = 600, **node_kwargs) -> Node:
    """One provider-neutral model call with no tools."""

    def run(state: dict) -> dict:
        prompt = prompt_template.format(**{k: v for k, v in state.items()
                                           if not k.startswith("_")})
        response = provider.complete(LLMRequest(
            model=model,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        ))
        return {out_key: response.text}

    return Node(name=name, fn=run, kind="llm", **node_kwargs)


def agent_node(name: str, out_key: str, *, provider: LLMProvider,
               model: str, system: str, tools: ToolRegistry, tool_harness: ToolHarness | None = None,
               tool_context: ToolContext | None = None,
               max_iterations: int = 10, max_tokens: int = 2048,
               message_key: str = "message", **node_kwargs) -> Node:
    """A full provider-neutral loop turn as one graph node."""

    resolved_harness = tool_harness or DefaultToolHarness(
        tools, DefaultSecurityHarness(), InMemoryToolAuditSink()
    )
    resolved_context = tool_context or ToolContext(
        actor="agent",
        principal="graph-local-user",
        agent_id=name,
        scopes=tools.scopes(),
        capabilities=tools.capabilities(),
    )

    def run(state: dict) -> dict:
        messages = [{"role": "user", "content": state[message_key]}]
        result = run_loop(provider=provider, model=model, system=system,
                          messages=messages, tools=tools, tool_harness=resolved_harness,
                          max_iterations=max_iterations, max_tokens=max_tokens,
                          observer=state.get("_notify"), tool_context=resolved_context)
        return {out_key: result}

    return Node(name=name, fn=run, kind="agent", **node_kwargs)


def key_router(key: str, default: str) -> RouteFn:
    def route(state: dict) -> str:
        value = state.get(key)
        return value if isinstance(value, str) and value else default

    return route


def fn_node(name: str, fn: NodeFn, **node_kwargs) -> Node:
    return Node(name=name, fn=fn, **node_kwargs)
