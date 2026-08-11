import pytest

from math_agent.agent.action_parser import (
    ActionParseError,
    parse_action,
    parse_action_with_repair,
)
from math_agent.billing.models import LLMResponse


def test_parse_valid_json_action():
    text = '{"thought": "search", "action": {"name": "search_web", "args": {"query": "sqrt 2"}}}'
    action = parse_action(text)
    assert action.name == "search_web"
    assert action.args == {"query": "sqrt 2"}


def test_parse_missing_action_raises():
    with pytest.raises(ActionParseError):
        parse_action('{"thought": "only thought"}')


def test_parse_action_strips_markdown_code_fence():
    text = """```json
{
  "thought": "search",
  "action": {"name": "search_web", "args": {"query": "sqrt 2"}}
}
```"""
    action = parse_action(text)
    assert action.name == "search_web"
    assert action.args == {"query": "sqrt 2"}


def test_parse_action_raises_on_invalid_json():
    with pytest.raises(ActionParseError):
        parse_action("not json at all")


def test_parse_action_raises_on_invalid_name():
    with pytest.raises(ActionParseError):
        parse_action('{"thought": "x", "action": {"name": "", "args": {}}}')


def test_parse_action_raises_on_invalid_args():
    with pytest.raises(ActionParseError):
        parse_action('{"thought": "x", "action": {"name": "foo", "args": [1, 2]}}')


class _FakeLLM:
    def __init__(self, response: str):
        self._response = response

    async def complete(self, messages, system="", temperature=None, response_format=None):
        return LLMResponse(
            text=self._response,
            prompt_tokens=0,
            completion_tokens=0,
            total_tokens=0,
        )


@pytest.mark.asyncio
async def test_parse_action_with_repair_success():
    bad_text = "not json"
    repaired = '{"thought": "repair", "action": {"name": "think", "args": {"text": "ok"}}}'
    action = await parse_action_with_repair(bad_text, _FakeLLM(repaired))
    assert action is not None
    assert action.name == "think"
    assert action.args == {"text": "ok"}


@pytest.mark.asyncio
async def test_parse_action_with_repair_failure_returns_none():
    bad_text = "not json"
    action = await parse_action_with_repair(bad_text, _FakeLLM("still not json"))
    assert action is None
