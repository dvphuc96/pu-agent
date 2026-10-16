"""Runtime data and secrets must remain excluded from distributable/source artifacts.

The Phase-10 source snapshot is intentionally usable without a .git directory,
so these tests validate the shipped ignore policy itself rather than relying on
repository metadata that disappears in ZIP releases.
"""

from __future__ import annotations

from fnmatch import fnmatch
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]


def _patterns() -> list[str]:
    lines = (REPO / ".gitignore").read_text(encoding="utf-8").splitlines()
    return [line.strip() for line in lines if line.strip() and not line.lstrip().startswith("#")]


def ignored(rel: str) -> bool:
    """Small deterministic matcher for the explicit privacy rules we ship."""
    rel = rel.replace("\\", "/")
    ignored_state = False
    for pattern in _patterns():
        negate = pattern.startswith("!")
        pat = pattern[1:] if negate else pattern
        candidates = [pat, pat.rstrip("/") + "/**"] if pat.endswith("/") else [pat]
        matched = any(fnmatch(rel, candidate) for candidate in candidates)
        # Directory prefixes such as .pu-*/ should match descendants.
        if not matched and pat.endswith("/"):
            prefix = rel.split("/", 1)[0] + "/"
            matched = fnmatch(prefix, pat)
        if matched:
            ignored_state = not negate
    return ignored_state


ARTIFACTS = [
    "state.db", "SOUL.md", "usage.jsonl", "calendar.ics",
    "traces/2026-07-26.jsonl", "outbox/msg-1.txt",
]


@pytest.mark.parametrize("home", [".pu", ".pu-alt", ".pu-demo"])
@pytest.mark.parametrize("artifact", ARTIFACTS)
def test_no_agent_home_leaks_its_runtime_data(home, artifact):
    assert ignored(f"{home}/{artifact}"), f"{home}/{artifact} is not covered by .gitignore"


@pytest.mark.parametrize("secret", [
    ".env", "credentials.json", "client_secret_123.json", "token.json",
    ".gcp/service-account.json",
])
def test_secrets_are_ignored(secret):
    assert ignored(secret), f"{secret} is not covered by .gitignore"


def test_snapshot_contains_no_runtime_or_secret_files():
    leaked = []
    for path in REPO.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(REPO).as_posix()
        if rel == ".env.example":
            continue
        if (rel.startswith((".pu/", ".pu-")) or rel == ".env" or rel.endswith(("credentials.json", "token.json"))) and not ignored(rel):
            leaked.append(rel)
    assert leaked == [], f"private/runtime files are in the snapshot: {leaked}"


def test_the_env_template_carries_no_real_secrets():
    lines = (REPO / ".env.example").read_text(encoding="utf-8").splitlines()
    assignments = [
        ln for ln in lines
        if "=" in ln and not ln.lstrip().startswith("#")
    ]
    unexpected = []
    for line in assignments:
        name, value = line.split("=", 1)
        value = value.strip().strip("\"'")
        if not value:
            continue
        if name == "PU_PROVIDER" and value in {"anthropic", "openai"}:
            continue
        unexpected.append(line)
    assert unexpected == [], f"non-placeholder values committed in .env.example: {unexpected}"
