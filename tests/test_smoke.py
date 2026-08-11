"""Smoke tests — no live API calls required."""

from __future__ import annotations

import pytest

from math_agent.config import load_config
from math_agent.llm.deepseek import DeepSeekBackend, _resolve_model
from math_agent.llm.factory import create_backend_from_model_string
from math_agent.llm.openai import OpenAICompatibleBackend


def test_config_loads():
    config = load_config()
    assert config.llm.provider == "openai"
    assert config.llm.model == "gpt-5.6-sol"
    assert config.agent.max_react_steps > 0


def test_deepseek_requires_api_key():
    with pytest.raises(ValueError, match="DeepSeek API key required"):
        DeepSeekBackend(api_key=None)


def test_deepseek_public_model_mapping():
    model, thinking = _resolve_model("deepseek-chat")
    assert model == "deepseek-chat"
    assert thinking is False

    model, thinking = _resolve_model("deepseek-reasoner")
    assert model == "deepseek-reasoner"
    assert thinking is True


def test_factory_openai_compatible_backend():
    backend = create_backend_from_model_string(
        "openai/gpt-5.6-sol",
        temperature=0.7,
        api_key="sk-test",
        base_url="https://example.test/v1",
    )
    assert isinstance(backend, OpenAICompatibleBackend)
    assert backend.model == "gpt-5.6-sol"
    assert backend._base_url == "https://example.test/v1"


def test_factory_unknown_provider():
    with pytest.raises(ValueError, match="Unknown LLM provider"):
        create_backend_from_model_string("unknown/model", api_key="sk-test")
