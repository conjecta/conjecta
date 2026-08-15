from __future__ import annotations

import os

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python <3.11
    import tomli as tomllib

import dataclasses
import logging
import types
import warnings
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from threading import RLock
from typing import Any, Union, get_args, get_origin, get_type_hints

from math_agent.mcp_config import McpServerConfig, parse_mcp_servers

if not os.environ.get("PYTEST_CURRENT_TEST"):
    from dotenv import load_dotenv

    load_dotenv()

log = logging.getLogger(__name__)


class ConfigError(Exception):
    """Raised when the configuration is invalid.

    Loading is strict by default: unknown TOML keys, uncoercible values, and
    inconsistent cross-field combinations abort startup with a clear message
    naming the offending key instead of silently falling back to defaults.
    Set ``CONJECTA_CONFIG_STRICT=0`` to downgrade unknown-key errors to
    warnings (type and validation errors still raise).
    """


@dataclass
class LLMConfig:
    provider: str = "shengsuanyun"
    model: str = "deepseek/deepseek-v4-pro"
    temperature: float = 0.7
    api_key: str = ""
    base_url: str = ""
    timeout_seconds: float = 300.0
    # Transient-error retries (429/5xx/connection) per request; 0 disables.
    retry_max_attempts: int = 3
    # Backoff between attempts: base, 3x base, 9x base (with small jitter).
    retry_base_seconds: float = 5.0
    # Hard cap on LLM calls (actor + critic + codegen + tactic generation) per
    # problem; exceeding it ends the solve gracefully as best_effort.
    max_calls_per_problem: int = 200


@dataclass
class CriticConfig:
    provider: str = "shengsuanyun"
    model: str = "deepseek/deepseek-v4-pro"
    temperature: float = 0.2
    timeout_seconds: float = 180.0
    # Optional OpenAI-compatible endpoint override (same semantics as
    # LLMConfig.base_url); empty uses the provider default.
    base_url: str = ""


@dataclass
class ProverConfig:
    """Optional formal-prover role model (e.g. DeepSeek-Prover / Kimina-Prover
    served behind an OpenAI-compatible endpoint).

    Empty ``model`` (the default) disables the role; tactic generation and
    Lean codegen then use the main backend. Set ``base_url`` to point at a
    self-hosted endpoint; the provider table is bypassed in that case.
    """

    provider: str = "openai"
    model: str = ""
    temperature: float = 0.7
    timeout_seconds: float = 300.0
    base_url: str = ""
    api_key: str = ""


@dataclass
class HitlConfig:
    """Durable human-in-the-loop policy for production solves."""

    enabled: bool = False
    mode: str = "adaptive"
    review_research_plan: bool = True
    ask_on_counterexample: bool = True
    ask_on_reviewer_block: bool = True
    approval_tools: list[str] = field(
        default_factory=lambda: ["add_material", "relate_knowledge"]
    )
    auto_approve_tools: list[str] = field(
        default_factory=lambda: [
            "compute",
            "search",
            "search_arxiv",
            "search_scholar",
            "fetch_url",
            "searching",
            "read_sources",
            "search_materials",
            "search_knowledge",
            "find_related",
            "search_mathlib",
            "plot_figure",
            "formalize",
            "lean_check",
        ]
    )
    max_interrupts_per_run: int = 3
    allow_edit_action: bool = True
    allow_freeform_feedback: bool = True
    # Seconds a paused run waits for a human decision before the default
    # decision (approve when allowed) is auto-claimed and the run resumes
    # headless. 0 or negative disables auto-resolve (wait forever).
    auto_resolve_seconds: float = 600.0


