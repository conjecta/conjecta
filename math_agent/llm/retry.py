"""Shared transient-error classification and retry helpers for LLM backends.

Both the OpenAI-compatible and DeepSeek backends talk to OpenAI-style HTTP
APIs through the ``openai`` SDK, so they share the same retry semantics:
retry 429/5xx/connection/timeout errors (plus mirror-site message markers)
with exponential backoff ``base * 3**attempt`` and +-10% jitter, and never
retry client errors such as 400/401.
"""

from __future__ import annotations

import asyncio
import logging
import random
from typing import AsyncIterator, Awaitable, Callable, TypeVar

log = logging.getLogger("math_agent.llm.retry")

T = TypeVar("T")


class MalformedResponseError(Exception):
    """The gateway returned a 200 body that is not a ChatCompletion object.

    Some OpenAI-compatible mirrors intermittently answer 200 with a plain-text
    (or empty) body; the SDK then hands back a raw ``str`` instead of raising.
    That is a transient gateway fault, so it is retryable like a 5xx.
    """

# Message markers for mirrors (e.g. AICodeMirror) that surface transient
# failures as a bare APIError without a standard HTTP status code.
_TRANSIENT_ERROR_MARKERS = (
    "concurrency limit",
    "rate limit",
    "temporarily unavailable",
    "service unavailable",
    "bad gateway",
    "overloaded",
)

# Message/code markers for prompt-too-long failures (OpenAI and DeepSeek word
# these slightly differently). These are never retried by the backoff loop;
# upper layers (e.g. the ReAct loop) catch them to compact and retry.
_CONTEXT_OVERFLOW_MARKERS = (
    "context_length_exceeded",
    "context length",
    "context window",
    "maximum context",
    "max context",
    "too many tokens",
    "token limit",
    "request too large",
    "input is too long",
    "reduce the length",
)


def is_retryable_error(exc: Exception) -> bool:
    """Return True for transient errors worth retrying (429/5xx/connection).

    Client errors such as 400/401 are never retried.
    """
    import openai

    if isinstance(exc, (openai.APIConnectionError, openai.APITimeoutError)):
        return True
    if isinstance(exc, MalformedResponseError):
        return True
    status = getattr(exc, "status_code", None)
    if isinstance(status, int):
        return status == 429 or 500 <= status < 600
    message = str(exc).lower()
    return any(marker in message for marker in _TRANSIENT_ERROR_MARKERS)


def is_context_overflow_error(exc: Exception) -> bool:
    """Return True when the error reports the prompt exceeds the context limit.

    Matches the common OpenAI/DeepSeek wordings, e.g. "This model's maximum
    context length is 65536 tokens" or code "context_length_exceeded".
    """
    code = getattr(exc, "code", None)
    haystacks = [str(exc).lower()]
    if isinstance(code, str):
        haystacks.append(code.lower())
    return any(
        marker in haystack for haystack in haystacks for marker in _CONTEXT_OVERFLOW_MARKERS
    )


def retry_delay(base_seconds: float, attempt: int) -> float:
    """Exponential backoff (base, 3x, 9x, ...) with +-10% jitter."""
    delay = base_seconds * (3**attempt)
    if delay > 0:
        delay *= random.uniform(0.9, 1.1)
    return delay


async def call_with_retries(
    fn: Callable[[], Awaitable[T]],
    *,
    max_attempts: int,
    base_seconds: float,
    description: str,
) -> T:
    """Await ``fn``, retrying transient errors up to ``max_attempts`` times."""
    for attempt in range(max_attempts + 1):
        try:
            return await fn()
        except Exception as exc:
            if not is_retryable_error(exc) or attempt >= max_attempts:
                raise
            delay = retry_delay(base_seconds, attempt)
            log.warning(
                "Transient LLM error, retrying %s in %.1fs (attempt %d/%d): %s",
                description,
                delay,
                attempt + 1,
                max_attempts + 1,
                exc,
            )
            await asyncio.sleep(delay)
    raise AssertionError("unreachable")  # pragma: no cover


async def stream_with_retries(
    factory: Callable[[], AsyncIterator[T]],
    *,
    max_attempts: int,
    base_seconds: float,
    description: str,
) -> AsyncIterator[T]:
    """Iterate ``factory()``, retrying only while nothing has been yielded.

    Once content has been emitted to the caller, a mid-stream failure must
    propagate because retrying would duplicate content.
    """
    for attempt in range(max_attempts + 1):
        produced = False
        try:
            async for item in factory():
                produced = True
                yield item
            return
        except Exception as exc:
            if produced or not is_retryable_error(exc) or attempt >= max_attempts:
                raise
            delay = retry_delay(base_seconds, attempt)
            log.warning(
                "Transient LLM error, retrying %s in %.1fs (attempt %d/%d): %s",
                description,
                delay,
                attempt + 1,
                max_attempts + 1,
                exc,
            )
            await asyncio.sleep(delay)
