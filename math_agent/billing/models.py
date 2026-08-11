from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ToolCall:
    """One native function call returned by the model.

    ``arguments`` is the already-parsed JSON object from the provider's
    tool_call payload.
    """

    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class LLMResponse:
    text: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    # Mean token logprob for the completion when the provider returns logprobs;
    # used to skip optional reviewer panels on high-confidence concludes.
    mean_logprob: float | None = None
    # Native tool calls requested by the model. Only populated on responses
    # that complete a generation (``complete()`` and the final summary chunk
    # of ``stream()``), never on incremental stream deltas.
    tool_calls: tuple[ToolCall, ...] | None = None


@dataclass(frozen=True)
class UsageRecord:
    """In-memory usage record.

    ``total_tokens`` is informational only and is not persisted to the
    database. The ``increment_usage`` Postgres routine derives
    ``total_tokens`` from ``prompt_tokens + completion_tokens``.
    """

    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    cost_usd: float
    provider: str
    model: str


@dataclass(frozen=True)
class StoredApiKey:
    provider: str
    api_key: str