@dataclass
class AgentConfig:
    max_react_steps: int = 12
    max_conclusion_revisions: int = 3
    # Registry-tool executions per solve, including resumed work. None means
    # unlimited; 0/negative values are deprecated aliases for None and are
    # normalized in __post_init__ with a DeprecationWarning.
    max_tool_calls: int | None = 8
    max_wall_seconds: float = 600.0
    max_identical_action_repeats: int = 2
    conclusion_candidate_count: int = 1
    candidate_search_min_turns: int = 4
    max_scheduler_iterations: int = 100
    max_retries_per_stage: int = 3
    tool_heartbeat_seconds: float = (
        10.0  # emit tool_progress while a tool runs longer than this
    )
    # REPL-backed search makes each attempt cheap (no per-step recompile), so
    # the default budget is larger than the old batch-compile era allowed.
    tactic_search_max_attempts: int = 64
    # Escalation policy for formal proofs: when require_formal_verification is
    # true and the first round fails verification, rerun with larger budgets
    # and diagnostics-driven context. Planning is forced on, and a bounded
    # number of replan rounds inject the previous round's Lean failures.
    escalation_max_react_steps: int = 24
    escalation_max_tool_calls: int = 16
    escalation_replan_rounds: int = 1
    tactic_search_max_depth: int = 12
    tactic_search_wall_seconds: float = 180.0
    # Cap on search_mathlib calls per solve; the ReAct prompt prose injects
    # the same value so the model sees the real limit.
    search_mathlib_max_calls: int = 3
    # Step budget for the lightweight clarify/follow-up path in the supervisor.
    clarify_max_steps: int = 4
    # Deep-search profile used only by the formal escalation route that forces
    # tactic_search / prove_by_lemmas after repeated one-shot repair failure.
    deep_search_wall_seconds: float = 3600.0
    deep_search_max_attempts: int = 200
    # Number of strategy-diversified subagent routes run concurrently in a
    # deep_search escalation round (e.g. tactic_search-biased vs
    # prove_by_lemmas-biased); the first verified result wins. 1 keeps the
    # legacy single-route serial behavior.
    deep_search_parallel_routes: int = 2
    tools: list[str] = field(
        default_factory=lambda: [
            "compute",
            "search",
            "search_arxiv",
            "search_scholar",
            "fetch_url",
            "searching",
            "read_sources",
            "add_material",
            "search_materials",
            "search_knowledge",
            "relate_knowledge",
            "find_related",
            "search_mathlib",
            "plot_figure",
            "formalize",
            "lean_check",
            "tactic_search",
            "prove_by_lemmas",
        ]
    )
    reviewers_enabled: list[str] = field(
        default_factory=lambda: ["critic", "fidelity", "completeness"]
    )
    # Skip the reviewer panel when the conclude action's geometric-mean token
    # probability (exp(mean logprob)) is at least this value. When logprobs are
    # unavailable, review still runs unless the prompt is classified as easy.
    skip_review_min_confidence: float = 0.90
    skip_review_on_easy_prompt: bool = True
    # Easy/hard prompt classification gates the heavy stages (planning,
    # claim_check, mid_verify, reviewer panel). "critic" asks the cheap critic
    # model once per solve (JSON verdict; failures fall back to hard);
    # "rules" skips that call and only fast-paths trivial arithmetic prompts.
    easy_prompt_classifier: str = "critic"
    # LLM reviewer panel voting: a conclusion is sent back for revision only
    # when the confidence-weighted FAIL votes reach the weighted PASS votes
    # plus this margin. The default 0.0 lets ties favor FAIL (conservative).
    # Formal/Lean reports are unaffected: a FAIL there always vetoes.
    review_vote_margin: float = 0.0
    # Non-easy normal mode: early hypothesis audit + computational refute.
    # Production config.toml enables these; dataclass defaults stay off so unit
    # tests with FakeLLM queues are not disrupted.
    normal_claim_check_enabled: bool = False
    # Non-easy normal mode: never skip the reviewer panel due to logprobs.
    normal_force_review: bool = False
    # Never skip the reviewer panel for any reason; ordinary solves keep the
    # skip shortcuts above.
    force_review: bool = False
    normal_claim_check_max_tool_calls: int = 1
    # Mid-trace verification checkpoints: judge recent turns for checkable
    # intermediate claims and verify them (compute refute / Lean formalize).
    # Production config.toml enables this; dataclass default stays off so unit
    # tests with FakeLLM queues are not disrupted. Checkpoint tool calls use
    # their own budget and never consume max_tool_calls.
    mid_verify_enabled: bool = False
    mid_verify_max_calls: int = 3
    # Run the checkpoint judge at most once per this many turns.
    mid_verify_every: int = 2
    # Failed checkpoints beyond this count mark the claim as unresolved in the
    # verification issues carried into the final answer metadata.
    mid_verify_max_corrections: int = 2
    # One up-front planning call for non-easy prompts (plan-first bounded ReAct).
    planning_enabled: bool = True
    planning_max_chars: int = 2500
    # Normal-mode actor context window cap (chars). Traces hydrated from
    # historical research checkpoints keep using research_context_max_chars.
    react_context_max_chars: int = 16_000
    # Token-budget trigger for proactive context compaction: when the rendered
    # actor context exceeds this many estimated tokens, older turns are
    # compacted even if the turn-count window has not overflowed yet.
    # 0 derives the budget from react_context_max_chars / 4.
    react_context_max_tokens: int = 0
    memory_consolidation_enabled: bool = True
    memory_consolidation_model: str | None = None
    artifact_root: str = "logs/artifacts"
    mcp_progressive_disclosure: bool = True
    mcp_initial_top_k: int = 3
    # Research mode was removed along with its orchestrator; these two knobs
    # outlived it and are still read on the normal ReAct path (claim-check
    # refutation, and the context budget for hydrated traces). The remaining
    # research_* settings are gone -- such keys in an existing config.toml get
    # a dedicated "removed" warning at load time, so old files keep loading.
    research_refutation_enabled: bool = True
    research_context_max_chars: int = 24_000
    hitl: HitlConfig = field(default_factory=HitlConfig)

    def __post_init__(self) -> None:
        if self.max_tool_calls is not None and self.max_tool_calls <= 0:
            warnings.warn(
                "max_tool_calls=0/negative is deprecated and treated as "
                "unlimited; use None (null) for an unlimited tool-call budget",
                DeprecationWarning,
                stacklevel=2,
            )
            self.max_tool_calls = None


