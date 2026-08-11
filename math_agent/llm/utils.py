from __future__ import annotations

import json
import logging
import math
from typing import Any

import tiktoken

from math_agent.billing.models import ToolCall

log = logging.getLogger("math_agent.llm.utils")


def _content_to_text(content: Any) -> str:
    """Convert a message content value to plain text for token estimation.

    Supports string content and multimodal list content (e.g. OpenAI vision
    messages containing dicts with a ``text`` key). Other item types are
    serialized with ``str()``.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                text = item.get("text", "")
                if isinstance(text, str):
                    parts.append(text)
                else:
                    parts.append(str(text))
            else:
                parts.append(str(item))
        return "\n".join(parts)
    return str(content)


def _estimate_tokens(text: str, model: str = "gpt-5.5") -> int:
    """Estimate token count for ``text`` using tiktoken when possible.

    Falls back to a rough character heuristic (one token per four characters)
    when tiktoken does not recognize the model.
    """
    try:
        enc = tiktoken.encoding_for_model(model)
        return len(enc.encode(text))
    except Exception:
        # Conservative fallback: CJK and other non-Latin scripts often need
        # ~1-2 characters per token, so //2 avoids systematic under-billing.
        return max(1, len(text) // 2)


def estimate_prompt_tokens(api_messages: list[dict[str, Any]], model: str) -> int:
    """Estimate prompt tokens from a list of API-formatted messages."""
    prompt_text = "\n".join(_content_to_text(m.get("content", "")) for m in api_messages)
    return _estimate_tokens(prompt_text, model)


def token_logprobs_from_choice(choice: Any) -> list[float]:
    """Extract per-token logprobs from an OpenAI-compatible chat choice."""
    logprobs = getattr(choice, "logprobs", None)
    if logprobs is None:
        return []
    content = getattr(logprobs, "content", None)
    if not isinstance(content, list):
        return []
    values: list[float] = []
    for item in content:
        lp = getattr(item, "logprob", None)
        if isinstance(lp, (int, float)):
            values.append(float(lp))
    return values


def mean_logprob(values: list[float]) -> float | None:
    if not values:
        return None
    return sum(values) / len(values)


def _parse_tool_arguments(raw: Any) -> dict[str, Any]:
    """Parse a tool_call ``arguments`` payload (JSON string) into a dict."""
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        log.warning("Could not parse tool_call arguments: %.200s", raw)
        return {}
    if not isinstance(parsed, dict):
        log.warning("Tool_call arguments are not a JSON object: %.200s", raw)
        return {}
    return parsed


def tool_calls_from_message(message: Any) -> tuple[ToolCall, ...] | None:
    """Parse native tool calls from a non-streaming chat completion message."""
    raw = getattr(message, "tool_calls", None)
    if not raw:
        return None
    calls: list[ToolCall] = []
    for item in raw:
        function = getattr(item, "function", None)
        name = getattr(function, "name", None) or ""
        calls.append(
            ToolCall(
                name=name,
                arguments=_parse_tool_arguments(getattr(function, "arguments", None)),
            )
        )
    return tuple(calls)


class ToolCallAccumulator:
    """Accumulate streamed tool_call deltas into complete ToolCall objects.

    Deltas arrive keyed by ``index``; one index may be split across many
    chunks (the name typically arrives first, arguments are appended piece by
    piece), and indexes may appear out of order.
    """

    def __init__(self) -> None:
        self._slots: dict[int, dict[str, str]] = {}

    def add_delta(self, delta: Any) -> None:
        raw = getattr(delta, "tool_calls", None)
        if not raw:
            return
        for item in raw:
            index = getattr(item, "index", None)
            if not isinstance(index, int):
                index = len(self._slots)
            slot = self._slots.setdefault(index, {"name": "", "arguments": ""})
            function = getattr(item, "function", None)
            if function is None:
                continue
            name = getattr(function, "name", None)
            if name:
                slot["name"] += name
            arguments = getattr(function, "arguments", None)
            if arguments:
                slot["arguments"] += arguments

    def tool_calls(self) -> tuple[ToolCall, ...] | None:
        if not self._slots:
            return None
        return tuple(
            ToolCall(
                name=slot["name"],
                arguments=_parse_tool_arguments(slot["arguments"]),
            )
            for _, slot in sorted(self._slots.items())
        )


def confidence_from_mean_logprob(mean_lp: float | None) -> float | None:
    """Convert mean token logprob to a geometric-mean probability in (0, 1]."""
    if mean_lp is None:
        return None
    return float(math.exp(max(-50.0, min(0.0, float(mean_lp)))))
