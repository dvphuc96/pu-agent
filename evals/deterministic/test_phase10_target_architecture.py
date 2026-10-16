"""Phase 10: target architecture lock-in and removal of migration shims."""
from __future__ import annotations

import inspect
from pathlib import Path

from evals.helpers import ScriptedProvider
from pu.adapters import GraphWorkflowEngine
from pu.app import PU
from pu.config import Settings
from pu.core.container import build_dependencies, create_application
from pu.runtime import DefaultAgentRuntime


ROOT = Path(__file__).resolve().parents[2]


def test_removed_compatibility_modules_and_dead_hosted_memory_assets_are_absent():
    removed = [
        ROOT / "pu" / "adapters" / "legacy",
        ROOT / "pu" / "providers" / "llm" / "compat.py",
        ROOT / "pu" / "providers" / "llm" / "legacy.py",
        ROOT / "pu" / "loop" / "models.py",
        ROOT / "sql" / "init_supabase.sql",
    ]
    assert all(not path.exists() for path in removed)


def test_composition_root_is_the_only_supported_application_construction_api(tmp_path):
    assert "client" not in inspect.signature(build_dependencies).parameters
    assert "client" not in inspect.signature(create_application).parameters
    assert tuple(inspect.signature(PU.__init__).parameters) == ("self", "dependencies")

    app = create_application(
        settings=Settings(home=tmp_path, api_key="offline", graph_workflows=False),
        llm_provider=ScriptedProvider([]),
    )
    try:
        assert isinstance(app.agent_runtime, DefaultAgentRuntime)
        assert isinstance(app.workflow_engine, GraphWorkflowEngine)
    finally:
        app.close()


def test_tool_registry_is_catalog_only_and_has_no_execution_method(tmp_path):
    app = create_application(
        settings=Settings(home=tmp_path, api_key="offline", graph_workflows=False),
        llm_provider=ScriptedProvider([]),
    )
    try:
        assert not hasattr(app.tools, "execute")
        assert "execute" not in type(app.tools).__dict__
    finally:
        app.close()


def test_runtime_source_has_no_removed_provider_coercion_or_vendor_shim_imports():
    forbidden = (
        "coerce_llm_provider",
        "pu.providers.llm.compat",
        "pu.providers.llm.legacy",
        "pu.adapters.legacy",
        "pu.loop.models",
    )
    offenders: list[str] = []
    for path in (ROOT / "pu").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if any(token in text for token in forbidden):
            offenders.append(str(path.relative_to(ROOT)))
    assert offenders == []


def test_provider_endpoint_overrides_are_provider_scoped_only():
    product_paths = [
        ROOT / "pu" / "config.py",
        ROOT / "pu" / "integrations.py",
        ROOT / "pu" / "providers" / "llm" / "catalog.py",
        ROOT / ".env.example",
    ]
    combined = "\n".join(path.read_text(encoding="utf-8") for path in product_paths)
    assert "PU_BASE_URL" not in combined
    assert "PU_ANTHROPIC_BASE_URL" in combined
    assert "PU_OPENAI_BASE_URL" in combined


def test_memory_read_and_write_capabilities_are_separate(tmp_path):
    app = create_application(
        settings=Settings(home=tmp_path, api_key="offline", graph_workflows=False),
        llm_provider=ScriptedProvider([]),
    )
    try:
        search = app.tools.resolve("search_memory")
        manage = app.tools.resolve("manage_memory")
        assert search is not None and search.side_effect is False
        assert search.capability == "memory:read" and search.required_scope == "memory:read"
        assert manage is not None and manage.side_effect is True
        assert manage.capability == "memory:write" and manage.required_scope == "memory:write"
        action = manage.input_schema["properties"]["action"]
        assert action["enum"] == ["update", "delete"]
        assert "query" not in manage.input_schema["properties"]
    finally:
        app.close()


def test_approval_repository_uses_operational_database_boundary_only():
    source = (ROOT / "pu" / "approval" / "repository.py").read_text(encoding="utf-8")
    assert "sqlite3.Connection" not in source
    assert "isinstance(" not in source
    assert "self._database.connection()" in source


def test_distribution_ships_privacy_ignore_and_safe_env_template():
    gitignore = ROOT / ".gitignore"
    env_example = ROOT / ".env.example"
    assert gitignore.exists() and env_example.exists()
    ignore_text = gitignore.read_text(encoding="utf-8")
    env_text = env_example.read_text(encoding="utf-8")
    assert ".pu/" in ignore_text and ".env" in ignore_text
    assert "!.env.example" in ignore_text
    assert "PU_PROVIDER=" in env_text
    assert "PU_ANTHROPIC_BASE_URL" in env_text
    assert "PU_OPENAI_BASE_URL" in env_text
    assert "PU_OPENROUTER_BASE_URL" in env_text
    assert "PU_DEEPSEEK_BASE_URL" in env_text
    assert "PU_GLM_BASE_URL" in env_text
    assert "PU_GEMINI_BASE_URL" in env_text


def test_product_provider_catalog_contains_supported_provider_set():
    from pu.providers.llm.catalog import PROVIDERS

    assert set(PROVIDERS) == {"anthropic", "openai", "openrouter", "deepseek", "glm", "gemini"}