@dataclass
class VerifierConfig:
    strictness: str = "high"
    formal_policy: str = "explicit"  # explicit | all_theorems | disabled
    require_lean_for_theorems: bool = False  # legacy alias for all_theorems
    prefer_lean: bool = False
    fallback_to_human: bool = True


@dataclass
class LeanConfig:
    enabled: bool = True
    lake_path: str = "lake"
    lean_path: str = "lean"
    max_repair_attempts: int = 3
    mathlib_dep: bool = True
    workspace_dir: str = ".lean_workspace"
    lean_toolchain: str = "leanprover/lean4:v4.30.0"
    mathlib_repo: str = "https://github.com/leanprover-community/mathlib4"
    mathlib_rev: str = "v4.30.0"
    prefetch_cache: bool = True
    update_timeout_seconds: int = 600
    build_timeout_seconds: int = 600
    # Overall wall budget for one prove_by_lemmas decomposition run; must stay
    # below the solve-level max_wall_seconds so a single lemma DAG cannot eat
    # the whole solve budget.
    lemma_executor_wall_seconds: float = 240.0
    # Lemmas in the same dependency level are proved concurrently, up to this
    # many workers (Lean checks stay capped by max_concurrent_checks).
    lemma_max_parallel: int = 6
    # Recursive rescue: a lemma that exhausts repairs gets one sub-decomposition
    # round before the whole decomposition run aborts.
    lemma_rescue_enabled: bool = True
    # Multi-route: sample this many temperature-diversified proof bodies per
    # lemma attempt and accept the first that verifies.
    lemma_route_count: int = 3
    # prove_by_lemmas per-lemma search hook (REPL tactic search) budgets.
    lemma_hook_max_attempts: int = 12
    lemma_hook_max_depth: int = 6
    # Hard cap on recursive rescue depth (each level re-decomposes a failed
    # sub-lemma once more).
    lemma_rescue_max_depth: int = 2
    # Temperature ladder for multi-route lemma proof sampling; extended by
    # repeating the last entry when more routes than entries are requested.
    lemma_route_temperatures: list[float] = field(
        default_factory=lambda: [0.0, 0.5, 0.9]
    )
    # Lemmas rated at or above this difficulty (1-5 model scale) get up to
    # lemma_max_routes_hard temperature-diversified proof routes.
    lemma_difficulty_threshold: int = 4
    lemma_max_routes_hard: int = 5
    # Character cap for a single lean_check input; longer sources must be
    # broken into smaller lemmas.
    max_check_chars: int = 8000
    premise_index_enabled: bool = True
    max_concurrent_checks: int = 4
    result_cache_size: int = 256
    reject_unsafe_source: bool = True
    # Lean REPL (leanprover-community/repl) long-running session for tactic
    # search with structured proof states. When enabled, the workspace
    # lakefile also requires the repl package so `lake build repl` produces
    # the binary at .lake/packages/repl/.lake/build/bin/repl.
    repl_enabled: bool = False
    repl_repo: str = "https://github.com/leanprover-community/repl"
    repl_rev: str = "v4.30.0"
    # Per-request timeout for one REPL command/tactic step (imports excluded:
    # the initial `import ...` command can take much longer on a cold cache).
    repl_step_timeout_seconds: float = 60.0
    # Timeout for the very first command in a session (pays import elaboration).
    repl_init_timeout_seconds: float = 300.0
    repl_max_sessions: int = 4
    # Proactive session recycling: a session that has served this many REPL
    # commands is closed at pool checkin and replaced with a fresh one.
    # Retained proof states make RSS grow with command count, so without
    # recycling a long-lived session eventually hits the cgroup MemoryMax
    # mid-search; recycling turns that into a planned between-search restart.
    # 0 disables recycling.
    repl_recycle_after_commands: int = 400
    # Hard per-session address-space cap. A runaway elaboration can otherwise
    # grow a single REPL to tens of GB and take the whole host down (no swap
    # means the kernel evicts page cache instead of OOM-killing the culprit).
    # 0 disables the limit.
    repl_memory_limit_mb: int = 4096


