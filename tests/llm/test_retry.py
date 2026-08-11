from __future__ import annotations

from types import SimpleNamespace

import pytest

from math_agent.llm.retry import (
    call_with_retries,
    is_context_overflow_error,
    is_retryable_error,
    retry_delay,
    stream_with_retries,
)


def _http_error(status: int, message: str = "error"):
    import httpx
    import openai

    request = httpx.Request("POST", "https://api.test/v1/chat/completions")
    return openai.APIStatusError(
        message,
        response=httpx.Response(status, request=request),
        body=None,
    )


def _rate_limit_error():
    import httpx
    import openai

    request = httpx.Request("POST", "https://api.test/v1/chat/completions")
    return openai.RateLimitError(
        "Rate limit exceeded",
        response=httpx.Response(429, request=request),
        body=None,
    )


# --- is_retryable_error -----------------------------------------------------


def test_retryable_status_codes():
    assert is_retryable_error(_rate_limit_error())
    assert is_retryable_error(_http_error(500))
    assert is_retryable_error(_http_error(503))


def test_non_retryable_status_codes():
    assert not is_retryable_error(_http_error(400))
    assert not is_retryable_error(_http_error(401))
    assert not is_retryable_error(_http_error(404))


def test_retryable_connection_and_timeout_errors():
    import httpx
    import openai

    request = httpx.Request("POST", "https://api.test/v1/chat/completions")
    assert is_retryable_error(openai.APIConnectionError(request=request))
    assert is_retryable_error(openai.APITimeoutError(request=request))


def test_retryable_mirror_markers_without_status_code():
    assert is_retryable_error(Exception("Upstream service temporarily unavailable"))
    assert is_retryable_error(Exception("concurrency limit reached"))
    assert is_retryable_error(Exception("502 Bad Gateway"))


def test_non_retryable_plain_error():
    assert not is_retryable_error(ValueError("invalid argument"))


def test_context_overflow_is_not_retryable():
    exc = _http_error(400, "This model's maximum context length is 65536 tokens.")
    assert not is_retryable_error(exc)


# --- is_context_overflow_error ----------------------------------------------


def test_context_overflow_openai_wording():
    exc = Exception(
        "This model's maximum context length is 8192 tokens. However, your "
        "messages resulted in 9000 tokens. Please reduce the length of the messages."
    )
    assert is_context_overflow_error(exc)


def test_context_overflow_deepseek_wording():
    exc = Exception(
        "This model's maximum context length is 65536 tokens. However, you "
        "requested 70000 tokens (68000 in the messages, 2000 in the completion)."
    )
    assert is_context_overflow_error(exc)


def test_context_overflow_error_code():
    exc = SimpleNamespace(code="context_length_exceeded")
    assert is_context_overflow_error(exc)  # type: ignore[arg-type]


def test_context_overflow_other_markers():
    assert is_context_overflow_error(Exception("input is too long"))
    assert is_context_overflow_error(Exception("request too large for model"))
    assert is_context_overflow_error(Exception("exceeds the context window"))


def test_context_overflow_negative():
    assert not is_context_overflow_error(Exception("rate limit exceeded"))
    assert not is_context_overflow_error(_rate_limit_error())


# --- retry_delay ------------------------------------------------------------


def test_retry_delay_backoff_sequence_with_jitter():
    for attempt, base_multiple in ((0, 1), (1, 3), (2, 9)):
        for _ in range(20):
            delay = retry_delay(5.0, attempt)
            assert 5.0 * base_multiple * 0.9 <= delay <= 5.0 * base_multiple * 1.1


def test_retry_delay_zero_base_stays_zero():
    assert retry_delay(0.0, 3) == 0.0


# --- call_with_retries -------------------------------------------------------


@pytest.mark.asyncio
async def test_call_with_retries_recovers_from_transient_errors():
    calls = 0

    async def fn():
        nonlocal calls
        calls += 1
        if calls < 3:
            raise _rate_limit_error()
        return "ok"

    result = await call_with_retries(
        fn, max_attempts=3, base_seconds=0.0, description="test"
    )
    assert result == "ok"
    assert calls == 3


@pytest.mark.asyncio
async def test_call_with_retries_raises_after_exhausting_attempts():
    calls = 0

    async def fn():
        nonlocal calls
        calls += 1
        raise _rate_limit_error()

    with pytest.raises(Exception):
        await call_with_retries(fn, max_attempts=2, base_seconds=0.0, description="test")
    assert calls == 3


@pytest.mark.asyncio
async def test_call_with_retries_does_not_retry_client_errors():
    calls = 0

    async def fn():
        nonlocal calls
        calls += 1
        raise _http_error(400)

    with pytest.raises(Exception):
        await call_with_retries(fn, max_attempts=3, base_seconds=0.0, description="test")
    assert calls == 1


@pytest.mark.asyncio
async def test_call_with_retries_zero_attempts_disables_retry():
    calls = 0

    async def fn():
        nonlocal calls
        calls += 1
        raise _rate_limit_error()

    with pytest.raises(Exception):
        await call_with_retries(fn, max_attempts=0, base_seconds=0.0, description="test")
    assert calls == 1


# --- stream_with_retries -----------------------------------------------------


async def _aiter(items):
    for item in items:
        yield item


async def _iter_then_raise(items, exc):
    for item in items:
        yield item
    raise exc


@pytest.mark.asyncio
async def test_stream_with_retries_retries_before_first_chunk():
    attempts = 0

    def factory():
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return _iter_then_raise([], _rate_limit_error())
        return _aiter(["a", "b"])

    collected = [
        item
        async for item in stream_with_retries(
            factory, max_attempts=3, base_seconds=0.0, description="test"
        )
    ]
    assert collected == ["a", "b"]
    assert attempts == 2


@pytest.mark.asyncio
async def test_stream_with_retries_does_not_retry_after_content():
    attempts = 0

    def factory():
        nonlocal attempts
        attempts += 1
        return _iter_then_raise(["partial"], _rate_limit_error())

    collected = []
    with pytest.raises(Exception):
        async for item in stream_with_retries(
            factory, max_attempts=3, base_seconds=0.0, description="test"
        ):
            collected.append(item)
    assert collected == ["partial"]
    assert attempts == 1
