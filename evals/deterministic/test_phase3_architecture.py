"""Phase 3 architecture gates: OOP contracts + composition root, no behavior rewrite."""

from __future__ import annotations

import inspect
from pathlib import Path

from evals.helpers import ScriptedProvider, response, text_block, tool_block
from pu.adapters.events import CallableEventPublisher, CompositeEventPublisher
from pu.adapters import GraphWorkflowEngine
from pu.runtime import DefaultAgentRuntime
from pu.harness import DefaultToolHarness
from pu.app import PU
from pu.config import Settings
from pu.core.container import create_application
from pu.core.interfaces import (
    AgentRunRequest,
    AgentRuntime,
    BaseAgent,
    EventPublisher,
    LLMProvider,
    MemoryRepository,
    SecurityHarness,
    ToolContext,
    ToolHarness,
    WorkflowEngine,
)
from pu.graph import END, START, Graph, Node
from pu.tools.registry import Tool, ToolRegistry


def test_core_contracts_are_runtime_checkable_and_vendor_free():
    root = Path(__file__).parents[2] / "pu"
    guarded = [root / "core", root / "agents", root / "app.py"]
    text = "\n".join(
        p.read_text(encoding="utf-8")
        for item in guarded
        for p in ([item] if item.is_file() else item.rglob("*.py"))
    )
    assert "import anthropic" not in text
    assert "import openai" not in text
    # Contract symbols import without either vendor SDK installed.
    assert LLMProvider and AgentRuntime and ToolHarness and SecurityHarness and WorkflowEngine
    assert EventPublisher and BaseAgent


def test_vendor_sdk_imports_are_confined_to_provider_boundary():
    root = Path(__file__).parents[2] / "pu"
    offenders = []
    for path in root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if "import anthropic" in text or "import openai" in text:
            rel = path.relative_to(root).as_posix()
            if rel in {"providers/llm/anthropic.py", "providers/llm/openai.py"}:
                continue
            offenders.append(rel)
    assert offenders == []


def test_composition_root_builds_application_with_injected_runtime(tmp_path):
    settings = Settings(home=tmp_path, api_key="offline", graph_workflows=False)
    client = ScriptedProvider([])
    app = create_application(settings=settings, llm_provider=client)

    assert isinstance(app, PU)
    assert isinstance(app, BaseAgent)
    assert isinstance(app.agent_runtime, DefaultAgentRuntime)
    assert isinstance(app.workflow_engine, GraphWorkflowEngine)
    assert isinstance(app.tool_harness, DefaultToolHarness)
    assert app.llm_provider is client
    assert app.memory is app.session.memory
    assert isinstance(app.memory, MemoryRepository)


def test_default_runtime_preserves_loop_behavior():
    registry = ToolRegistry()
    seen = []
    registry.register(Tool(
        name="echo",
        description="echo text",
        input_schema={"type": "object", "properties": {"text": {"type": "string"}}},
        fn=lambda text: (seen.append(text), text.upper())[1],
    ))
    client = ScriptedProvider([
        response([tool_block("echo", {"text": "hello"})], "tool_use"),
        response([text_block("done")]),
    ])
    harness = DefaultToolHarness(registry, __import__("pu.harness", fromlist=["DefaultSecurityHarness"]).DefaultSecurityHarness(), __import__("pu.harness", fromlist=["InMemoryToolAuditSink"]).InMemoryToolAuditSink())
    runtime = DefaultAgentRuntime(client, harness)
    result = runtime.run(AgentRunRequest(
        model="test",
        system="test",
        messages=[{"role": "user", "content": "echo hello"}],
        tools=registry,
    ))
    assert result.reply == "done"
    assert result.iterations == 2
    assert result.tool_calls[0]["output"] == "HELLO"
    assert seen == ["hello"]


def test_tool_registry_exposes_metadata_without_execution():
    calls = []
    registry = ToolRegistry()
    registry.register(Tool(
        name="write_thing",
        description="write",
        input_schema={"type": "object"},
        fn=lambda: (calls.append(1), "ok")[1],
        capability="test.write",
        side_effect=True,
    ))
    tool = registry.resolve("write_thing")
    assert tool is not None
    assert tool.definition.capability == "test.write"
    assert tool.definition.side_effect is True
    assert registry.definitions()[0].name == "write_thing"
    assert calls == []  # metadata lookup must never execute a capability

    assert not hasattr(registry, "execute"), "ToolRegistry must not expose an execution path"
    assert calls == []


def test_workflow_engine_adapter_wraps_existing_graph_behavior():
    graph = Graph("phase3")
    graph.add_node(Node("one", lambda state: {"value": state["seed"] + 1}))
    graph.add_edge(START, "one")
    graph.add_edge("one", END)
    state = GraphWorkflowEngine().run(graph, {"seed": 4})
    assert state["value"] == 5


def test_event_publisher_composition_preserves_observer_order():
    seen = []
    bus = CompositeEventPublisher([
        CallableEventPublisher(lambda kind, event: seen.append(("a", kind, event["n"]))),
        CallableEventPublisher(lambda kind, event: seen.append(("b", kind, event["n"]))),
    ])
    bus.publish("llm", {"n": 1})
    assert seen == [("a", "llm", 1), ("b", "llm", 1)]


def test_product_entrypoints_use_composition_root_not_direct_pu_constructor():
    import pu.gateway.cli as cli
    import pu.ops.brief as brief
    import pu.ops.browser_agent as browser_agent

    for module in (cli, brief, browser_agent):
        source = inspect.getsource(module)
        assert "create_application" in source
        assert "PU(" not in source
