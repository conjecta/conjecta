"""Unit tests for normal-mode claim check helpers."""
from __future__ import annotations

import pytest

from math_agent.agent.claim_check import (
    ClaimCheckResult,
    apply_claim_check_to_trace,
    format_claim_check_preamble,
    normalize_audit,
    run_claim_check,
)
from math_agent.agent.react_state import ReActTrace
from math_agent.billing.models import LLMResponse


class FakeLLM:
    def __init__(self, responses: list[str]):
        self.responses = list(responses)
        self.calls = 0

    async def complete(self, messages, system=None, temperature=None, response_format=None):
        text = self.responses[self.calls]
        self.calls += 1
        return LLMResponse(
            text=text,
            prompt_tokens=0,
            completion_tokens=0,
            total_tokens=0,
        )


def test_normalize_audit_defaults_unknown_status():
    result = normalize_audit({"status": "weird", "issues": ["a"], "revised_claim": "x"})
    assert result.status == "ok"
    assert result.issues == ["a"]
    assert result.revised_claim == "x"


def test_format_preamble_blocks_original_claim():
    result = ClaimCheckResult(
        status="false_as_stated",
        issues=["allows repeats but asks for strict monotone"],
        revised_claim="Require pairwise distinct terms.",
        counterexample_found=True,
        refute_summary="Constant sequence is a counterexample.",
    )
    text = format_claim_check_preamble(result)
    assert "false_as_stated" in text
    assert "Do NOT claim the original statement is proved" in text
    assert "Constant sequence" in text


def test_apply_claim_check_to_trace_appends_once():
    trace = ReActTrace(problem="Prove something.")
    result = ClaimCheckResult(status="needs_clarification", issues=["ambiguous n"])
    apply_claim_check_to_trace(trace, result)
    apply_claim_check_to_trace(trace, result)
    assert trace.context_preamble.count("Claim check (pre-solve):") == 1
    assert trace.claim_check["status"] == "needs_clarification"


@pytest.mark.asyncio
async def test_run_claim_check_marks_false_when_refute_finds_counterexample(monkeypatch):
    async def fake_refute(**kwargs):
        return {
            "counterexample_found": True,
            "summary": "n=1 constant sequence",
            "revised_statement": "Require distinct reals.",
        }

    monkeypatch.setattr(
        "math_agent.agent.claim_check.run_computational_refute",
        fake_refute,
    )
    audit = (
        '{"status":"ok","issues":[],"revised_claim":""}'
    )
    llm = FakeLLM([])
    critic = FakeLLM([audit])
    result = await run_claim_check(
        problem="Any real sequence of length n^2+1 has a strict monotone subsequence of length n+1.",
        llm=llm,
        critic_llm=critic,
        tool_registry=None,
    )
    assert result.counterexample_found is True
    assert result.status == "false_as_stated"
    assert result.revised_claim == "Require distinct reals."