@dataclass
class UploadConfig:
    target: str = "package"
    github_repo: str = "leanprover-community/mathlib4"


@dataclass
class LoggingConfig:
    enabled: bool = True
    level: str = "INFO"  # DEBUG, INFO, WARNING, ERROR
    dir: str = "logs"


@dataclass
class SearchConfig:
    provider: str = "tavily"
    max_results: int = 5
    # Web fallback when the primary provider fails: "duckduckgo" or "none".
    fallback_provider: str = "duckduckgo"


@dataclass
class KnowledgeConfig:
    embedding_enabled: bool = False
    embedding_provider: str = "openai"
    embedding_model: str = "text-embedding-3-small"
    embedding_api_key: str = ""
    hybrid_search_top_k: int = 20


@dataclass
class MathNewsConfig:
    # Background refresh uses the system API key for this provider.
    provider: str = "deepseek"
    model: str = "deepseek-chat"
    refresh_seconds: int = 6 * 3600
    min_interval_seconds: int = 3600


@dataclass
class WebConfig:
    """Web-layer admission-control state backend.

    "memory" (default) keeps rate-limit, throttle, capacity, and quota-
    reservation state in-process — correct only for single-replica
    deployments. "redis" shares that state across replicas via redis_url.
    See docs/distributed-state.md.
    """

    state_backend: str = "memory"
    redis_url: str | None = None


@dataclass
class Config:
    llm: LLMConfig = field(default_factory=LLMConfig)
    critic: CriticConfig = field(default_factory=CriticConfig)
    prover: ProverConfig = field(default_factory=ProverConfig)
    agent: AgentConfig = field(default_factory=AgentConfig)
    verifier: VerifierConfig = field(default_factory=VerifierConfig)
    lean: LeanConfig = field(default_factory=LeanConfig)
    upload: UploadConfig = field(default_factory=UploadConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    search: SearchConfig = field(default_factory=SearchConfig)
    knowledge: KnowledgeConfig = field(default_factory=KnowledgeConfig)
    math_news: MathNewsConfig = field(default_factory=MathNewsConfig)
    web: WebConfig = field(default_factory=WebConfig)
    mcp_servers: list[McpServerConfig] = field(default_factory=list)


def _unwrap_optional(typ: Any) -> Any:
    """Return the concrete type inside ``Optional[X]`` or ``X | None``."""
    origin = get_origin(typ)
    if origin is Union or origin is types.UnionType:
        args = [a for a in get_args(typ) if a is not type(None)]
        if len(args) == 1:
            return args[0]
    return typ


def _coerce_value(
    field_name: str, value: Any, typ: Any, fallback: Any, path: str = ""
) -> Any:
    """Coerce a raw TOML/env value to the dataclass field type.

    Raises ConfigError naming the key, expected type, and received value when
    conversion fails; config errors must surface at startup instead of
    silently falling back to defaults. ``fallback`` is used only when the
    value is explicitly None (an unset Optional field).
    """
    if value is None:
        return fallback
    typ = _unwrap_optional(typ)
    if typ is Any:
        return value
    key = f"{path}{field_name}"
    if typ is bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            lowered = value.strip().lower()
            if lowered in {"1", "true", "yes", "on"}:
                return True
            if lowered in {"0", "false", "no", "off"}:
                return False
        raise ConfigError(f"{key}: expected bool, got {value!r}")
    if typ is int:
        if isinstance(value, bool) or (
            isinstance(value, float) and not value.is_integer()
        ):
            raise ConfigError(f"{key}: expected int, got {value!r}")
        try:
            return int(value)
        except (TypeError, ValueError):
            raise ConfigError(f"{key}: expected int, got {value!r}") from None
    if typ is float:
        if isinstance(value, bool):
            raise ConfigError(f"{key}: expected float, got {value!r}")
        try:
            return float(value)
        except (TypeError, ValueError):
            raise ConfigError(f"{key}: expected float, got {value!r}") from None
    if typ is str:
        return str(value)
    origin = get_origin(typ)
    if origin is list or typ is list:
        if isinstance(value, str):
            value = [part.strip() for part in value.split(",") if part.strip()]
        if not isinstance(value, (list, tuple)):
            raise ConfigError(f"{key}: expected a list, got {value!r}")
        args = get_args(typ)
        element_type = _unwrap_optional(args[0]) if args else str
        return [
            _coerce_value(field_name, item, element_type, item, path=path)
            for item in value
        ]
    return value


def _build_dataclass(cls: type, data: dict[str, Any], section: str = "") -> Any:
    valid_fields = {f.name: f for f in cls.__dataclass_fields__.values()}
    hints = get_type_hints(cls)
    kwargs: dict[str, Any] = {}
    for name, config_field in valid_fields.items():
        if name not in data:
            continue
        if config_field.default is not dataclasses.MISSING:
            fallback = config_field.default
        elif config_field.default_factory is not dataclasses.MISSING:
            fallback = config_field.default_factory()
        else:
            fallback = None
        kwargs[name] = _coerce_value(
            name, data[name], hints.get(name, Any), fallback, path=section
        )
    return cls(**kwargs)


def _config_strict() -> bool:
    """Strict loading is the default; CONJECTA_CONFIG_STRICT=0 downgrades
    unknown-key errors to warnings."""
    return os.environ.get("CONJECTA_CONFIG_STRICT", "1").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }


