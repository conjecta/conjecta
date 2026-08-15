"""Smoke tests — no live API calls required."""

from __future__ import annotations

import pytest

from math_agent.config import load_config
from math_agent.llm.deepseek import DeepSeekBackend, _resolve_model
from math_agent.llm.factory import create_backend_from_model_string


def test_config_loads():
    config = load_config()
    assert config.llm.provider in ("openai", "deepseek", "shengsuanyun", "kimi")
    assert config.agent.max_react_steps > 0


def test_deepseek_requires_api_key():
    with pytest.raises(ValueError, match="DeepSeek API key required"):
        DeepSeekBackend(api_key=None)


def test_deepseek_v4_models_enable_thinking():
    model, thinking = _resolve_model("deepseek-v4-pro")
    assert model == "deepseek-v4-pro"
    assert thinking is True

    model, thinking = _resolve_model("deepseek-v4-flash")
    assert model == "deepseek-v4-flash"
    assert thinking is True


def test_deepseek_legacy_model_mapping():
    model, thinking = _resolve_model("deepseek-chat")
    assert model == "deepseek-chat"
    assert thinking is False

    model, thinking = _resolve_model("deepseek-reasoner")
    assert model == "deepseek-reasoner"
    assert thinking is True


def test_factory_deepseek_backend():
    backend = create_backend_from_model_string(
        "deepseek/deepseek-v4-pro",
        temperature=0.7,
        api_key="sk-test",
    )
    assert isinstance(backend, DeepSeekBackend)
    assert backend.model == "deepseek-v4-pro"


def test_factory_unknown_provider():
    with pytest.raises(ValueError, match="Unknown LLM provider"):
        create_backend_from_model_string("unknown/model", api_key="sk-test")
