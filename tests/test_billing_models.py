from math_agent.billing.models import LLMResponse, StoredApiKey, UsageRecord


def test_llm_response_dataclass() -> None:
    response = LLMResponse(
        text="hello", prompt_tokens=1, completion_tokens=2, total_tokens=3
    )
    assert response.text == "hello"
    assert response.prompt_tokens == 1
    assert response.completion_tokens == 2
    assert response.total_tokens == 3


def test_usage_record_dataclass() -> None:
    record = UsageRecord(
        prompt_tokens=10,
        completion_tokens=20,
        total_tokens=30,
        cost_usd=0.05,
        provider="openai",
        model="gpt-4",
    )
    assert record.prompt_tokens == 10
    assert record.completion_tokens == 20
    assert record.total_tokens == 30
    assert record.cost_usd == 0.05
    assert record.provider == "openai"
    assert record.model == "gpt-4"


def test_stored_api_key_dataclass() -> None:
    key = StoredApiKey(
        api_key="sk-secret", base_url="https://api.example.com/v1"
    )
    assert key.api_key == "sk-secret"
    assert key.base_url == "https://api.example.com/v1"
    assert key.legacy_provider == ""


def test_dataclasses_are_frozen() -> None:
    response = LLMResponse(
        text="hello", prompt_tokens=1, completion_tokens=2, total_tokens=3
    )
    try:
        response.text = "world"  # type: ignore[misc]
    except (AttributeError, TypeError):
        pass
    else:
        raise AssertionError("Expected frozen dataclass to be immutable")
