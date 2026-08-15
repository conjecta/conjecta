import pytest

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
        "CONJECTA_LEAN_TOOLCHAIN",
        "CONJECTA_LEAN_BUILD_TIMEOUT",
        "CONJECTA_MATH_NEWS_REFRESH_SECONDS",
        "CONJECTA_MATH_NEWS_MIN_INTERVAL_SECONDS",
    ):
        monkeypatch.delenv(key, raising=False)

    cfg = load_config(tmp_path / "nonexistent.toml")
    assert cfg.llm.provider == "shengsuanyun"
    assert cfg.llm.model == "deepseek/deepseek-v4-pro"
    assert cfg.llm.timeout_seconds == 300.0
    assert cfg.critic.provider == "shengsuanyun"
    assert cfg.critic.model == "deepseek/deepseek-v4-pro"
    assert cfg.critic.timeout_seconds == 180.0
    assert cfg.lean.lean_toolchain == "leanprover/lean4:v4.30.0"
    assert cfg.lean.mathlib_rev == "v4.30.0"
    assert cfg.lean.build_timeout_seconds == 600


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
    assert cfg.provider == "deepseek"
    assert cfg.model == "deepseek-chat"
    assert cfg.refresh_seconds == 6 * 3600
    assert cfg.min_interval_seconds == 3600


def test_config_has_math_news_field():
    cfg = load_config()
    assert hasattr(cfg, "math_news")
    assert cfg.math_news.provider == "deepseek"
    assert cfg.math_news.model == "deepseek-chat"
    assert cfg.math_news.refresh_seconds == 6 * 3600
    assert cfg.math_news.min_interval_seconds == 3600


