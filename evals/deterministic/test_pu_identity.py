"""Mechanical identity-migration checks for PU Agent.

These tests intentionally avoid importing the LLM SDKs so they can guard the
rename even in a minimal development environment.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

from pu.config import Settings


ROOT = Path(__file__).resolve().parents[2]

# Tooling and runtime state, not product source: this scan guards the identity
# we ship. Editor/agent tooling (e.g. .claude skills that reference legacy
# branch/env names) and local runtime output must never fail it — a venv or a
# chat transcript containing the old name is not a migration regression.
_SCAN_EXCLUDES = {
    ".git", ".claude", ".venv", "venv", ".pu", "node_modules",
    "__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache",
    "traces", "outbox", "reports",
}


def _is_excluded(relative: Path) -> bool:
    return any(part in _SCAN_EXCLUDES for part in relative.parts)


def test_distribution_package_and_cli_are_pu() -> None:
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    assert data["project"]["name"] == "pu-agent"
    assert data["project"]["scripts"] == {"pu": "pu.__main__:main"}
    assert data["tool"]["hatch"]["build"]["targets"]["wheel"]["packages"] == ["pu"]
    assert (ROOT / "pu" / "__init__.py").is_file()


def test_default_runtime_home_uses_pu_namespace(monkeypatch) -> None:
    monkeypatch.delenv("PU_HOME", raising=False)
    assert Settings().home == Path(".pu")

    # "PU_HOME=" in a .env sets an EMPTY variable; os.getenv then returns ""
    # (not the default), which used to send home to the CWD — scattering
    # state.db/traces/outbox in the repo root and hiding .pu/credentials.json.
    monkeypatch.setenv("PU_HOME", "")
    assert Settings().home == Path(".pu")

    monkeypatch.setenv("PU_HOME", "/tmp/pu-home")
    assert Settings().home == Path("/tmp/pu-home")


def test_repository_has_no_legacy_identity_in_paths_or_utf8_text() -> None:
    old = "ti" + "ni"
    forbidden = (old, old.title(), old.upper())
    offenders: list[str] = []

    for path in ROOT.rglob("*"):
        relative = path.relative_to(ROOT)
        if _is_excluded(relative):
            continue
        if any(token in str(relative) for token in forbidden):
            offenders.append(str(relative))
            continue
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if any(token in text for token in forbidden):
            offenders.append(str(relative))

    assert offenders == []
