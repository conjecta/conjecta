from math_agent.config import (
    AgentConfig,
    LeanConfig,
    MathNewsConfig,
    VerifierConfig,
    load_config,
)


def test_fallback_defaults_match_committed_config(tmp_path, monkeypatch):
    """If config.toml is absent, fallback defaults must match the committed config.toml."""
    # Ensure no env overrides leak in.
    for key in (
        "CONJECTA_LLM_PROVIDER",
        "CONJECTA_LLM_MODEL",
        "CONJECTA_LLM_BASE_URL",
        "CONJECTA_LEAN_TOOLCHAIN",
        "CONJECTA_LEAN_BUILD_TIMEOUT",
        "CONJECTA_MATH_NEWS_REFRESH_SECONDS",
        "CONJECTA_MATH_NEWS_MIN_INTERVAL_SECONDS",
    ):
        monkeypatch.delenv(key, raising=False)

    cfg = load_config(tmp_path / "nonexistent.toml")
    assert cfg.llm.provider == "openai"
    assert cfg.llm.model == "gpt-5.6-sol"
    assert cfg.llm.timeout_seconds == 300.0
    assert cfg.critic.provider == "openai"
    assert cfg.critic.model == "gpt-5.6-sol"
    assert cfg.critic.timeout_seconds == 180.0
    assert cfg.lean.lean_toolchain == "leanprover/lean4:v4.30.0"
    assert cfg.lean.mathlib_rev == "v4.30.0"
    assert cfg.lean.build_timeout_seconds == 600
    assert cfg.lean.enabled is False


def test_agent_config_has_react_fields():
    cfg = AgentConfig()
    assert hasattr(cfg, "max_react_steps")
    assert hasattr(cfg, "reviewers_enabled")
    assert "critic" in cfg.reviewers_enabled
    assert cfg.max_react_steps == 12
    assert cfg.max_tool_calls == 8
    assert cfg.max_wall_seconds == 600.0
    assert cfg.max_identical_action_repeats == 2
    assert cfg.reviewers_enabled == ["critic", "fidelity", "completeness"]
    assert "formalize" in cfg.tools
    assert "lean_check" in cfg.tools
    assert "search_mathlib" in cfg.tools
    assert "tactic_search" in cfg.tools
    assert cfg.normal_claim_check_enabled is False
    assert cfg.normal_force_review is False
    assert cfg.normal_claim_check_max_tool_calls == 1


def test_load_config_enables_normal_claim_check_from_toml():
    cfg = load_config()
    assert cfg.agent.normal_claim_check_enabled is True
    assert cfg.agent.normal_force_review is True
    assert cfg.agent.normal_claim_check_max_tool_calls == 1


def test_agent_config_has_memory_consolidation_fields():
    cfg = AgentConfig()
    assert hasattr(cfg, "memory_consolidation_enabled")
    assert hasattr(cfg, "memory_consolidation_model")
    assert cfg.memory_consolidation_enabled is True
    assert cfg.memory_consolidation_model is None


def test_agent_config_has_scheduler_fields():
    cfg = AgentConfig()
    assert cfg.max_scheduler_iterations == 100
    assert cfg.max_retries_per_stage == 3
    assert cfg.artifact_root == "logs/artifacts"


def test_agent_config_has_tactic_search_fields():
    cfg = AgentConfig()
    assert cfg.tactic_search_max_attempts == 64
    assert cfg.tactic_search_max_depth == 12
    assert cfg.tactic_search_wall_seconds == 180.0


