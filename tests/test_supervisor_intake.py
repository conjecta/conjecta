from unittest.mock import AsyncMock

import pytest

from math_agent.agent.supervisor_intake import (
    SupervisorIntake,
    _normalize_intake,
    looks_temporal,
    resolve_formal_verification,
    resolve_intent,
)
from math_agent.billing.models import LLMResponse
from math_agent.config import VerifierConfig


def test_normalize_intake_with_source_digest():
    result = _normalize_intake({
        "strategy": "react",
        "source_digest": "Paper on random tensors and anti-concentration.",
        "source_label": "arXiv:1905.00802",
    })
    assert result.strategy == "react"
    assert "random tensors" in result.source_digest
    assert result.source_label == "arXiv:1905.00802"


def test_normalize_intake_invalid_strategy_falls_back_to_react():
    result = _normalize_intake({"strategy": "unknown", "source_digest": ""})
    assert result.strategy == "react"


def test_resolve_intent_forces_new_problem_without_history():
    assert resolve_intent("clarify", has_history=False) == "new_problem"
    assert resolve_intent("extend", has_history=False) == "new_problem"


def test_resolve_intent_accepts_valid_intents_with_history():
    assert resolve_intent("clarify", has_history=True) == "clarify"
    assert resolve_intent("extend", has_history=True) == "extend"
    assert resolve_intent("new_problem", has_history=True) == "new_problem"


def test_resolve_intent_invalid_or_missing_falls_back_to_extend():
    assert resolve_intent(None, has_history=True) == "extend"
    assert resolve_intent("", has_history=True) == "extend"
    assert resolve_intent("unknown", has_history=True) == "extend"


def test_resolve_intent_lean_request_never_clarify():
    assert (
        resolve_intent(
            "clarify",
            has_history=True,
            problem="请用 Lean 验证上面的证明",
        )
        == "extend"
    )
    assert (
        resolve_intent(
            "clarify",
            has_history=True,
            problem="formalize this in Lean 4",
        )
        == "extend"
    )


def test_resolve_intent_diagram_request_never_clarify():
    assert (
        resolve_intent(
            "clarify",
            has_history=True,
            problem="画图解释",
        )
        == "extend"
    )
    assert (
        resolve_intent(
            "clarify",
            has_history=True,
            problem="请画图说明上述证明",
        )
        == "extend"
    )
    assert (
        resolve_intent(
            "clarify",
            has_history=True,
            problem="draw a diagram of the similar triangles",
        )
        == "extend"
    )
    # Prior turns mentioning 画图 must not force extend on an unrelated follow-up.
    assert (
        resolve_intent(
            "clarify",
            has_history=True,
            problem=(
                "Prior conversation (context only; do not treat it as the current theorem):\n"
                "User: 画图解释勾股定理\nAssistant: ...\n\n"
                "Current user request (authoritative target):\n为什么？"
            ),
        )
        == "clarify"
    )


def test_requires_diagram_matches_explicit_requests():
    from math_agent.agent.supervisor_intake import requires_diagram

    assert requires_diagram("画图解释")
    assert requires_diagram("图解勾股定理")
    assert requires_diagram("Please illustrate with a figure")
    assert not requires_diagram("证明勾股定理")
    assert not requires_diagram(
        "Conversation so far:\nUser: 画图解释\nAssistant: ok\n\nCurrent question:\n为什么？"
    )


def test_normalize_intake_includes_intent():
    result = _normalize_intake(
        {
            "strategy": "react",
            "intent": "clarify",
            "source_digest": "",
            "source_label": "",
            "needs_search": False,
            "search_query": "",
        },
        has_history=True,
    )
    assert result.intent == "clarify"


def test_looks_temporal_detects_latest_award_queries():
    assert looks_temporal("解释最新菲尔兹奖命题")
    assert looks_temporal("Who won the latest Fields Medal?")
    assert looks_temporal("今年的菲尔兹奖")
    assert not looks_temporal("Prove that the sum of two even integers is even.")


@pytest.mark.asyncio
async def test_new_problem_without_source_or_history_skips_intake_llm():
    llm = AsyncMock()
    intake = SupervisorIntake(llm)

    result = await intake.analyze(
        "Prove that the sum of two even integers is even.",
        has_history=False,
    )

    llm.complete.assert_not_awaited()
    assert result.intent == "new_problem"
    assert result.strategy == "react"
    assert result.needs_search is False


@pytest.mark.asyncio
async def test_temporal_fresh_problem_searches_without_intake_llm(monkeypatch):
    llm = AsyncMock()
    intake = SupervisorIntake(llm)

    async def fake_search(query: str) -> str:
        assert "菲尔兹" in query
        return "[web search via DuckDuckGo]\n1. IMU Fields Medal 2026"

    monkeypatch.setattr(
        "math_agent.agent.supervisor_intake._intake_web_search",
        fake_search,
    )

    result = await intake.analyze("解释最新菲尔兹奖命题", has_history=False)

    llm.complete.assert_not_awaited()
    assert result.needs_search is True
    assert result.search_results.startswith("[web search via DuckDuckGo]")
    assert "2026" in result.search_results


@pytest.mark.asyncio
async def test_fast_intake_detects_explicit_formal_verification_request():
    llm = AsyncMock()
    intake = SupervisorIntake(llm)

    result = await intake.analyze(
        "Formalize this theorem in Lean 4 and verify the proof.",
        has_history=False,
    )

    llm.complete.assert_not_awaited()
    assert result.require_formal_verification is True


@pytest.mark.asyncio
async def test_research_intake_classifies_search_need_for_fresh_problem():
    llm = AsyncMock()
    llm.complete.return_value = LLMResponse(
        text=(
            '{"intent":"new_problem","strategy":"react","source_digest":"",'
            '"source_label":"","needs_search":false,"search_query":""}'
        ),
        prompt_tokens=0,
        completion_tokens=0,
        total_tokens=0,
    )
    intake = SupervisorIntake(llm)

    result = await intake.analyze(
        "Prove a difficult stochastic domination theorem.",
        has_history=False,
        proactive_search=True,
    )

    llm.complete.assert_awaited_once()
    assert result.intent == "new_problem"


def test_formal_policy_defaults_to_explicit_requests_only():
    config = VerifierConfig()

    assert resolve_formal_verification(
        "Prove that sqrt(2) is irrational", config
    ) is False
    assert resolve_formal_verification("Prove it formally in Lean", config) is True


def test_all_theorems_policy_requires_lean_for_proof_requests():
    config = VerifierConfig(formal_policy="all_theorems")

    assert resolve_formal_verification("证明两个偶数之和仍为偶数", config) is True


def test_disabled_formal_policy_overrides_explicit_request():
    config = VerifierConfig(formal_policy="disabled")

    assert resolve_formal_verification("Formalize this in Lean 4", config) is False
