"""Security Harness policies for tool execution.

This module implements the pre-execution security boundary. Human approval
binding is handled by the Tool Harness, while every call passes
through this policy chain so approval can be inserted without another bypass.
"""

from __future__ import annotations

import ipaddress
import re
from collections.abc import Iterable
from urllib.parse import urlparse

from pu.core.interfaces.security import SecurityDecision, SecurityHarness, SecurityPolicy
from pu.core.interfaces.tools import ToolContext, ToolDefinition

_SECRET_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b"),
    re.compile(r"\bya29\.[A-Za-z0-9._-]{10,}\b"),
    re.compile(r"(?i)\b(?:api[_-]?key|access[_-]?token|authorization)\s*[:=]\s*\S+"),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{12,}"),
)

_INJECTION_PATTERNS = (
    re.compile(r"(?i)bỏ\s+qua\s+(?:mọi|tất\s+cả)?\s*(?:hướng\s+dẫn|chỉ\s+dẫn)"),
    re.compile(r"(?i)(?:gửi|tiết\s+lộ|cung\s+cấp).*(?:api\s*key|khóa\s+api|mật\s+khẩu|access\s*token)"),
    re.compile(r"(?i)ignore\s+(?:all\s+)?(?:previous|prior)\s+instructions"),
    re.compile(r"(?i)reveal\s+(?:the\s+)?(?:system\s+prompt|api\s*key|secret|credentials?)"),
    re.compile(r"(?i)exfiltrat(?:e|ion).*(?:secret|credential|token|key)"),
)

_URL_KEYS = {"url", "uri", "endpoint", "callback", "webhook", "redirect_url"}


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, (list, tuple, set)):
        for item in value:
            yield from _strings(item)


def _url_values(arguments: dict):
    for key, value in arguments.items():
        if key.lower() in _URL_KEYS and isinstance(value, str):
            yield value
        elif isinstance(value, dict):
            yield from _url_values(value)


class SecretEgressPolicy(SecurityPolicy):
    name = "secret_egress"

    def evaluate(self, tool: ToolDefinition, arguments: dict, context: ToolContext) -> SecurityDecision:
        if not tool.external:
            return SecurityDecision(True, "local capability", self.name)
        for text in _strings(arguments):
            if any(pattern.search(text) for pattern in _SECRET_PATTERNS):
                return SecurityDecision(False, "possible secret in outbound tool arguments", self.name)
        return SecurityDecision(True, "no secret-like outbound data detected", self.name)


class SSRFPolicy(SecurityPolicy):
    name = "ssrf"

    def evaluate(self, tool: ToolDefinition, arguments: dict, context: ToolContext) -> SecurityDecision:
        for raw in _url_values(arguments):
            try:
                parsed = urlparse(raw)
                host = (parsed.hostname or "").strip().lower()
                if parsed.scheme not in {"http", "https"} or not host:
                    return SecurityDecision(False, "URL must use http/https with a host", self.name)
                if host in {"localhost", "localhost.localdomain"} or host.endswith(".localhost"):
                    return SecurityDecision(False, "localhost targets are blocked", self.name)
                try:
                    ip = ipaddress.ip_address(host)
                except ValueError:
                    ip = None
                if ip is not None and (
                    ip.is_private
                    or ip.is_loopback
                    or ip.is_link_local
                    or ip.is_multicast
                    or ip.is_reserved
                    or ip.is_unspecified
                ):
                    return SecurityDecision(False, "private/link-local network targets are blocked", self.name)
            except Exception:
                return SecurityDecision(False, "invalid URL", self.name)
        return SecurityDecision(True, "no unsafe URL target detected", self.name)


class PromptInjectionPolicy(SecurityPolicy):
    name = "prompt_injection"

    def evaluate(self, tool: ToolDefinition, arguments: dict, context: ToolContext) -> SecurityDecision:
        # Only treat content as an instruction-bearing external artifact when the
        # caller marks its provenance as untrusted. This avoids blocking a user
        # who legitimately asks to research prompt-injection examples.
        if not context.metadata.get("untrusted_input"):
            return SecurityDecision(True, "no untrusted provenance flag", self.name)
        if not tool.side_effect:
            return SecurityDecision(True, "read-only capability", self.name)
        for text in _strings(arguments):
            if any(pattern.search(text) for pattern in _INJECTION_PATTERNS):
                return SecurityDecision(False, "instruction-like untrusted content cannot trigger side effects", self.name)
        return SecurityDecision(True, "no high-confidence injection pattern detected", self.name)


class DefaultSecurityHarness(SecurityHarness):
    def __init__(self, policies: Iterable[SecurityPolicy] | None = None):
        self.policies = list(policies or (SSRFPolicy(), SecretEgressPolicy(), PromptInjectionPolicy()))

    def authorize(self, tool: ToolDefinition, arguments: dict, context: ToolContext) -> SecurityDecision:
        evaluated: list[str] = []
        for policy in self.policies:
            decision = policy.evaluate(tool, arguments, context)
            evaluated.append(getattr(policy, "name", decision.policy))
            if not decision.allowed:
                return SecurityDecision(
                    False,
                    decision.reason,
                    decision.policy,
                    {**decision.metadata, "evaluated": evaluated},
                )
        return SecurityDecision(True, "security policy chain allowed execution", "default_security", {"evaluated": evaluated})
