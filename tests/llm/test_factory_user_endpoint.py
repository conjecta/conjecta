import pytest

from math_agent.billing.models import StoredApiKey
from math_agent.config import LLMConfig, ProverConfig
from math_agent.llm.factory import create_backend_for_user


def test_user_endpoint_uses_supplied_url_and_key_with_fixed_model():
    backend = create_backend_for_user(
        LLMConfig(
            provider="deepseek",
            model="ignored",
            temperature=0.25,
            timeout_seconds=42,
            retry_max_attempts=4,
            retry_base_seconds=1.5,
            max_calls_per_problem=17,
        ),
        StoredApiKey(
            api_key="sk-user",
            base_url="https://gateway.example.com/openai/v1",
        ),
    )

    assert backend.model == "gpt-5.6-sol"
    assert backend.provider_name == "openai"
    assert backend._base_url == "https://gateway.example.com/openai/v1"
    assert backend._api_key == "sk-user"
    assert backend.default_temperature == 0.25
    assert backend._timeout_seconds == 42
    assert backend._retry_max_attempts == 4
    assert backend._retry_base_seconds == 1.5
    assert backend._follow_redirects is False
    assert backend.max_calls_per_problem == 17


def test_user_endpoint_rejects_legacy_record_without_base_url():
    with pytest.raises(ValueError, match="Base URL"):
        create_backend_for_user(
            LLMConfig(),
            StoredApiKey(api_key="sk-old", legacy_provider="openai"),
        )


def test_enabled_prover_role_uses_same_fixed_user_endpoint():
    backend = create_backend_for_user(
        ProverConfig(
            provider="openai",
            model="server-prover-model",
            base_url="https://server-prover.example/v1",
        ),
        StoredApiKey(
            api_key="sk-user",
            base_url="https://gateway.example.com/openai/v1",
        ),
    )

    assert backend.model == "gpt-5.6-sol"
    assert backend._base_url == "https://gateway.example.com/openai/v1"
    assert backend._api_key == "sk-user"
