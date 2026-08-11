from __future__ import annotations

import json
import logging
from typing import Any

from math_agent.agent.react_state import Action
from math_agent.llm.base import LLMBackend, Message

log = logging.getLogger("math_agent.agent.action_parser")

_REPAIR_SYSTEM = "You output only valid JSON."


class ActionParseError(Exception):
    pass


def parse_action(text: str) -> Action:
    data = _extract_json(text)
    if data is None:
        raise ActionParseError(f"Could not parse JSON from: {text[:200]}")
    return _action_from_dict(data)


def _extract_json(text: str) -> dict[str, Any] | None:
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _action_from_dict(data: dict[str, Any]) -> Action:
    if not isinstance(data, dict):
        raise ActionParseError(f"Expected JSON object, got {type(data).__name__}")
    action_data = data.get("action")
    if not isinstance(action_data, dict):
        raise ActionParseError(f"Missing 'action' object. Got: {action_data}")
    name = action_data.get("name")
    if not isinstance(name, str) or not name:
        raise ActionParseError(f"Missing or invalid action name: {name}")
    args = action_data.get("args", {})
    if not isinstance(args, dict):
        raise ActionParseError(f"Action args must be a dict, got {type(args).__name__}")
    return Action(name=name, args=args)


async def parse_action_with_repair(text: str, llm: LLMBackend) -> Action | None:
    try:
        return parse_action(text)
    except ActionParseError:
        pass
    repair_prompt = (
        "The previous response was not valid JSON or lacked the required "
        "'thought' and 'action' fields. Output ONLY a valid JSON object like:\n"
        '{"thought": "...", "action": {"name": "think", "args": {"text": "..."}}}\n\n'
        f"Previous response:\n{text}\n\n"
        "Now output valid JSON:"
    )
    try:
        response = await llm.complete(
            [Message(role="user", content=repair_prompt)],
            system=_REPAIR_SYSTEM,
            temperature=0.0,
        )
        return parse_action(response.text)
    except Exception:
        log.warning("Action JSON repair failed")
        return None
