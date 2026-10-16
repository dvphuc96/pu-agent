"""Tool capability registry.

This registry is metadata/catalog only for product runtime. It can
resolve a concrete handler for ToolHarness, but it is no longer an execution
boundary and deliberately exposes no direct ``execute`` implementation.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from pu.core.interfaces.tools import ToolContext, ToolDefinition, ToolResult


@dataclass
class Tool:
    name: str
    description: str
    input_schema: dict[str, Any]
    fn: Callable[..., str | ToolResult]
    wants_notify: bool = False
    capability: str = "tool:execute"
    required_scope: str | None = None
    side_effect: bool = False
    external: bool = False
    version: str = "1"
    wants_context: bool = False

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name=self.name,
            description=self.description,
            input_schema=self.input_schema,
            capability=self.capability,
            required_scope=self.required_scope or self.capability,
            side_effect=self.side_effect,
            external=self.external,
            version=self.version,
        )

    def to_api(self) -> dict[str, Any]:
        """Provider-facing tool schema. Security metadata stays server-side."""
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }

    def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        """Concrete handler invocation; only ToolHarness should call this at runtime."""
        kwargs = dict(arguments)
        if self.wants_notify:
            kwargs["_notify"] = context.observer or (lambda kind, ev: None)
        if self.wants_context:
            kwargs["_context"] = context
        output = self.fn(**kwargs)
        if isinstance(output, ToolResult):
            return output
        return ToolResult(output=str(output))


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def resolve(self, name: str) -> Tool | None:
        """Resolve metadata + handler without executing it."""
        return self._tools.get(name)

    def definitions(self) -> list[ToolDefinition]:
        return [tool.definition for tool in self._tools.values()]

    def schemas(self) -> list[dict[str, Any]]:
        return [t.to_api() for t in self._tools.values()]

    def agent_definitions(self) -> list[ToolDefinition]:
        """Provider-facing catalog for ordinary agents.

        Side-effect tools are exposed as propose_* operations. The underlying
        concrete tool keeps its real name and write capability; ToolHarness
        resolves the alias and stores a ProposedAction instead of executing it.
        """
        out: list[ToolDefinition] = []
        for item in self.definitions():
            if item.side_effect:
                out.append(ToolDefinition(
                    name=f"propose_{item.name}",
                    description=(
                        f"Propose this action for human approval; do not claim it has executed. "
                        f"After approval PU will execute the underlying '{item.name}' action. "
                        + item.description
                    ),
                    input_schema=item.input_schema,
                    capability="action:propose",
                    required_scope=item.required_scope,
                    side_effect=False,
                    external=item.external,
                    version=item.version,
                ))
            else:
                out.append(item)
        return out

    def resolve_agent_name(self, name: str) -> tuple[Tool | None, bool]:
        """Resolve an agent-visible tool name to the concrete tool.

        Returns (tool, proposal_requested). Direct write names from stale model
        responses are also treated as proposals by ToolHarness, so they cannot
        bypass the approval boundary.
        """
        if name.startswith("propose_"):
            tool = self.resolve(name[len("propose_"):])
            if tool is not None and tool.definition.side_effect:
                return tool, True
            return None, True
        tool = self.resolve(name)
        return tool, bool(tool and tool.definition.side_effect)

    def capabilities(self) -> frozenset[str]:
        return frozenset(item.capability for item in self.definitions())

    def agent_capabilities(self) -> frozenset[str]:
        """Capabilities granted to normal reasoning agents. Never includes writes.

        ``action:propose`` is granted only when this catalog actually exposes a
        side-effect tool. Read-only specialized agents therefore have no latent
        proposal capability at all.
        """
        definitions = self.definitions()
        reads = {item.capability for item in definitions if not item.side_effect}
        if any(item.side_effect for item in definitions):
            reads.add("action:propose")
        return frozenset(reads)

    def scopes(self) -> frozenset[str]:
        return frozenset(item.required_scope for item in self.definitions())

    def subset(self, names: list[str] | tuple[str, ...] | set[str], *, strict: bool = False) -> ToolRegistry:
        """Return a read-only catalog view containing only selected tool names.

        The concrete ``Tool`` objects are shared with the parent registry, while
        the returned registry controls what an agent can *see*. ToolHarness still
        enforces capability/scope using the agent-specific ToolContext, so a
        hallucinated tool name outside the subset cannot be executed.
        """
        child = ToolRegistry()
        missing: list[str] = []
        for name in names:
            tool = self.resolve(name)
            if tool is None:
                missing.append(name)
                continue
            child.register(tool)
        if strict and missing:
            raise KeyError(f"unknown tool(s): {', '.join(sorted(missing))}")
        return child

