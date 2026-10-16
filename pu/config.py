"""Configuration — every knob is an env var, documented in .env.example.

No settings framework: a dataclass read once at startup. If you can read this
file, you know everything PU can be configured to do.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import find_dotenv, load_dotenv


def _load_env() -> str:
    """Find the user's .env the way the user expects: from where they ARE.

    Bare `load_dotenv()` searches upward from the file that called it, not from
    the working directory. Inside a git checkout that is invisible — config.py
    lives in the project, so walking up from it lands on the project's .env and
    everything works. Installed from PyPI it walks up from site-packages,
    reaches the filesystem root, and finds nothing: the user stands in a folder
    holding a perfectly good .env and PU reports "No API key". Reported from
    a clean install on 2026-07-31, from inside the repo folder itself.

    usecwd=True is the whole fix. The walk upward is kept on purpose, so
    running `pu` from a subdirectory of your project still finds the .env at
    its root — the same rule git, npm and pytest already taught everyone.

    Returns the path that was loaded (empty string if none) so `pu doctor`
    and the first-run error can say WHICH file was read, rather than leaving
    people guessing between three .env files.
    """
    path = find_dotenv(usecwd=True)
    if path:
        load_dotenv(path)
    return path


DOTENV_PATH = _load_env()


@dataclass
class Settings:
    # --- LLM: pick a provider, set its key. See pu/providers/llm/catalog.py PROVIDERS.
    provider: str = field(default_factory=lambda: os.getenv("PU_PROVIDER", "anthropic"))
    # Explicit overrides (optional): key, endpoint, and model ids. Left empty,
    # the provider's own key env var and default models are used.
    api_key: str = field(default_factory=lambda: os.getenv("PU_API_KEY", ""))
    base_url: str | None = None
    model: str = field(default_factory=lambda: os.getenv("PU_MODEL", ""))
    # Cheap model used by the retrieval gate and the consolidation summarizer.
    small_model: str = field(default_factory=lambda: os.getenv("PU_SMALL_MODEL", ""))
    # Providers the user turned off in the dashboard (comma-separated ids).
    # Disabled providers are hidden from pickers/switchers; the ACTIVE provider
    # can't be disabled (guarded in integrations.apply_provider_disabled).
    disabled_providers: frozenset[str] = field(default_factory=lambda: frozenset(
        p.strip() for p in os.getenv("PU_DISABLED_PROVIDERS", "").split(",") if p.strip()))

    # --- Home: where PU keeps its state (memory DB, calendar, outbox, traces).
    # Defaults to ./.pu next to where you run it, so you can open every file
    # it writes. Local-first means you can always look.
    # `or ".pu"`: a bare "PU_HOME=" line in .env sets an EMPTY variable, and
    # os.getenv then returns "" instead of the default — which silently sent
    # home to the CWD (state.db/traces/outbox scattered in the repo root,
    # .pu/credentials.json overlooked by `pu connect`).
    home: Path = field(default_factory=lambda: Path(os.getenv("PU_HOME", "") or ".pu"))

    # --- Loop guardrails
    max_iterations: int = field(default_factory=lambda: int(os.getenv("PU_MAX_ITERATIONS", "10")))
    # Headroom matters for reasoning-capable models:
    # they spend output tokens thinking before the answer, so a low cap makes
    # them hit stop_reason=max_tokens mid-thought and return an EMPTY reply
    # 8192 leaves room to think AND
    # answer; it's a ceiling, not a target, so efficient models still cost the same.
    max_tokens: int = field(default_factory=lambda: int(os.getenv("PU_MAX_TOKENS", "8192")))
    # Working memory is a SLIDING WINDOW (like context RAM): only the last N
    # turns go into the prompt. Older turns aren't lost — they're in state.db,
    # distilled into facts by consolidation, and pulled back by the retrieval
    # gate when relevant. Without this cap a long thread (esp. the always-on
    # long-lived session resends its whole history every turn until it explodes.
    history_turns: int = field(default_factory=lambda: int(os.getenv("PU_HISTORY_TURNS", "12")))

    # --- Memory
    # Durable local memory uses SQLite + FTS5. Hosted stores are outside the
    # current product scope unless a deployment requirement adds an adapter.
    consolidate_every: int = field(default_factory=lambda: int(os.getenv("PU_CONSOLIDATE_EVERY", "6")))
    retrieval_top_k: int = field(default_factory=lambda: int(os.getenv("PU_RETRIEVAL_TOP_K", "4")))

    # --- Tools / execution harness
    tool_rate_limit_per_minute: int = field(
        default_factory=lambda: int(os.getenv("PU_TOOL_RATE_LIMIT_PER_MINUTE", "120"))
    )
    # Safe automatic retry count for external READ tools only. Side effects are
    # never retried automatically; approval/execution idempotency remains explicit.
    tool_read_retries: int = field(
        default_factory=lambda: int(os.getenv("PU_TOOL_READ_RETRIES", "2"))
    )
    approval_ttl_minutes: int = field(
        default_factory=lambda: int(os.getenv("PU_APPROVAL_TTL_MINUTES", "60"))
    )
    # --- Report quality / Evaluation Harness
    report_quality_min_score: float = field(
        default_factory=lambda: float(os.getenv("PU_REPORT_QUALITY_MIN_SCORE", "0.72"))
    )
    report_min_completeness: float = field(
        default_factory=lambda: float(os.getenv("PU_REPORT_MIN_COMPLETENESS", "0.65"))
    )
    report_min_source_coverage: float = field(
        default_factory=lambda: float(os.getenv("PU_REPORT_MIN_SOURCE_COVERAGE", "0.80"))
    )
    report_freshness_days: int = field(
        default_factory=lambda: int(os.getenv("PU_REPORT_FRESHNESS_DAYS", "30"))
    )
    report_judge_enabled: bool = field(
        default_factory=lambda: os.getenv("PU_REPORT_JUDGE", "") in ("1", "true", "yes")
    )
    report_judge_model: str = field(
        default_factory=lambda: os.getenv("PU_REPORT_JUDGE_MODEL", "")
    )
    # Mirror locally-created events to Google Calendar. SQLite + ICS remain the
    # source of truth; this is an opt-in write target.
    google_calendar: bool = field(
        default_factory=lambda: os.getenv("PU_GOOGLE_CALENDAR", "") in ("1", "true", "yes")
    )
    google_calendar_id: str = field(
        default_factory=lambda: os.getenv("PU_GOOGLE_CALENDAR_ID", "") or "primary"
    )
    # Gmail CRM inbox (read-only pull, see pu/tools/gmail.py). Same reasoning
    # as gh_tool: the tool is only registered when this is on, because every
    # registered tool ships in every prompt.
    gmail: bool = field(
        default_factory=lambda: os.getenv("PU_GMAIL", "") in ("1", "true", "yes")
    )
    gmail_label: str = field(default_factory=lambda: os.getenv("PU_GMAIL_LABEL", "") or "INBOX")
    gmail_max_messages: int = field(
        default_factory=lambda: int(os.getenv("PU_GMAIL_MAX_MESSAGES", "50") or "50")
    )
    # Route every message through the triage graph workflow first (a small model
    # classifies it; trivial messages get a fast small-model reply, real tasks
    # run the normal loop as a graph node). Any failure anywhere fails open to
    # the plain loop, so this can never make PU worse — only faster/cheaper.
    graph_workflows: bool = field(
        default_factory=lambda: os.getenv("PU_GRAPH_WORKFLOWS", "") in ("1", "true", "yes")
    )

    # --- AgentOps / SLOs (durable events + optional OTLP traces + /metrics)
    otel_endpoint: str = field(
        default_factory=lambda: os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "")
    )
    slo_llm_p95_ms: int = field(
        default_factory=lambda: int(os.getenv("PU_SLO_LLM_P95_MS", "15000"))
    )
    slo_tool_error_rate: float = field(
        default_factory=lambda: float(os.getenv("PU_SLO_TOOL_ERROR_RATE", "0.05"))
    )
    slo_workflow_success_rate: float = field(
        default_factory=lambda: float(os.getenv("PU_SLO_WORKFLOW_SUCCESS_RATE", "0.95"))
    )
    slo_approval_failure_rate: float = field(
        default_factory=lambda: float(os.getenv("PU_SLO_APPROVAL_FAILURE_RATE", "0.05"))
    )
    slo_security_blocks_max: int = field(
        default_factory=lambda: int(os.getenv("PU_SLO_SECURITY_BLOCKS_MAX", "25"))
    )

    def ensure_home(self) -> Path:
        self.home.mkdir(parents=True, exist_ok=True)
        (self.home / "traces").mkdir(exist_ok=True)
        (self.home / "outbox").mkdir(exist_ok=True)
        return self.home


def load_settings() -> Settings:
    return Settings()
