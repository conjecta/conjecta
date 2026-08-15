from math_agent.web.app import _platform_api_key


def test_platform_api_key_resolves_provider_env(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "ds-key")
    monkeypatch.setenv("SHENGSUANYUN_API_KEY", "ssy-key")
    monkeypatch.setenv("OPENAI_API_KEY", "oai-key")
    monkeypatch.setenv("KIMI_API_KEY", "kimi-key")

    assert _platform_api_key("deepseek/deepseek-v4-pro") == "ds-key"
    assert _platform_api_key("shengsuanyun/openai/gpt-5.5") == "ssy-key"
    assert _platform_api_key("openai/gpt-5.5") == "oai-key"
    assert _platform_api_key("kimi/k3") == "kimi-key"


def test_platform_api_key_defaults_to_config_provider(monkeypatch):
    from types import SimpleNamespace

    from math_agent.web import agent_factory

    monkeypatch.setenv("SHENGSUANYUN_API_KEY", "ssy-key")
    monkeypatch.setattr(
        agent_factory,
        "load_config",
        lambda: SimpleNamespace(llm=SimpleNamespace(provider="shengsuanyun")),
    )
    assert _platform_api_key(None) == "ssy-key"
    assert _platform_api_key("") == "ssy-key"


def test_platform_api_key_unknown_provider_returns_none(monkeypatch):
    assert _platform_api_key("anthropic/claude") is None
