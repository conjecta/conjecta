"""Shared JSON parsing helpers for web API handlers."""
from __future__ import annotations

import json
import re
from typing import Any

from math_agent.llm.base import LLMBackend, Message


def parse_json_object(raw: str) -> dict[str, Any] | None:
    """Best-effort parse a JSON object, tolerating markdown fences and brace nesting."""
    text = (raw or "").strip()
    fence_match = re.search(r"```(?:json)?\s*\n(.*?)\n?```", text, re.DOTALL | re.IGNORECASE)
    if fence_match:
        text = fence_match.group(1).strip()
    if not text:
        return None
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else None
    except Exception:
        pass

    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    end = -1
    in_string = False
    escape = False
    for i, ch in enumerate(text[start:], start=start):
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end = i
                break
    if end == -1:
        return None
    try:
        data = json.loads(text[start : end + 1])
        return data if isinstance(data, dict) else None
    except Exception:
        return None


async def complete_json_object(
    llm: LLMBackend,
    *,
    user: str,
    system: str,
    temperature: float = 0.0,
) -> dict[str, Any] | None:
    """Run an LLM completion and parse the result as a JSON object."""
    try:
        response = await llm.complete([Message(role="user", content=user)], system=system, temperature=temperature)
        raw = response.text
    except Exception:
        return None
    plan = parse_json_object(raw)
    if plan is not None:
        return plan

    repair_prompt = (
        "Your previous response was not valid JSON. "
        "Output ONLY a single valid JSON object with the required keys, "
        "no markdown fences and no commentary.\n\n"
        f"Previous response:\n{raw}\n\n"
        "Now output valid JSON:"
    )
    try:
        response2 = await llm.complete(
            [Message(role="user", content=repair_prompt)],
            system="You output only valid JSON.",
            temperature=temperature,
        )
        return parse_json_object(response2.text)
    except Exception:
        return None