def test_load_config_reads_math_news(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(
        """
[math_news]
provider = "shengsuanyun"
model = "openai/gpt-4o-mini"
refresh_seconds = 7200
min_interval_seconds = 1800
""".strip(),
        encoding="utf-8",
    )

    cfg = load_config(path)
    assert cfg.math_news.provider == "shengsuanyun"
    assert cfg.math_news.model == "openai/gpt-4o-mini"
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


# ---------------------------------------------------------------------------
# Strict loading, max_tool_calls=None semantics, validation, redaction
# ---------------------------------------------------------------------------


def test_unknown_key_raises_in_strict_mode(tmp_path):
    from math_agent.config import ConfigError

    path = tmp_path / "config.toml"
    path.write_text("[agent]\nbogus_key = 1\nother_typo = 2\n", encoding="utf-8")

    with pytest.raises(ConfigError, match="bogus_key") as excinfo:
        load_config(path)
    assert "other_typo" in str(excinfo.value)
    assert "[agent]" in str(excinfo.value)


def test_unknown_key_warns_when_strict_disabled(tmp_path, monkeypatch, caplog):
    monkeypatch.setenv("CONJECTA_CONFIG_STRICT", "0")
    path = tmp_path / "config.toml"
    path.write_text("[agent]\nbogus_key = 1\n", encoding="utf-8")

    with caplog.at_level("WARNING", logger="math_agent.config"):
        cfg = load_config(path)

    assert cfg.agent.max_react_steps == 12
    assert any("bogus_key" in record.message for record in caplog.records)


def test_unknown_nested_and_top_level_keys_raise(tmp_path):
    from math_agent.config import ConfigError

    path = tmp_path / "hitl.toml"
    path.write_text("[agent.hitl]\nnope = true\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="agent.hitl"):
        load_config(path)

    path = tmp_path / "toplevel.toml"
    path.write_text("[made_up_section]\nfoo = 1\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="made_up_section"):
        load_config(path)

    path = tmp_path / "knowledge.toml"
    path.write_text("[knowledge]\nhybrid_search_top_k = 5\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="knowledge"):
        load_config(path)


def test_bad_type_raises_naming_key_and_value(tmp_path):
    from math_agent.config import ConfigError

    path = tmp_path / "config.toml"
    path.write_text('[agent]\nmax_react_steps = "abc"\n', encoding="utf-8")

    with pytest.raises(ConfigError) as excinfo:
        load_config(path)
    message = str(excinfo.value)
    assert "agent.max_react_steps" in message
    assert "int" in message
    assert "'abc'" in message


def test_bool_and_fractional_int_type_errors_raise(tmp_path):
    from math_agent.config import ConfigError

    path = tmp_path / "float_ok.toml"
    path.write_text("[agent]\nmax_wall_seconds = 8.5\n", encoding="utf-8")
    # 8.5 is a valid float; this must NOT raise.
    assert load_config(path).agent.max_wall_seconds == 8.5

    path = tmp_path / "int_truncation.toml"
    path.write_text("[agent]\nmax_react_steps = 8.5\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="max_react_steps"):
        load_config(path)

    path = tmp_path / "bad_bool.toml"
    path.write_text('[lean]\nenabled = "maybe"\n', encoding="utf-8")
    with pytest.raises(ConfigError, match="enabled"):
        load_config(path)


def test_removed_research_keys_warn_but_load_in_strict_mode(tmp_path, caplog):
    path = tmp_path / "config.toml"
    path.write_text(
        "[agent]\nresearch_foo = 1\nresearch_refutation_enabled = false\n",
        encoding="utf-8",
    )

    with caplog.at_level("WARNING", logger="math_agent.config"):
        cfg = load_config(path)

    assert cfg.agent.research_refutation_enabled is False
    messages = [record.message for record in caplog.records]
    assert any(
        "research_foo" in message and "removed" in message for message in messages
    )


def test_max_tool_calls_zero_warns_and_means_unlimited(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text("[agent]\nmax_tool_calls = 0\n", encoding="utf-8")

    with pytest.warns(DeprecationWarning, match="max_tool_calls"):
        cfg = load_config(path)

    assert cfg.agent.max_tool_calls is None


def test_max_tool_calls_positive_still_loads(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text("[agent]\nmax_tool_calls = 5\n", encoding="utf-8")

    cfg = load_config(path)
    assert cfg.agent.max_tool_calls == 5


def test_cross_field_validation_raises(tmp_path):
    from math_agent.config import ConfigError

    path = tmp_path / "reviewers.toml"
    path.write_text('[agent]\nreviewers_enabled = ["critic", "nope"]\n', encoding="utf-8")
    with pytest.raises(ConfigError, match="nope"):
        load_config(path)

    path = tmp_path / "tools.toml"
    path.write_text('[agent]\ntools = ["compute", "not_a_tool"]\n', encoding="utf-8")
    with pytest.raises(ConfigError, match="not_a_tool"):
        load_config(path)

    path = tmp_path / "wall.toml"
    path.write_text("[agent]\nmax_wall_seconds = -1\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="max_wall_seconds"):
        load_config(path)

    path = tmp_path / "lean.toml"
    path.write_text('[lean]\nenabled = true\nlake_path = ""\n', encoding="utf-8")
    with pytest.raises(ConfigError, match="lake_path"):
        load_config(path)

    path = tmp_path / "web.toml"
    path.write_text('[web]\nstate_backend = "redis"\n', encoding="utf-8")
    with pytest.raises(ConfigError, match="redis_url"):
        load_config(path)


def test_redacted_summary_masks_secrets():
    from math_agent.config import Config, redacted_summary

    cfg = Config()
    cfg.llm.api_key = "super-secret-key"
    cfg.knowledge.embedding_api_key = "another-secret"

    summary = redacted_summary(cfg)

    assert summary["llm"]["api_key"] == "***"
    assert summary["knowledge"]["embedding_api_key"] == "***"
    assert summary["llm"]["provider"] == cfg.llm.provider
    assert summary["agent"]["max_tool_calls"] == cfg.agent.max_tool_calls


def test_redacted_summary_masks_mcp_env_and_headers():
    from math_agent.config import Config, redacted_summary
    from math_agent.mcp_config import McpServerConfig

    cfg = Config(
        mcp_servers=[
            McpServerConfig(
                name="svc",
                env={"MCP_API_KEY": "shh", "PLAIN": "visible"},
                headers={"Authorization": "Bearer token"},
            )
        ]
    )

    summary = redacted_summary(cfg)
    server = summary["mcp_servers"][0]
    assert server["env"]["MCP_API_KEY"] == "***"
    assert server["env"]["PLAIN"] == "visible"
    # "Authorization" carries a token but does not match the marker list;
    # only key/token/secret/password names are masked.
    assert server["headers"]["Authorization"] == "Bearer token"


def test_committed_root_configs_load_under_strict_mode():
    """The committed TOML configs must pass strict loading unchanged."""
    from pathlib import Path

    from math_agent.config import clear_config_cache

    for name in (
        "config.toml",
        "config.example.toml",
        "config.eval.toml",
        "config.retry.toml",
    ):
        path = Path(name)
        if not path.exists():
            continue
        clear_config_cache()
        cfg = load_config(path)
        assert cfg.agent.max_tool_calls is None or cfg.agent.max_tool_calls > 0
