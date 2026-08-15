from math_agent.config import LLMConfig
from math_agent.llm.factory import create_backend, create_backend_from_model_string


def test_shengsuanyun_provider_uses_router_and_passes_model_verbatim(monkeypatch):
    monkeypatch.setenv("SHENGSUANYUN_API_KEY", "test-key-123")
    backend = create_backend(LLMConfig(provider="shengsuanyun", model="openai/gpt-5.5", temperature=0.7))
    assert backend.model == "openai/gpt-5.5"          # prefix preserved, not stripped
    assert backend._base_url == "https://router.shengsuanyun.com/api/v1"
    assert backend._api_key == "test-key-123"


def test_shengsuanyun_from_model_string_splits_only_first_slash(monkeypatch):
    monkeypatch.setenv("SHENGSUANYUN_API_KEY", "test-key-123")
    backend = create_backend_from_model_string("shengsuanyun/openai/gpt-5.5", temperature=0.2)
    assert backend.model == "openai/gpt-5.5"
    assert backend._base_url == "https://router.shengsuanyun.com/api/v1"


def test_kimi_provider_defaults_to_moonshot_platform(monkeypatch):
    monkeypatch.setenv("KIMI_API_KEY", "test-key-456")
    monkeypatch.delenv("KIMI_BASE_URL", raising=False)
    backend = create_backend(LLMConfig(provider="kimi", model="k3", temperature=0.7))
    assert backend.model == "k3"
    assert backend._base_url == "https://api.moonshot.ai/v1"
    assert backend._api_key == "test-key-456"


def test_kimi_provider_honors_base_url_override(monkeypatch):
    monkeypatch.setenv("KIMI_API_KEY", "test-key-456")
    monkeypatch.setenv("KIMI_BASE_URL", "https://api.kimi.com/coding/v1")
    backend = create_backend(LLMConfig(provider="kimi", model="k3", temperature=0.7))
    assert backend._base_url == "https://api.kimi.com/coding/v1"
