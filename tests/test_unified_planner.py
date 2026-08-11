"""Unit tests for the unified (informal + formalization) planner."""
from __future__ import annotations

import json

import pytest

from math_agent.agent.planner import UnifiedPlanner, _render_informal_plan
from math_agent.billing.models import LLMResponse


class FakeLLM:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    async def complete(self, messages, system=None, temperature=None, response_format=None):
        text = self.responses[self.calls]
        self.calls += 1
        if isinstance(text, Exception):
            raise text
        return LLMResponse(text=text, prompt_tokens=0, completion_tokens=0, total_tokens=0)


def _payload(**overrides):
    data = {
        "informal": {
            "strategy": "Use induction on n.",
            "steps": ["prove base case", "prove inductive step"],
            "watch_outs": ["n = 0 edge case"],
            "done_when": "closed-form equality stated",
        },
        "formalization": {
            "restatement": "Sum of first n odd numbers equals n^2",
            "goal_type": "∀ (n : ℕ), ∑ i ∈ Finset.range n, (2 * i + 1) = n ^ 2",
            "recommended_imports": ["Mathlib.Data.Nat.Basic"],
            "lemmas": [],
            "proof_strategy": "induction",
        },
    }
    data.update(overrides)
    return json.dumps(data)


def test_render_informal_plan_blocks():
    text = _render_informal_plan(
        {
            "strategy": "s",
            "steps": ["a", "b"],
            "watch_outs": ["w"],
            "done_when": "d",
        }
    )
    assert text == "STRATEGY: s\nSTEPS:\n1. a\n2. b\nWATCH-OUTS:\n- w\nDONE-WHEN: d"


def test_render_informal_plan_accepts_plain_string():
    assert _render_informal_plan("  just text  ") == "just text"


@pytest.mark.asyncio
async def test_valid_json_produces_both_parts():
    llm = FakeLLM([_payload()])
    planner = UnifiedPlanner(llm)
    plan = await planner.plan("Prove the odd-sum identity.")
    assert llm.calls == 1
    assert plan is not None
    assert plan.plan_text.startswith("STRATEGY: Use induction on n.")
    assert "1. prove base case" in plan.plan_text
    assert "DONE-WHEN: closed-form equality stated" in plan.plan_text
    formal = plan.formalization
    assert formal.restatement == "Sum of first n odd numbers equals n^2"
    assert "∑" in formal.goal_type
    assert formal.recommended_imports == ["Mathlib.Data.Nat.Basic"]


@pytest.mark.asyncio
async def test_invalid_json_repairs_once():
    llm = FakeLLM(["not json at all", _payload()])
    planner = UnifiedPlanner(llm)
    plan = await planner.plan("Prove the odd-sum identity.")
    assert llm.calls == 2
    assert plan is not None
    assert plan.plan_text.startswith("STRATEGY:")


@pytest.mark.asyncio
async def test_double_invalid_json_degrades_to_none():
    llm = FakeLLM(["garbage", "still garbage"])
    planner = UnifiedPlanner(llm)
    assert await planner.plan("Prove something.") is None
    assert llm.calls == 2


@pytest.mark.asyncio
async def test_llm_exception_propagates_for_caller_fallback():
    llm = FakeLLM([RuntimeError("boom")])
    planner = UnifiedPlanner(llm)
    with pytest.raises(RuntimeError):
        await planner.plan("Prove something.")


class _FakeSearch:
    """Minimal MathlibSearch stand-in: no theorems exist, root has no files."""

    def __init__(self, root):
        self.root = root

    def search_by_name(self, name, max_results=3):
        return []


@pytest.mark.asyncio
async def test_mathlib_validation_applies_to_formalization_part(tmp_path):
    payload = _payload()
    data = json.loads(payload)
    data["formalization"]["recommended_theorem"] = "made_up_theorem"
    data["formalization"]["recommended_imports"] = ["Mathlib.Does.Not.Exist"]
    llm = FakeLLM([json.dumps(data)])
    planner = UnifiedPlanner(llm, mathlib_search=_FakeSearch(tmp_path))
    plan = await planner.plan("Prove the odd-sum identity.")
    assert plan is not None
    formal = plan.formalization
    assert formal.recommended_theorem is None
    assert formal.recommended_imports == []
    assert "made_up_theorem" in formal.notes
    assert "Mathlib.Does.Not.Exist" in formal.notes
    # The informal part is untouched by validation.
    assert plan.plan_text.startswith("STRATEGY:")
