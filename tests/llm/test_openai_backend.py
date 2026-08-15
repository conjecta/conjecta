from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from math_agent.billing.models import LLMResponse
from math_agent.llm.base import Message
from math_agent.llm.openai import OpenAICompatibleBackend


def _make_fake_response(content: str, *, prompt_tokens: int, completion_tokens: int) -> SimpleNamespace:
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
        usage=SimpleNamespace(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
        ),
    )


def _make_backend(monkeypatch, response) -> OpenAICompatibleBackend:
    import openai

    fake_client = MagicMock()
    fake_client.chat.completions.create = AsyncMock(return_value=response)
    monkeypatch.setattr(openai, "AsyncOpenAI", lambda **kwargs: fake_client)
    return OpenAICompatibleBackend(model="gpt-5.5", api_key="sk-test")


@pytest.mark.asyncio
async def test_complete_returns_llm_response_with_usage(monkeypatch):
    backend = _make_backend(
        monkeypatch,
        _make_fake_response("hello", prompt_tokens=10, completion_tokens=3),
    )
    result = await backend.complete([Message(role="user", content="hi")])
    assert isinstance(result, LLMResponse)
    assert result.text == "hello"
    assert result.prompt_tokens == 10
    assert result.completion_tokens == 3
    assert result.total_tokens == 13


@pytest.mark.asyncio
async def test_complete_falls_back_to_estimated_usage_when_response_lacks_usage(monkeypatch):
    backend = _make_backend(
        monkeypatch,
        SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="world"))],
            usage=None,
        ),
    )
    result = await backend.complete([Message(role="user", content="hi")], system="sys")
    assert isinstance(result, LLMResponse)
    assert result.text == "world"
    assert result.prompt_tokens > 0
    assert result.completion_tokens > 0
    assert result.total_tokens == result.prompt_tokens + result.completion_tokens


@pytest.mark.asyncio
async def test_stream_yields_text_chunks_then_final_usage_chunk(monkeypatch):
    chunks = [
        SimpleNamespace(
            choices=[SimpleNamespace(delta=SimpleNamespace(content="Hello, "))],
            usage=None,
        ),
        SimpleNamespace(
            choices=[SimpleNamespace(delta=SimpleNamespace(content="world!"))],
            usage=None,
        ),
        SimpleNamespace(choices=[], usage=SimpleNamespace(prompt_tokens=5, completion_tokens=4, total_tokens=9)),
    ]

    import openai

    fake_client = MagicMock()
    fake_client.chat.completions.create = AsyncMock(return_value=_async_iter(chunks))
    monkeypatch.setattr(openai, "AsyncOpenAI", lambda **kwargs: fake_client)
    backend = OpenAICompatibleBackend(model="gpt-5.5", api_key="sk-test")

    responses = [r async for r in backend.stream([Message(role="user", content="hi")])]

    assert all(isinstance(r, LLMResponse) for r in responses)
    assert responses[0].text == "Hello, "
    assert responses[0].total_tokens == 0
    assert responses[1].text == "world!"
    assert responses[1].total_tokens == 0
    assert responses[-1].text == ""
    assert responses[-1].prompt_tokens == 5
    assert responses[-1].completion_tokens == 4
    assert responses[-1].total_tokens == 9


@pytest.mark.asyncio
async def test_stream_falls_back_to_estimated_usage_when_no_final_usage(monkeypatch):
    import openai

    chunks = [
        SimpleNamespace(
            choices=[SimpleNamespace(delta=SimpleNamespace(content="abc"))],
            usage=None,
        ),
    ]

    fake_client = MagicMock()
    fake_client.chat.completions.create = AsyncMock(return_value=_async_iter(chunks))
    monkeypatch.setattr(openai, "AsyncOpenAI", lambda **kwargs: fake_client)
    backend = OpenAICompatibleBackend(model="gpt-5.5", api_key="sk-test")

    responses = [r async for r in backend.stream([Message(role="user", content="hi")])]

    assert responses[-1].text == ""
    assert responses[-1].prompt_tokens > 0
    assert responses[-1].completion_tokens > 0
    assert responses[-1].total_tokens == responses[-1].prompt_tokens + responses[-1].completion_tokens


@pytest.mark.asyncio
async def test_stream_sends_include_usage_option(monkeypatch):
    import openai

    captured_kwargs = {}

    async def _fake_create(**kwargs):
        captured_kwargs.update(kwargs)
        return _async_iter([])

    fake_client = MagicMock()
    fake_client.chat.completions.create = _fake_create
    monkeypatch.setattr(openai, "AsyncOpenAI", lambda **kwargs: fake_client)
    backend = OpenAICompatibleBackend(model="gpt-5.5", api_key="sk-test")

    async for _ in backend.stream([Message(role="user", content="hi")]):
        pass

    assert captured_kwargs.get("stream") is True
    assert captured_kwargs.get("stream_options") == {"include_usage": True}


async def _async_iter(items):
    for item in items:
        yield item


# --- Transient-error retry tests -------------------------------------------


def _rate_limit_error():
    import httpx
    import openai

    request = httpx.Request("POST", "https://api.test/v1/chat/completions")
    return openai.RateLimitError(
        "Rate limit exceeded",
        response=httpx.Response(429, request=request),
        body=None,
    )