def _check_unknown_keys(
    section: str,
    data: dict[str, Any],
    allowed: set[str],
    *,
    strict: bool,
    warn_legacy_research: bool = False,
) -> None:
    """Reject (strict) or warn about (non-strict) unknown keys in a section.

    Removed ``research_*`` keys always get a dedicated "removed" warning and
    never hard-fail, so pre-removal config files keep loading.
    """
    unknown = sorted(key for key in data if key not in allowed)
    if warn_legacy_research:
        legacy = [key for key in unknown if key.startswith("research_")]
        for key in legacy:
            log.warning(
                "Ignoring config key [%s] %s: research mode was removed and "
                "this setting no longer exists",
                section,
                key,
            )
        unknown = [key for key in unknown if key not in legacy]
    if not unknown:
        return
    message = f"Unknown config key(s) in [{section}]: {', '.join(unknown)}"
    if strict:
        raise ConfigError(
            f"{message}. Remove the key(s) or fix the typo; set "
            "CONJECTA_CONFIG_STRICT=0 to downgrade this to a warning."
        )
    log.warning(
        "%s; ignoring them (CONJECTA_CONFIG_STRICT=0).", message
    )


def _section(raw: dict[str, Any], name: str) -> dict[str, Any]:
    value = raw.get(name, {})
    if not isinstance(value, dict):
        raise ConfigError(f"[{name}] must be a TOML table, got {value!r}")
    return value


_CONFIG_CACHE: dict[Any, Config] = {}
_CONFIG_CACHE_LOCK = RLock()
# Every environment variable that can change a loaded Config. Explicit names
# cover _apply_env_overrides; the prefix covers the dynamic
# CONJECTA_MCP_<NAME>_COMMAND keys resolved in parse_mcp_servers.
_ENV_OVERRIDE_NAMES = (
    "CONJECTA_ARTIFACT_ROOT",
    "CONJECTA_CONFIG_STRICT",
    "CONJECTA_LEAN_BUILD_TIMEOUT",
    "CONJECTA_LEAN_TOOLCHAIN",
    "CONJECTA_LLM_API_KEY",
    "CONJECTA_LLM_BASE_URL",
    "CONJECTA_LLM_MODEL",
    "CONJECTA_LLM_PROVIDER",
    "CONJECTA_MATH_NEWS_MIN_INTERVAL_SECONDS",
    "CONJECTA_MATH_NEWS_REFRESH_SECONDS",
)
_ENV_OVERRIDE_PREFIX = "CONJECTA_MCP_"


def _config_cache_key(path: Path | None) -> tuple[Any, ...]:
    """Identity of a loaded config: the file it came from plus every env var
    that can override it, so a changed file or env produces a fresh load."""
    try:
        stat = path.stat() if path is not None else None
        stamp = (stat.st_mtime_ns, stat.st_size) if stat is not None else None
    except OSError:
        stamp = None
    env = tuple(
        sorted(
            (name, value)
            for name, value in os.environ.items()
            if name in _ENV_OVERRIDE_NAMES or name.startswith(_ENV_OVERRIDE_PREFIX)
        )
    )
    return (str(path) if path is not None else None, stamp, env)


def load_config(path: Path | None = None) -> Config:
    """Load configuration, reusing the parsed result for an unchanged file.

    This is called on nearly every request path (~1.2ms per parse across 17
    call sites), so the parsed object is cached and shared. Callers must treat
    the result as read-only; only ``_apply_env_overrides`` mutates it, during
    construction.
    """
    if path is None:
        candidates = [Path("config.toml"), Path("config.example.toml")]
        for c in candidates:
            if c.exists():
                path = c
                break

    key = _config_cache_key(path)
    with _CONFIG_CACHE_LOCK:
        cached = _CONFIG_CACHE.get(key)
    if cached is not None:
        return cached

    config = _load_config_uncached(path)
    # Cache misses are rare (one per distinct file+env combination), so the
    # effective-config summary is logged exactly once per distinct config.
    log.info(
        "Effective config (%s): %s",
        path if path is not None else "defaults",
        redacted_summary(config),
    )
    with _CONFIG_CACHE_LOCK:
        if len(_CONFIG_CACHE) >= 32:
            # Bound the cache: keys vary with env, and a pathological caller
            # flipping env vars must not grow this without limit.
            _CONFIG_CACHE.clear()
        _CONFIG_CACHE[key] = config
    return config


