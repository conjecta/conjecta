from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from math_agent.web import agent_factory
from math_agent.web.app import _platform_api_key


def test_platform_api_key_resolves_openai_env(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "oai-key")
    assert _platform_api_key("openai/gpt-5.6-sol") == "oai-key"


def test_platform_api_key_defaults_to_config_provider(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "oai-key")
    monkeypatch.setattr(
        agent_factory,
        "load_config",
        lambda: SimpleNamespace(
            llm=SimpleNamespace(
                provider="openai",
                base_url="https://example.test/v1",
            )
        ),
    )
    assert _platform_api_key(None) == "oai-key"
    assert _platform_api_key("") == "oai-key"
    assert agent_factory._platform_base_url(None) == "https://example.test/v1"
    assert (
        agent_factory._platform_base_url("openai/gpt-5.6-sol")
        == "https://example.test/v1"
    )


def test_unknown_provider_has_no_platform_credentials(monkeypatch):
    monkeypatch.setattr(
        agent_factory,
        "load_config",
        lambda: SimpleNamespace(
            llm=SimpleNamespace(
                provider="openai",
                base_url="https://example.test/v1",
            )
        ),
    )
    assert _platform_api_key("anthropic/claude") is None
    assert agent_factory._platform_base_url("anthropic/claude") is None


def test_public_platform_accepts_only_gpt_5_6_sol():
    assert (
        agent_factory._resolve_platform_model("openai/gpt-5.6-sol")
        == "openai/gpt-5.6-sol"
    )
    with pytest.raises(HTTPException, match="Invalid or unsupported model"):
        agent_factory._resolve_platform_model("openai/gpt-4o")


def test_self_hosted_allowlist_override(monkeypatch):
    monkeypatch.setenv(
        "CONJECTA_PLATFORM_MODEL_ALLOWLIST", "openai/gpt-4o,openai/gpt-5"
    )
    assert (
        agent_factory._resolve_platform_model("openai/gpt-4o") == "openai/gpt-4o"
    )
    assert (
        agent_factory._resolve_platform_model("openai/gpt-5") == "openai/gpt-5"
    )
    with pytest.raises(HTTPException, match="Invalid or unsupported model"):
        agent_factory._resolve_platform_model("openai/gpt-5.6-sol")
