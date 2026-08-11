from math_agent.billing.models import StoredApiKey
from math_agent.config import LLMConfig
from math_agent.llm.factory import (
    create_backend,
    create_backend_for_user,
    create_backend_from_model_string,
)


def test_openai_provider_honors_configured_base_url(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-123")
    backend = create_backend(
        LLMConfig(
            provider="openai",
            model="gpt-5.6-sol",
            base_url="https://example.test/v1",
            temperature=0.7,
        )
    )
    assert backend.model == "gpt-5.6-sol"
    assert backend._base_url == "https://example.test/v1"
    assert backend._api_key == "test-key-123"


def test_model_string_honors_explicit_base_url(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-123")
    backend = create_backend_from_model_string(
        "openai/gpt-5.6-sol",
        temperature=0.2,
        base_url="https://example.test/v1",
    )
    assert backend.model == "gpt-5.6-sol"
    assert backend._base_url == "https://example.test/v1"


def test_user_key_backend_inherits_configured_base_url():
    backend = create_backend_for_user(
        LLMConfig(
            provider="openai",
            model="gpt-5.6-sol",
            base_url="https://example.test/v1",
        ),
        StoredApiKey(provider="openai", api_key="test-key-123"),
    )
    assert backend.model == "gpt-5.6-sol"
    assert backend._base_url == "https://example.test/v1"


def test_kimi_provider_defaults_to_moonshot_platform(monkeypatch):
    monkeypatch.setenv("KIMI_API_KEY", "test-key-456")
    monkeypatch.delenv("KIMI_BASE_URL", raising=False)
    backend = create_backend(
        LLMConfig(provider="kimi", model="kimi-k2.5", temperature=0.7)
    )
    assert backend.model == "kimi-k2.5"
    assert backend._base_url == "https://api.moonshot.ai/v1"
    assert backend._api_key == "test-key-456"


def test_kimi_provider_honors_base_url_override(monkeypatch):
    monkeypatch.setenv("KIMI_API_KEY", "test-key-456")
    monkeypatch.setenv("KIMI_BASE_URL", "https://api.kimi.com/coding/v1")
    backend = create_backend(
        LLMConfig(provider="kimi", model="kimi-k2.5", temperature=0.7)
    )
    assert backend._base_url == "https://api.kimi.com/coding/v1"