def clear_config_cache() -> None:
    """Drop cached configs (tests, and any deliberate hot reload)."""
    with _CONFIG_CACHE_LOCK:
        _CONFIG_CACHE.clear()


_TOP_LEVEL_SECTIONS = {
    "llm",
    "agent",
    "verifier",
    "lean",
    "upload",
    "logging",
    "search",
    "knowledge",
    "math_news",
    "web",
    "mcp_servers",
}
# [knowledge] holds only the nested [knowledge.embedding] table; the flat
# KnowledgeConfig fields are mapped from it below.
_KNOWLEDGE_KEYS = {"embedding"}
_KNOWLEDGE_EMBEDDING_KEYS = {
    "enabled",
    "provider",
    "model",
    "api_key",
    "hybrid_search_top_k",
}

# Reviewer names ReActAgent knows how to build for the reviewer panel.
KNOWN_REVIEWERS = frozenset(
    {"critic", "formal", "knowledge", "fidelity", "completeness"}
)

# Built-in tool names registered by ToolRegistry (math_agent/tools/registry.py).
# config.py cannot import the registry (it imports config — circular), so
# this list mirrors ToolRegistry._register_builtins; unknown names are only
# skipped with a warning there, and are rejected here at load time.
KNOWN_BUILTIN_TOOLS = frozenset(
    {
        "compute",
        "search",
        "search_arxiv",
        "search_scholar",
        "fetch_url",
        "searching",
        "read_sources",
        "add_material",
        "search_materials",
        "search_knowledge",
        "relate_knowledge",
        "find_related",
        "search_mathlib",
        "plot_figure",
        "formalize",
        "lean_check",
        "tactic_search",
        "prove_by_lemmas",
    }
)


def _validate(config: Config) -> None:
    """Cross-field validation run on every loaded config.

    Raises ConfigError on violations; defaults must always pass.
    """

    def _require_positive(section: str, name: str, value: float) -> None:
        if value <= 0:
            raise ConfigError(
                f"{section}.{name} must be positive, got {value!r}"
            )

    _require_positive("llm", "timeout_seconds", config.llm.timeout_seconds)
    _require_positive(
        "llm", "max_calls_per_problem", config.llm.max_calls_per_problem
    )
    if config.llm.retry_max_attempts < 0:
        raise ConfigError(
            "llm.retry_max_attempts must be >= 0 (0 disables retries), "
            f"got {config.llm.retry_max_attempts!r}"
        )
    _require_positive("critic", "timeout_seconds", config.critic.timeout_seconds)
    _require_positive("agent", "max_react_steps", config.agent.max_react_steps)
    if config.agent.max_tool_calls is not None and config.agent.max_tool_calls <= 0:
        raise ConfigError(
            "agent.max_tool_calls must be a positive integer or None "
            f"(unlimited), got {config.agent.max_tool_calls!r}"
        )
    _require_positive("agent", "max_wall_seconds", config.agent.max_wall_seconds)
    _require_positive(
        "agent", "escalation_max_react_steps", config.agent.escalation_max_react_steps
    )
    _require_positive(
        "agent", "escalation_max_tool_calls", config.agent.escalation_max_tool_calls
    )
    _require_positive("search", "max_results", config.search.max_results)
    _require_positive(
        "lean", "build_timeout_seconds", config.lean.build_timeout_seconds
    )
    _require_positive(
        "lean", "update_timeout_seconds", config.lean.update_timeout_seconds
    )

    unknown_reviewers = sorted(set(config.agent.reviewers_enabled) - KNOWN_REVIEWERS)
    if unknown_reviewers:
        raise ConfigError(
            "agent.reviewers_enabled: unknown reviewer(s) "
            f"{', '.join(unknown_reviewers)}; known reviewers: "
            f"{', '.join(sorted(KNOWN_REVIEWERS))}"
        )

    for field_name, tools in (
        ("agent.tools", config.agent.tools),
        ("agent.hitl.approval_tools", config.agent.hitl.approval_tools),
        ("agent.hitl.auto_approve_tools", config.agent.hitl.auto_approve_tools),
    ):
        unknown_tools = sorted(set(tools) - KNOWN_BUILTIN_TOOLS)
        if unknown_tools:
            raise ConfigError(
                f"{field_name}: unknown tool(s) {', '.join(unknown_tools)}; "
                "built-in tools: " + ", ".join(sorted(KNOWN_BUILTIN_TOOLS))
            )

    if config.lean.enabled:
        for name in ("lake_path", "lean_path", "workspace_dir"):
            if not getattr(config.lean, name):
                raise ConfigError(
                    f"lean.{name} must be set when lean.enabled = true"
                )

    if config.verifier.formal_policy not in {
        "explicit",
        "all_theorems",
        "disabled",
    }:
        raise ConfigError(
            "verifier.formal_policy must be one of explicit, all_theorems, "
            f"disabled; got {config.verifier.formal_policy!r}"
        )
    if config.verifier.strictness not in {"standard", "high", "maximum"}:
        raise ConfigError(
            "verifier.strictness must be one of standard, high, maximum; "
            f"got {config.verifier.strictness!r}"
        )
    if config.logging.level.upper() not in {"DEBUG", "INFO", "WARNING", "ERROR"}:
        raise ConfigError(
            "logging.level must be one of DEBUG, INFO, WARNING, ERROR; "
            f"got {config.logging.level!r}"
        )
    if config.agent.easy_prompt_classifier not in {"critic", "rules"}:
        raise ConfigError(
            "agent.easy_prompt_classifier must be 'critic' or 'rules'; "
            f"got {config.agent.easy_prompt_classifier!r}"
        )
    if config.search.fallback_provider not in {"duckduckgo", "none"}:
        raise ConfigError(
            "search.fallback_provider must be 'duckduckgo' or 'none'; "
            f"got {config.search.fallback_provider!r}"
        )
    if config.web.state_backend not in {"memory", "redis"}:
        raise ConfigError(
            "web.state_backend must be 'memory' or 'redis'; "
            f"got {config.web.state_backend!r}"
        )
    if config.web.state_backend == "redis" and not config.web.redis_url:
        raise ConfigError(
            "web.redis_url must be set when web.state_backend = 'redis'"
        )