def _mirror_api_error(message: str = "Upstream service temporarily unavailable"):
    """Bare APIError without a status code, as the AICodeMirror mirror emits."""
    import httpx
    import openai

    request = httpx.Request("POST", "https://api.test/v1/chat/completions")
    return openai.APIError(message, request, body=None)


def _bad_request_error():
    import httpx
    import openai

    request = httpx.Request("POST", "https://api.test/v1/chat/completions")
    return openai.BadRequestError(
        "invalid request",
        response=httpx.Response(400, request=request),
        body=None,
    )


def _make_retry_backend(monkeypatch, side_effects, *, retry_max_attempts: int = 3):
    import openai

    fake_client = MagicMock()
    fake_client.chat.completions.create = AsyncMock(side_effect=side_effects)
    monkeypatch.setattr(openai, "AsyncOpenAI", lambda **kwargs: fake_client)
    backend = OpenAICompatibleBackend(
        model="gpt-5.5",
        api_key="sk-test",
        retry_max_attempts=retry_max_attempts,
        retry_base_seconds=0.0,
    )
    return backend, fake_client.chat.completions.create


@pytest.mark.asyncio
async def test_complete_retries_transient_errors_and_records_usage_once(monkeypatch):
    response = _make_fake_response("recovered", prompt_tokens=7, completion_tokens=2)
    backend, create = _make_retry_backend(
        monkeypatch, [_rate_limit_error(), _rate_limit_error(), response]
    )
    record = AsyncMock()
    monkeypatch.setattr("math_agent.llm.openai.record_llm_usage", record)

    result = await backend.complete([Message(role="user", content="hi")])

    assert result.text == "recovered"
    assert result.total_tokens == 9
    assert create.await_count == 3
    assert record.await_count == 1


@pytest.mark.asyncio
async def test_complete_retries_mirror_api_error_message_variant(monkeypatch):
    response = _make_fake_response("ok", prompt_tokens=2, completion_tokens=1)
    backend, create = _make_retry_backend(
        monkeypatch, [_mirror_api_error(), response]
    )
    monkeypatch.setattr("math_agent.llm.openai.record_llm_usage", AsyncMock())

    result = await backend.complete([Message(role="user", content="hi")])

    assert result.text == "ok"
    assert create.await_count == 2


@pytest.mark.asyncio
async def test_complete_does_not_retry_client_errors(monkeypatch):
    import openai

    backend, create = _make_retry_backend(monkeypatch, [_bad_request_error()])

    with pytest.raises(openai.BadRequestError):
        await backend.complete([Message(role="user", content="hi")])
    assert create.await_count == 1


@pytest.mark.asyncio
async def test_complete_raises_original_error_after_retries_exhausted(monkeypatch):
    import openai

    last_error = _rate_limit_error()
    backend, create = _make_retry_backend(
        monkeypatch,
        [_rate_limit_error(), _rate_limit_error(), last_error],
        retry_max_attempts=2,
    )

    with pytest.raises(openai.RateLimitError) as exc_info:
        await backend.complete([Message(role="user", content="hi")])
    assert exc_info.value is last_error
    assert create.await_count == 3


@pytest.mark.asyncio
async def test_complete_does_not_retry_when_retry_max_attempts_is_zero(monkeypatch):
    import openai

    backend, create = _make_retry_backend(
        monkeypatch,
        [_rate_limit_error(), _make_fake_response("never", prompt_tokens=1, completion_tokens=1)],
        retry_max_attempts=0,
    )

    with pytest.raises(openai.RateLimitError):
        await backend.complete([Message(role="user", content="hi")])
    assert create.await_count == 1


@pytest.mark.asyncio
async def test_stream_retries_failure_before_first_chunk(monkeypatch):
    chunks = [
        SimpleNamespace(
            choices=[SimpleNamespace(delta=SimpleNamespace(content="ok"))],
            usage=None,
        ),
        SimpleNamespace(
            choices=[],
            usage=SimpleNamespace(prompt_tokens=3, completion_tokens=1, total_tokens=4),
        ),
    ]
    backend, create = _make_retry_backend(
        monkeypatch, [_rate_limit_error(), _async_iter(chunks)]
    )
    record = AsyncMock()
    monkeypatch.setattr("math_agent.llm.openai.record_llm_usage", record)

    responses = [r async for r in backend.stream([Message(role="user", content="hi")])]

    assert responses[0].text == "ok"
    assert responses[-1].total_tokens == 4
    assert create.await_count == 2
    assert record.await_count == 1


async def _iter_then_raise(items, exc):
    for item in items:
        yield item
    raise exc


@pytest.mark.asyncio
async def test_stream_does_not_retry_after_content_yielded(monkeypatch):
    import openai

    chunk = SimpleNamespace(
        choices=[SimpleNamespace(delta=SimpleNamespace(content="partial"))],
        usage=None,
    )
    backend, create = _make_retry_backend(
        monkeypatch,
        [_iter_then_raise([chunk], _rate_limit_error()), _async_iter([])],
    )
    monkeypatch.setattr("math_agent.llm.openai.record_llm_usage", AsyncMock())

    collected = []
    with pytest.raises(openai.RateLimitError):
        async for r in backend.stream([Message(role="user", content="hi")]):
            collected.append(r)

    assert [r.text for r in collected] == ["partial"]
    assert create.await_count == 1