def test_nested_hitl_config_is_loaded(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(
        """
[agent.hitl]
enabled = true
mode = "adaptive"
approval_tools = ["add_material"]
max_interrupts_per_run = 2
""".strip(),
        encoding="utf-8",
    )

    cfg = load_config(path)

    assert cfg.agent.hitl.enabled is True
    assert cfg.agent.hitl.mode == "adaptive"
    assert cfg.agent.hitl.approval_tools == ["add_material"]
    assert cfg.agent.hitl.max_interrupts_per_run == 2


def test_hitl_auto_resolve_seconds_default_and_toml(tmp_path):
    from math_agent.config import HitlConfig

    assert HitlConfig().auto_resolve_seconds == 600.0

    path = tmp_path / "config.toml"
    path.write_text(
        """
[agent.hitl]
auto_resolve_seconds = 0
""".strip(),
        encoding="utf-8",
    )

    cfg = load_config(path)

    assert cfg.agent.hitl.auto_resolve_seconds == 0.0


def test_lean_config_has_premise_index_enabled():
    cfg = LeanConfig()
    assert hasattr(cfg, "premise_index_enabled")
    assert cfg.premise_index_enabled is True


def test_load_config_reads_premise_index_enabled(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(
        """
[lean]
premise_index_enabled = false
""".strip(),
        encoding="utf-8",
    )

    cfg = load_config(path)
    assert cfg.lean.premise_index_enabled is False


def test_lean_config_has_safe_shared_runtime_defaults():
    cfg = LeanConfig()

    # Performance-first defaults: parallel Lean checks are capped, not serial.
    assert cfg.max_concurrent_checks == 4
    assert cfg.result_cache_size == 256
    assert cfg.reject_unsafe_source is True


def test_verifier_defaults_to_explicit_formal_policy():
    cfg = VerifierConfig()

    assert cfg.formal_policy == "explicit"
    assert cfg.require_lean_for_theorems is False
    assert cfg.prefer_lean is False


def test_math_news_config_defaults():
    cfg = MathNewsConfig()
    assert cfg.provider == "openai"
    assert cfg.model == "gpt-5.6-sol"
    assert cfg.refresh_seconds == 6 * 3600
    assert cfg.min_interval_seconds == 3600


def test_config_has_math_news_field():
    cfg = load_config()
    assert hasattr(cfg, "math_news")
    assert cfg.math_news.provider == "openai"
    assert cfg.math_news.model == "gpt-5.6-sol"
    assert cfg.math_news.refresh_seconds == 6 * 3600
    assert cfg.math_news.min_interval_seconds == 3600


def test_load_config_reads_math_news(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(
        """
[math_news]
provider = "openai"
model = "gpt-5.6-sol"
refresh_seconds = 7200
min_interval_seconds = 1800
""".strip(),
        encoding="utf-8",
    )

    cfg = load_config(path)
    assert cfg.math_news.provider == "openai"
    assert cfg.math_news.model == "gpt-5.6-sol"
    assert cfg.math_news.refresh_seconds == 7200
    assert cfg.math_news.min_interval_seconds == 1800


def test_math_news_env_overrides(tmp_path, monkeypatch):
    path = tmp_path / "config.toml"
    path.write_text("[math_news]\n", encoding="utf-8")
    monkeypatch.setenv("CONJECTA_MATH_NEWS_REFRESH_SECONDS", "1234")
    monkeypatch.setenv("CONJECTA_MATH_NEWS_MIN_INTERVAL_SECONDS", "567")

    cfg = load_config(path)
    assert cfg.math_news.refresh_seconds == 1234
    assert cfg.math_news.min_interval_seconds == 567


def test_critic_inherits_main_base_url(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(
        """
[llm]
provider = "openai"
model = "gpt-5.6-sol"
base_url = "https://example.test/v1"

[llm.critic]
provider = "openai"
model = "gpt-5.6-sol"
""".strip(),
        encoding="utf-8",
    )

    cfg = load_config(path)
    assert cfg.critic.base_url == "https://example.test/v1"


def test_math_news_env_overrides_ignore_invalid(tmp_path, monkeypatch):
    path = tmp_path / "config.toml"
    path.write_text("[math_news]\n", encoding="utf-8")
    monkeypatch.setenv("CONJECTA_MATH_NEWS_REFRESH_SECONDS", "not-a-number")
    monkeypatch.setenv("CONJECTA_MATH_NEWS_MIN_INTERVAL_SECONDS", "also-bad")

    cfg = load_config(path)
    assert cfg.math_news.refresh_seconds == 6 * 3600
    assert cfg.math_news.min_interval_seconds == 3600


def test_new_budget_and_routing_keys_have_expected_defaults():
    from math_agent.config import LLMConfig

    llm = LLMConfig()
    assert llm.max_calls_per_problem == 200

    agent = AgentConfig()
    assert agent.search_mathlib_max_calls == 3
    assert agent.clarify_max_steps == 4
    assert agent.deep_search_wall_seconds == 3600.0
    assert agent.deep_search_max_attempts == 200

    lean = LeanConfig()
    assert lean.lemma_hook_max_attempts == 12
    assert lean.lemma_hook_max_depth == 6
    assert lean.lemma_rescue_max_depth == 2
    assert lean.lemma_route_temperatures == [0.0, 0.5, 0.9]
    assert lean.lemma_difficulty_threshold == 4
    assert lean.lemma_max_routes_hard == 5
    assert lean.max_check_chars == 8000
    assert lean.lemma_executor_wall_seconds == 240.0
    assert lean.repl_max_sessions == 4


def test_load_config_reads_new_keys(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(
        """
[llm]
max_calls_per_problem = 42

[agent]
search_mathlib_max_calls = 5
clarify_max_steps = 2
deep_search_wall_seconds = 900.0
deep_search_max_attempts = 50

[lean]
lemma_hook_max_attempts = 7
lemma_hook_max_depth = 3
lemma_rescue_max_depth = 1
lemma_route_temperatures = [0.1, 0.9]
lemma_difficulty_threshold = 3
lemma_max_routes_hard = 8
max_check_chars = 4000
lemma_executor_wall_seconds = 1800
repl_max_sessions = 8
""".strip(),
        encoding="utf-8",
    )

    cfg = load_config(path)

    assert cfg.llm.max_calls_per_problem == 42
    assert cfg.agent.search_mathlib_max_calls == 5
    assert cfg.agent.clarify_max_steps == 2
    assert cfg.agent.deep_search_wall_seconds == 900.0
    assert cfg.agent.deep_search_max_attempts == 50
    assert cfg.lean.lemma_hook_max_attempts == 7
    assert cfg.lean.lemma_hook_max_depth == 3
    assert cfg.lean.lemma_rescue_max_depth == 1
    # List elements keep their TOML numeric type (no stringification).
    assert cfg.lean.lemma_route_temperatures == [0.1, 0.9]
    assert cfg.lean.lemma_difficulty_threshold == 3
    assert cfg.lean.lemma_max_routes_hard == 8
    assert cfg.lean.max_check_chars == 4000
    assert cfg.lean.lemma_executor_wall_seconds == 1800.0
    assert cfg.lean.repl_max_sessions == 8