_SECRET_NAME_MARKERS = ("key", "token", "secret", "password")


def _redacted(value: Any, name: str = "") -> Any:
    if any(marker in name.lower() for marker in _SECRET_NAME_MARKERS):
        return "***" if value else value
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {
            f.name: _redacted(getattr(value, f.name), f.name)
            for f in dataclasses.fields(value)
        }
    if isinstance(value, dict):
        return {k: _redacted(v, str(k)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_redacted(item) for item in value]
    return value


def redacted_summary(config: Config) -> dict[str, Any]:
    """Effective config as a plain dict, with secrets masked as ``***``.

    Any field or mapping key containing key/token/secret/password (case
    insensitive) is redacted; empty values are left as-is.
    """
    return _redacted(config)


def _load_config_uncached(path: Path | None = None) -> Config:
    if path is None or not path.exists():
        config = Config()
        _apply_env_overrides(config)
        _validate(config)
        return config

    with open(path, "rb") as f:
        raw = tomllib.load(f)

    strict = _config_strict()
    _check_unknown_keys("<top-level>", raw, _TOP_LEVEL_SECTIONS, strict=strict)

    llm_data = _section(raw, "llm")
    _check_unknown_keys(
        "llm",
        llm_data,
        set(LLMConfig.__dataclass_fields__) | {"critic", "prover"},
        strict=strict,
    )
    critic_data = _section(llm_data, "critic")
    _check_unknown_keys(
        "llm.critic", critic_data, set(CriticConfig.__dataclass_fields__), strict=strict
    )
    prover_data = _section(llm_data, "prover")
    _check_unknown_keys(
        "llm.prover", prover_data, set(ProverConfig.__dataclass_fields__), strict=strict
    )

    agent_data = _section(raw, "agent")
    _check_unknown_keys(
        "agent",
        agent_data,
        set(AgentConfig.__dataclass_fields__),
        strict=strict,
        warn_legacy_research=True,
    )
    agent = _build_dataclass(AgentConfig, agent_data, "agent.")
    hitl_data = _section(agent_data, "hitl")
    _check_unknown_keys(
        "agent.hitl", hitl_data, set(HitlConfig.__dataclass_fields__), strict=strict
    )
    agent.hitl = _build_dataclass(HitlConfig, hitl_data, "agent.hitl.")

    knowledge_raw = _section(raw, "knowledge")
    _check_unknown_keys("knowledge", knowledge_raw, _KNOWLEDGE_KEYS, strict=strict)
    embedding_raw = _section(knowledge_raw, "embedding")
    _check_unknown_keys(
        "knowledge.embedding", embedding_raw, _KNOWLEDGE_EMBEDDING_KEYS, strict=strict
    )
    knowledge_data = {
        "embedding_enabled": embedding_raw.get("enabled", False),
        "embedding_provider": embedding_raw.get("provider", "openai"),
        "embedding_model": embedding_raw.get("model", "text-embedding-3-small"),
        "embedding_api_key": embedding_raw.get("api_key", ""),
        "hybrid_search_top_k": embedding_raw.get("hybrid_search_top_k", 20),
    }

    mcp_servers_raw = raw.get("mcp_servers")
    mcp_allowed = set(McpServerConfig.__dataclass_fields__)
    mcp_entries = (
        mcp_servers_raw
        if isinstance(mcp_servers_raw, list)
        else [mcp_servers_raw]
    )
    for entry in mcp_entries:
        if isinstance(entry, dict):
            _check_unknown_keys("mcp_servers", entry, mcp_allowed, strict=strict)

    sections: dict[str, tuple[type, dict[str, Any]]] = {}
    for name, cls in (
        ("verifier", VerifierConfig),
        ("lean", LeanConfig),
        ("upload", UploadConfig),
        ("logging", LoggingConfig),
        ("search", SearchConfig),
        ("math_news", MathNewsConfig),
        ("web", WebConfig),
    ):
        data = _section(raw, name)
        _check_unknown_keys(name, data, set(cls.__dataclass_fields__), strict=strict)
        sections[name] = (cls, data)

    config = Config(
        llm=_build_dataclass(LLMConfig, llm_data, "llm."),
        critic=_build_dataclass(CriticConfig, critic_data, "llm.critic."),
        prover=_build_dataclass(ProverConfig, prover_data, "llm.prover."),
        agent=agent,
        verifier=_build_dataclass(
            sections["verifier"][0], sections["verifier"][1], "verifier."
        ),
        lean=_build_dataclass(sections["lean"][0], sections["lean"][1], "lean."),
        upload=_build_dataclass(
            sections["upload"][0], sections["upload"][1], "upload."
        ),
        logging=_build_dataclass(
            sections["logging"][0], sections["logging"][1], "logging."
        ),
        search=_build_dataclass(
            sections["search"][0], sections["search"][1], "search."
        ),
        knowledge=_build_dataclass(KnowledgeConfig, knowledge_data, "knowledge."),
        math_news=_build_dataclass(
            sections["math_news"][0], sections["math_news"][1], "math_news."
        ),
        web=_build_dataclass(sections["web"][0], sections["web"][1], "web."),
        mcp_servers=parse_mcp_servers(mcp_servers_raw),
    )
    _apply_env_overrides(config)
    _validate(config)
    return config


def default_config() -> Config:
    """Return a fresh configuration built from hard-coded defaults.

    This is useful for tests and standalone scripts that need a deterministic
    baseline without reading ``config.toml`` or applying environment overrides.
    """
    return Config()


def _apply_env_overrides(config: Config) -> None:
    """Allow environment variables to override TOML config values."""
    if os.environ.get("CONJECTA_LLM_PROVIDER"):
        config.llm.provider = os.environ["CONJECTA_LLM_PROVIDER"]
    if os.environ.get("CONJECTA_LLM_MODEL"):
        config.llm.model = os.environ["CONJECTA_LLM_MODEL"]
    if os.environ.get("CONJECTA_LLM_API_KEY"):
        config.llm.api_key = os.environ["CONJECTA_LLM_API_KEY"]
    if os.environ.get("CONJECTA_LLM_BASE_URL"):
        config.llm.base_url = os.environ["CONJECTA_LLM_BASE_URL"]
    if os.environ.get("CONJECTA_LEAN_TOOLCHAIN"):
        config.lean.lean_toolchain = os.environ["CONJECTA_LEAN_TOOLCHAIN"]
    if os.environ.get("CONJECTA_LEAN_BUILD_TIMEOUT"):
        with suppress(ValueError):
            config.lean.build_timeout_seconds = int(
                os.environ["CONJECTA_LEAN_BUILD_TIMEOUT"]
            )
    if os.environ.get("CONJECTA_MATH_NEWS_REFRESH_SECONDS"):
        with suppress(ValueError):
            config.math_news.refresh_seconds = int(
                os.environ["CONJECTA_MATH_NEWS_REFRESH_SECONDS"]
            )
    if os.environ.get("CONJECTA_MATH_NEWS_MIN_INTERVAL_SECONDS"):
        with suppress(ValueError):
            config.math_news.min_interval_seconds = int(
                os.environ["CONJECTA_MATH_NEWS_MIN_INTERVAL_SECONDS"]
            )
    if os.environ.get("CONJECTA_ARTIFACT_ROOT"):
        config.agent.artifact_root = os.environ["CONJECTA_ARTIFACT_ROOT"]
