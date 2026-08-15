"""Property-based tests for math_agent.agent.action_parser.parse_action.

The parser is a pure function over LLM output text; these properties assert
it never crashes with an unexpected exception and always returns a
well-formed Action when it succeeds.
"""
from __future__ import annotations

import json

from hypothesis import given, settings
from hypothesis import strategies as st

from math_agent.agent.action_parser import ActionParseError, parse_action
from math_agent.agent.react_state import Action

# JSON values that can appear inside action args.
_json_scalars = st.one_of(
    st.none(), st.booleans(), st.integers(), st.floats(allow_nan=False), st.text()
)
_json_values = st.recursive(
    _json_scalars,
    lambda children: st.one_of(
        st.lists(children, max_size=5),
        st.dictionaries(st.text(max_size=20), children, max_size=5),
    ),
    max_leaves=10,
)

valid_payloads = st.fixed_dictionaries(
    {
        "thought": st.text(),
        "action": st.fixed_dictionaries(
            {
                "name": st.text(min_size=1),
                "args": st.dictionaries(st.text(), _json_values, max_size=5),
            }
        ),
    }
)


@settings(max_examples=300)
@given(text=st.text())
def test_parse_action_never_raises_unexpected_exception(text: str):
    """Arbitrary text must raise ActionParseError or yield a valid Action."""
    try:
        action = parse_action(text)
    except ActionParseError:
        return
    assert isinstance(action, Action)
    assert isinstance(action.name, str)
    assert action.name
    assert isinstance(action.args, dict)


@settings(max_examples=200)
@given(payload=valid_payloads)
def test_parse_action_round_trips_valid_payloads(payload: dict):
    action = parse_action(json.dumps(payload))
    assert action.name == payload["action"]["name"]
    assert action.args == payload["action"]["args"]


@settings(max_examples=100)
@given(
    payload=valid_payloads,
    fence=st.sampled_from(["```", "```json", "```JSON"]),
)
def test_parse_action_tolerates_code_fences(payload: dict, fence: str):
    text = f"{fence}\n{json.dumps(payload, indent=2)}\n```"
    action = parse_action(text)
    assert action.name == payload["action"]["name"]
    assert action.args == payload["action"]["args"]
