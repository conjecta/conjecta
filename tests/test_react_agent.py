import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from math_agent.agent.react_agent import ReActAgent
from math_agent.agent.formal_evidence import formal_evidence_id
from math_agent.agent.reviewers import CriticReviewer, StatementFidelityReviewer
from math_agent.agent.react_state import (
    Action,
    ReActTrace,
    ReActTurn,
    ReviewResult,
    ToolObservation,
)
from math_agent.billing.models import LLMResponse
from math_agent.config import AgentConfig


class StubConsolidator:
    def __init__(self):
        self.calls = []

    async def consolidate(self, trace, solution):
        self.calls.append((trace.problem, solution.final_answer))


class FailingConsolidator:
    async def consolidate(self, trace, solution):
        raise RuntimeError("consolidator failed")


class FakeLLM:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    def _next_response(self) -> LLMResponse:
        response = self.responses[self.calls]
        self.calls += 1
        if isinstance(response, LLMResponse):
            return response
        return LLMResponse(
            text=response,
            prompt_tokens=0,
            completion_tokens=0,
            total_tokens=0,
        )

    async def complete(
        self,
        messages,
        system=None,
        temperature=None,
        response_format=None,
        *,
        logprobs=False,
    ):
        return self._next_response()

    async def stream(
        self,
        messages,
        system=None,
        temperature=None,
        response_format=None,
        *,
        logprobs=False,
    ):
        response = self._next_response()
        yield LLMResponse(
            text=response.text,
            prompt_tokens=0,
            completion_tokens=0,
            total_tokens=0,
        )
        yield LLMResponse(
            text="",
            prompt_tokens=response.prompt_tokens,
            completion_tokens=response.completion_tokens,
            total_tokens=response.total_tokens,
            mean_logprob=response.mean_logprob,
        )


class SplitOnlyLLM:
    def __init__(self, responses):
        self.responses = list(responses)
        self.index = 0

    async def complete(
        self,
        messages,
        system=None,
        temperature=None,
        response_format=None,
        *,
        logprobs=False,
    ):
        raise AssertionError("streamed action should parse without repair")

    async def stream(
        self,
        messages,
        system=None,
        temperature=None,
        response_format=None,
        *,
        logprobs=False,
    ):
        response = self.responses[self.index]
        self.index += 1
        midpoint = max(1, len(response) // 2)
        for chunk in (response[:midpoint], response[midpoint:]):
            yield LLMResponse(
                text=chunk,
                prompt_tokens=0,
                completion_tokens=0,
                total_tokens=0,
            )


class ProseReviewLLM:
    def __init__(self):
        self.system_prompts = []

    async def complete(
        self,
        messages,
        system=None,
        temperature=None,
        response_format=None,
        *,
        logprobs=False,
    ):
        self.system_prompts.append(system or "")
        return LLMResponse(
            text="VERDICT: PASS\nISSUES: none\nSUGGESTIONS: none\nCONFIDENCE: 1.0",
            prompt_tokens=0,
            completion_tokens=0,
            total_tokens=0,
        )

    async def stream(
        self,
        messages,
        system=None,
        temperature=None,
        response_format=None,
        *,
        logprobs=False,
    ):
        yield await self.complete(messages, system, temperature, response_format)


def _conclude(answer, evidence_id=None):
    args = {"answer": answer}
    if evidence_id:
        args["evidence_id"] = evidence_id
    return json.dumps(
        {"thought": "Done.", "action": {"name": "conclude", "args": args}},
        ensure_ascii=False,
    )


def _formal_id(action_name, target_claim, artifact):
    return formal_evidence_id(
        action_name=action_name,
        target_claim=target_claim,
        artifact=artifact,
    )


def _action(name, args, *, thought="Act."):
    return json.dumps(
        {"thought": thought, "action": {"name": name, "args": args}},
        ensure_ascii=False,
    )


async def _run_minimal_unreviewed_solution():
    llm = FakeLLM([
        '{"thought": "I know this.", "action": {"name": "conclude", "args": {"answer": "sqrt(2) is irrational"}}}'
    ])
    critic_llm = FakeLLM(["VERDICT: PASS\nISSUES: none\nSUGGESTIONS: none\nCONFIDENCE: 1.0"])
    config = AgentConfig(max_react_steps=5, reviewers_enabled=[], planning_enabled=False)
    agent = ReActAgent(llm=llm, critic_llm=critic_llm, config=config)
    return await agent.solve("Prove sqrt(2) is irrational")


@pytest.mark.asyncio
async def test_react_agent_reaches_conclusion():
    solution = await _run_minimal_unreviewed_solution()
    assert solution.final_answer == "sqrt(2) is irrational"
    assert solution.verification_status == "unreviewed"


@pytest.mark.asyncio
async def test_informal_proof_is_not_retried_for_missing_lean_goal():
    problem = "从 1 到 2n 中任取 n+1 个数，证明其中必有两个数互质。"
    answer = (
        "把 1 到 2n 分成 n 对相邻整数。由抽屉原理，至少有一对都被选中；"
        "相邻整数的最大公约数为 1，所以这两个数互质。"
    )
    llm = FakeLLM([_conclude(answer)])
    reviewer_llm = ProseReviewLLM()
    agent = ReActAgent(
        llm=llm,
        critic_llm=reviewer_llm,
        config=AgentConfig(
            max_react_steps=3,
            reviewers_enabled=["critic", "fidelity"],
            planning_enabled=False,
        ),
    )

    solution = await agent.solve(problem)

    assert solution.final_answer == answer
    assert solution.verification_status == "reviewed"
    assert len(solution.turns) == 1
    fidelity_system = next(
        system
        for system in reviewer_llm.system_prompts
        if "fidelity gate" in system or "fidelity reviewer" in system
    )
    assert "NOT a Lean formalization review" in fidelity_system


@pytest.mark.asyncio
async def test_easy_prompt_skips_reviewer_panel():
    llm = FakeLLM([_conclude("4")])

    class BoomCritic:
        name = "critic"

        async def review(self, turn, trace):
            raise AssertionError("reviewer should be skipped for easy prompts")

    agent = ReActAgent(
        llm=llm,
        critic_llm=FakeLLM(['{"difficulty": "easy", "reason": "trivial arithmetic"}']),
        config=AgentConfig(
            max_react_steps=3,
            reviewers_enabled=["critic"],
            skip_review_on_easy_prompt=True,
            # Force confidence path off so only easy-prompt gating applies.
            skip_review_min_confidence=1.01,
        ),
    )
    agent.reviewers = [BoomCritic()]

    events = []

    async def on_event(event):
        events.append(event)

    solution = await agent.solve("What is 2+2?", on_event=on_event)

    assert solution.final_answer == "4"
    assert solution.verification_status == "unreviewed"
    assert solution.turns[0].observation.metadata.get("skip_review_reason") == "easy_prompt"
    assert any(
        event.get("type") == "stage_status"
        and event.get("stage") == "accepting"
        and "简单问题" in str(event.get("message") or "")
        for event in events
    )


def test_is_easy_prompt_heuristic():
    # The regex keyword table is gone; trivial prompts short-circuit
    # structurally and everything else is a critic call (see
    # tests/test_prompt_difficulty.py for the full matrix).
    from math_agent.agent.prompt_difficulty import trivially_easy

    assert trivially_easy("3+5=?")
    assert trivially_easy("12*9")
    assert not trivially_easy("What is 2+2?")
    assert not trivially_easy("计算 3+5")
    assert not trivially_easy("Prove that sqrt(2) is irrational.")
    assert not trivially_easy("证明勾股定理")


@pytest.mark.asyncio
async def test_normal_force_review_blocks_high_confidence_skip():
    import math

    conclude = _conclude("even + even = even")
    llm = FakeLLM(
        [
            LLMResponse(
                text=conclude,
                prompt_tokens=0,
                completion_tokens=0,
                total_tokens=0,
                mean_logprob=math.log(0.99),
            )
        ]
    )
    reviewed = 0

    class CountingCritic:
        name = "critic"

        async def review(self, turn, trace):
            nonlocal reviewed
            reviewed += 1
            from math_agent.agent.react_state import ReviewResult

            return ReviewResult(
                reviewer="critic",
                verdict="PASS",
                confidence=0.9,
            )

    agent = ReActAgent(
        llm=llm,
        critic_llm=FakeLLM([]),
        config=AgentConfig(
            max_react_steps=3,
            reviewers_enabled=["critic"],
            skip_review_min_confidence=0.90,
            skip_review_on_easy_prompt=False,
            planning_enabled=False,
            normal_force_review=True,
            normal_claim_check_enabled=False,
        ),
    )
    agent.reviewers = [CountingCritic()]

    solution = await agent.solve(
        "Prove that the sum of two even integers is even.",
    )
    assert reviewed == 1
    assert solution.verification_status != "unreviewed"
    assert solution.turns[0].observation.metadata.get("skip_review_reason") is None


@pytest.mark.asyncio
async def test_claim_check_injects_preamble_and_keeps_reviewers(monkeypatch):
    from math_agent.agent.claim_check import ClaimCheckResult

    async def fake_run_claim_check(**kwargs):
        return ClaimCheckResult(
            status="false_as_stated",
            issues=["strict monotone with possible repeats"],
            revised_claim="Require distinct terms.",
            counterexample_found=True,
            refute_summary="Constant sequence.",
        )

    monkeypatch.setattr(
        "math_agent.agent.claim_check.run_claim_check",
        fake_run_claim_check,
    )

    conclude = _conclude("The stated theorem is false; constant sequences are counterexamples.")
    llm = FakeLLM([conclude])
    reviewed = 0

    class CountingCritic:
        name = "critic"

        async def review(self, turn, trace):
            nonlocal reviewed
            reviewed += 1
            assert "Claim check" in (trace.context_preamble or "")
            assert trace.claim_check.get("counterexample_found") is True
            from math_agent.agent.react_state import ReviewResult

            return ReviewResult(
                reviewer="critic",
                verdict="PASS",
                confidence=0.9,
            )

    agent = ReActAgent(
        llm=llm,
        critic_llm=FakeLLM([]),
        config=AgentConfig(
            max_react_steps=3,
            reviewers_enabled=["critic"],
            planning_enabled=False,
            normal_force_review=True,
            normal_claim_check_enabled=True,
            skip_review_on_easy_prompt=False,
        ),
    )
    agent.reviewers = [CountingCritic()]

    events = []

    async def on_event(event):
        events.append(event)

    solution = await agent.solve(
        "Prove Erdős–Szekeres for arbitrary real sequences with strict monotone subsequences.",
        on_event=on_event,
    )
    assert reviewed == 1
    assert any(
        e.get("type") == "stage_status" and e.get("stage") == "claim_check"
        for e in events
    )
    assert "Constant sequence" in (solution.trace.context_preamble or "")


@pytest.mark.asyncio
async def test_high_logprob_confidence_skips_reviewer_panel():
    import math

    conclude = _conclude("2 + 2 = 4")
    llm = FakeLLM(
        [
            LLMResponse(
                text=conclude,
                prompt_tokens=0,
                completion_tokens=0,
                total_tokens=0,
                mean_logprob=math.log(0.95),
            )
        ]
    )

    class BoomCritic:
        name = "critic"

        async def review(self, turn, trace):
            raise AssertionError("reviewer should be skipped for high confidence")

    agent = ReActAgent(
        llm=llm,
        critic_llm=FakeLLM([]),
        config=AgentConfig(
            max_react_steps=3,
            reviewers_enabled=["critic"],
            skip_review_min_confidence=0.90,
            skip_review_on_easy_prompt=False,
            planning_enabled=False,
            # Isolate the legacy logprob skip path from normal-mode force review.
            normal_force_review=False,
            normal_claim_check_enabled=False,
        ),
    )
    agent.reviewers = [BoomCritic()]

    events = []

    async def on_event(event):
        events.append(event)

    solution = await agent.solve(
        "Prove that the sum of two even integers is even.",
        on_event=on_event,
    )

    assert solution.final_answer == "2 + 2 = 4"
    assert solution.verification_status == "unreviewed"
    assert solution.turns[0].observation.metadata.get("skip_review_reason") == "high_confidence"
    assert any(
        event.get("type") == "stage_status" and event.get("stage") == "accepting"
        for event in events
    )
    assert not any(
        event.get("type") == "stage_status" and event.get("stage") == "reviewing"
        for event in events
    )


@pytest.mark.asyncio
async def test_force_review_never_skips_reviewer_panel():
    import math

    # Both skip triggers are armed (easy prompt + high confidence), but
    # force_review must keep the panel: research workers earn their status.
    conclude = _conclude("4")
    llm = FakeLLM(
        [
            LLMResponse(
                text=conclude,
                prompt_tokens=0,
                completion_tokens=0,
                total_tokens=0,
                mean_logprob=math.log(0.99),
            )
        ]
    )
    reviewed = 0

    class CountingCritic:
        name = "critic"

        async def review(self, turn, trace):
            nonlocal reviewed
            reviewed += 1
            return ReviewResult(
                reviewer="critic",
                verdict="PASS",
                confidence=0.9,
            )

    agent = ReActAgent(
        llm=llm,
        critic_llm=FakeLLM([]),
        config=AgentConfig(
            max_react_steps=3,
            reviewers_enabled=["critic"],
            skip_review_min_confidence=0.90,
            skip_review_on_easy_prompt=True,
            planning_enabled=False,
            force_review=True,
        ),
    )
    agent.reviewers = [CountingCritic()]

    events = []

    async def on_event(event):
        events.append(event)

    solution = await agent.solve("What is 2+2?", on_event=on_event)

    assert reviewed == 1
    assert solution.verification_status == "reviewed"
    assert solution.turns[0].observation.metadata.get("skip_review_reason") is None
    assert not any(
        event.get("type") == "stage_status" and event.get("stage") == "accepting"
        for event in events
    )


@pytest.mark.asyncio
async def test_failed_conclusion_is_revised_before_acceptance():
    llm = FakeLLM([
        _conclude("2 + 2 = 5"),
        _conclude("2 + 2 = 4"),
    ])
    critic_llm = FakeLLM([
        "VERDICT: FAIL\nISSUES: arithmetic error\nSUGGESTIONS: recompute\nCONFIDENCE: 1.0",
        "VERDICT: PASS\nISSUES: none\nSUGGESTIONS: none\nCONFIDENCE: 1.0",
    ])
    config = AgentConfig(
        max_react_steps=5,
        reviewers_enabled=["critic"],
        skip_review_on_easy_prompt=False,
        skip_review_min_confidence=1.01,
        planning_enabled=False,
    )
    agent = ReActAgent(llm=llm, critic_llm=critic_llm, config=config)

    solution = await agent.solve("Prove that 2 + 2 = 4.")

    assert solution.final_answer == "2 + 2 = 4"
    assert [
        turn.action.args["answer"]
        for turn in solution.turns
        if turn.action.name == "conclude"
    ] == ["2 + 2 = 5", "2 + 2 = 4"]
    assert solution.verification_status == "reviewed"
    assert solution.verification_issues == []


@pytest.mark.asyncio
async def test_best_of_n_selects_reviewer_accepted_complete_candidate():
    llm = FakeLLM(
        [
            _action("think", {"text": f"work {index}"})
            for index in range(4)
        ]
        + [
            _conclude("Incorrect primary answer."),
            json.dumps(
                {
                    "candidates": [
                        {"answer": "Correct independent answer."},
                        {"answer": "Another incorrect answer."},
                    ]
                }
            ),
        ]
    )
    critic_llm = FakeLLM(
        [
            '{"difficulty": "hard", "reason": "proof search"}',
            "VERDICT: FAIL\nISSUES: wrong\nSUGGESTIONS: revise\nCONFIDENCE: 1.0",
            "VERDICT: PASS\nISSUES: none\nSUGGESTIONS: none\nCONFIDENCE: 0.9",
            "VERDICT: FAIL\nISSUES: gap\nSUGGESTIONS: revise\nCONFIDENCE: 0.8",
        ]
    )
    config = AgentConfig(
        max_react_steps=5,
        reviewers_enabled=["critic"],
        conclusion_candidate_count=3,
        candidate_search_min_turns=4,
        planning_enabled=False,
    )
    agent = ReActAgent(llm=llm, critic_llm=critic_llm, config=config)
    events = []

    async def on_event(event):
        events.append(event)

    solution = await agent.solve("Find the correct proof.", on_event=on_event)

    assert solution.final_answer == "Correct independent answer."
    assert solution.verification_status == "reviewed"
    assert solution.turns[-1].action.args["answer"] == "Correct independent answer."
    candidate_event = next(
        event for event in events if event.get("type") == "candidate_search"
    )
    assert candidate_event["candidate_count"] == 3
    assert candidate_event["selected_index"] == 1


@pytest.mark.asyncio
async def test_failed_conclusion_revisions_are_capped_at_two():
    llm = FakeLLM([
        _conclude("first rejected answer"),
        _conclude("second rejected answer"),
        _conclude("third rejected answer"),
    ])
    critic_llm = FakeLLM([
        '{"difficulty": "hard", "reason": "proof required"}',
        "VERDICT: FAIL\nISSUES: first gap\nSUGGESTIONS: revise\nCONFIDENCE: 1.0",
        "VERDICT: FAIL\nISSUES: second gap\nSUGGESTIONS: revise\nCONFIDENCE: 1.0",
        "VERDICT: FAIL\nISSUES: final gap\nSUGGESTIONS: revise\nCONFIDENCE: 1.0",
    ])
    config = AgentConfig(
        max_react_steps=5,
        max_conclusion_revisions=2,
        reviewers_enabled=["critic"],
        planning_enabled=False,
    )
    agent = ReActAgent(llm=llm, critic_llm=critic_llm, config=config)

    solution = await agent.solve("Give a proof.")

    assert config.max_conclusion_revisions == 2
    assert solution.final_answer == "third rejected answer"
    assert solution.verification_status == "best_effort"
    assert solution.verification_issues == ["final gap"]
    assert [turn.action.name for turn in solution.turns] == [
        "conclude",
        "conclude",
        "conclude",
    ]


@pytest.mark.asyncio
async def test_react_agent_continues_an_initial_trace():
    initial_trace = ReActTrace(problem="What is 2 + 2?", current_goal="compute the sum")
    initial_trace.turns.append(
        ReActTurn(
            thought="Start from arithmetic.",
            action=Action(name="think", args={"text": "use addition"}),
            observation=ToolObservation(success=True, output="Thinking recorded."),
            step_num=4,
        )
    )
    llm = FakeLLM([_conclude("4")])
    config = AgentConfig(max_react_steps=5, reviewers_enabled=[])
    agent = ReActAgent(llm=llm, critic_llm=llm, config=config)

    solution = await agent.solve(
        "What is 2 + 2?",
        initial_trace=initial_trace,
    )

    assert solution.trace is initial_trace
    assert solution.turns is initial_trace.turns
    assert [turn.step_num for turn in solution.turns] == [4, 5]
    assert solution.final_answer == "4"


@pytest.mark.asyncio
async def test_react_agent_resume_uses_serialized_next_step_number():
    initial_trace = ReActTrace(
        problem="What is 2 + 2?",
        current_goal="compute the sum",
        next_step_num=5,
    )
    initial_trace.turns.append(
        ReActTurn(
            thought="Saved earlier work.",
            action=Action(name="think", args={"text": "saved"}),
            observation=ToolObservation(success=True, output="saved"),
            step_num=2,
        )
    )
    llm = FakeLLM([_conclude("4")])
    agent = ReActAgent(
        llm=llm,
        critic_llm=llm,
        config=AgentConfig(max_react_steps=5, reviewers_enabled=[]),
    )

    solution = await agent.solve("What is 2 + 2?", initial_trace=initial_trace)

    assert [turn.step_num for turn in solution.turns] == [2, 5]


@pytest.mark.asyncio
async def test_react_agent_resume_preserves_consumed_search_budget(monkeypatch):
    initial_trace = ReActTrace(
        problem="Find a theorem",
        current_goal="find the theorem",
        next_step_num=4,
        budget_consumption={"search_mathlib_calls": 3},
    )
    llm = FakeLLM([
        '{"thought":"Search once more.","action":{"name":"search_mathlib","args":{"query":"theorem"}}}',
        _conclude("No additional search is needed."),
    ])
    agent = ReActAgent(
        llm=llm,
        critic_llm=llm,
        config=AgentConfig(max_react_steps=5, reviewers_enabled=[], planning_enabled=False),
    )
    execute = AsyncMock(
        return_value=ToolObservation(success=True, output="should not execute")
    )
    monkeypatch.setattr(agent, "_execute_with_heartbeat", execute)

    solution = await agent.solve("Find a theorem", initial_trace=initial_trace)

    execute.assert_not_awaited()
    assert solution.turns[-2].observation.error == "search_mathlib_limit_reached"


@pytest.mark.asyncio
async def test_react_agent_resume_counts_prior_rejected_conclusions():
    initial_trace = ReActTrace(problem="Give a proof.", current_goal="prove the claim")
    for step_num, answer, issue in (
        (1, "first rejected answer", "first gap"),
        (2, "second rejected answer", "second gap"),
    ):
        initial_trace.turns.append(
            ReActTurn(
                thought="Try a conclusion.",
                action=Action(name="conclude", args={"answer": answer}),
                observation=ToolObservation(success=True, output=f"Conclusion: {answer}"),
                reviews=[
                    ReviewResult(
                        reviewer="critic",
                        verdict="FAIL",
                        issues=[issue],
                        confidence=1.0,
                    )
                ],
                step_num=step_num,
            )
        )
    llm = FakeLLM([
        _conclude("third rejected answer"),
        _conclude("fourth answer must not run"),
    ])
    critic_llm = FakeLLM([
        '{"difficulty": "hard", "reason": "proof required"}',
        "VERDICT: FAIL\nISSUES: third gap\nSUGGESTIONS: revise\nCONFIDENCE: 1.0",
        "VERDICT: PASS\nISSUES: none\nSUGGESTIONS: none\nCONFIDENCE: 1.0",
    ])
    config = AgentConfig(
        max_react_steps=4,
        max_conclusion_revisions=2,
        reviewers_enabled=["critic"],
    )
    agent = ReActAgent(llm=llm, critic_llm=critic_llm, config=config)

    solution = await agent.solve(
        "Give a proof.",
        initial_trace=initial_trace,
    )

    assert solution.final_answer == "third rejected answer"
    assert solution.verification_status == "best_effort"
    assert solution.verification_issues == ["third gap"]
    assert len([turn for turn in solution.turns if turn.action.name == "conclude"]) == 3
    assert llm.calls == 1


@pytest.mark.asyncio
async def test_react_agent_resume_does_not_exceed_exhausted_conclusion_budget():
    initial_trace = ReActTrace(problem="Give a proof.", current_goal="prove the claim")
    for step_num, answer, issue in (
        (1, "first rejected answer", "first gap"),
        (2, "second rejected answer", "second gap"),
        (3, "third rejected answer", "third gap"),
    ):
        initial_trace.turns.append(
            ReActTurn(
                thought="Try a conclusion.",
                action=Action(name="conclude", args={"answer": answer}),
                observation=ToolObservation(success=True, output=f"Conclusion: {answer}"),
                reviews=[
                    ReviewResult(
                        reviewer="critic",
                        verdict="FAIL",
                        issues=[issue],
                        confidence=1.0,
                    )
                ],
                step_num=step_num,
            )
        )
    llm = FakeLLM([_conclude("fourth answer must not run")])
    critic_llm = FakeLLM([
        "VERDICT: PASS\nISSUES: none\nSUGGESTIONS: none\nCONFIDENCE: 1.0"
    ])
    config = AgentConfig(
        max_react_steps=4,
        max_conclusion_revisions=2,
        reviewers_enabled=["critic"],
    )
    agent = ReActAgent(llm=llm, critic_llm=critic_llm, config=config)

    solution = await agent.solve(
        "Give a proof.",
        initial_trace=initial_trace,
    )

    assert solution.final_answer == "third rejected answer"
    assert solution.verification_status == "best_effort"
    assert solution.verification_issues == ["third gap"]
    assert len([turn for turn in solution.turns if turn.action.name == "conclude"]) == 3
    assert llm.calls == 0


@pytest.mark.asyncio
async def test_react_agent_think_then_conclude():
    llm = FakeLLM([
        '{"thought": "Let me reason internally first.", "action": {"name": "think", "args": {"text": "internal reasoning"}}}',
        '{"thought": "Now I am ready.", "action": {"name": "conclude", "args": {"answer": "2 + 2 = 4"}}}'
    ])
    critic_llm = FakeLLM(['{"difficulty": "easy", "reason": "trivial arithmetic"}'])
    config = AgentConfig(max_react_steps=5, reviewers_enabled=[])
    agent = ReActAgent(llm=llm, critic_llm=critic_llm, config=config)
    solution = await agent.solve("What is 2 + 2?")
    assert solution.final_answer == "2 + 2 = 4"
    assert len(solution.turns) == 2


@pytest.mark.asyncio
async def test_react_agent_set_goal_updates_goal():
    llm = FakeLLM([
        '{"thought": "Break the problem down.", "action": {"name": "set_goal", "args": {"goal": "show n is even"}}}',
        '{"thought": "Done.", "action": {"name": "conclude", "args": {"answer": "n is even"}}}'
    ])
    critic_llm = FakeLLM(["VERDICT: PASS\nISSUES: none\nSUGGESTIONS: none\nCONFIDENCE: 1.0"])
    config = AgentConfig(max_react_steps=5, reviewers_enabled=[], planning_enabled=False)
    agent = ReActAgent(llm=llm, critic_llm=critic_llm, config=config)
    solution = await agent.solve("Prove n is even")
    assert solution.final_answer == "n is even"
    assert solution.turns[0].action.name == "set_goal"


@pytest.mark.asyncio
async def test_set_goal_checkpoint_contains_updated_goal():
    llm = FakeLLM([
        '{"thought":"Refine it.","action":{"name":"set_goal","args":{"goal":"prove the inductive step"}}}',
        _conclude("done"),
    ])
    agent = ReActAgent(
        llm=llm,
        critic_llm=llm,
        config=AgentConfig(max_react_steps=2, reviewers_enabled=[], planning_enabled=False),
    )
    checkpoints = []

    await agent.solve(
        "Prove P",
        on_checkpoint=checkpoints.append,
    )

    assert checkpoints[0]["current_goal"] == "prove the inductive step"


@pytest.mark.asyncio
async def test_negative_conclusion_revision_budget_does_not_crash_solve():
    """max_conclusion_revisions < 0 means unlimited; a fresh solve must not
    treat the budget as exhausted and index into empty prior_conclusions."""
    llm = FakeLLM([
        '{"thought": "Trivial.", "action": {"name": "think", "args": {"text": "ok"}}}',
        _conclude("done"),
    ])
    agent = ReActAgent(
        llm=llm,
        critic_llm=llm,
        config=AgentConfig(
            max_react_steps=3,
            max_conclusion_revisions=-1,
            reviewers_enabled=[],
            planning_enabled=False,
        ),
    )

    solution = await agent.solve("What is 1 + 1?")

    assert solution.final_answer == "done"


@pytest.mark.asyncio
async def test_react_agent_compute_tool_executes():
    llm = FakeLLM([
        '{"thought": "Calculate the sum.", "action": {"name": "compute", "args": {"code": "2 + 2"}}}',
        _conclude("4"),
    ])
    critic_llm = FakeLLM(['{"difficulty": "easy", "reason": "trivial arithmetic"}'])
    config = AgentConfig(max_react_steps=5, reviewers_enabled=[])
    agent = ReActAgent(llm=llm, critic_llm=critic_llm, config=config)
    solution = await agent.solve("What is 2 + 2?")
    assert solution.final_answer == "4"
    assert any(t.action.name == "compute" for t in solution.turns)
    compute_turn = next(t for t in solution.turns if t.action.name == "compute")
    assert compute_turn.observation.success
    assert "4" in compute_turn.observation.output


@pytest.mark.asyncio
async def test_failed_lean_check_is_not_collected_as_formal_verification():
    failed_code = "theorem false_claim : False := by trivial"
    llm = FakeLLM([
        json.dumps({
            "thought": "Check the proposed proof.",
            "action": {"name": "lean_check", "args": {"code": failed_code}},
        }),
        _conclude("The attempted formal proof failed."),
    ])
    config = AgentConfig(
        max_react_steps=5,
        max_conclusion_revisions=0,
        reviewers_enabled=[],
        planning_enabled=False,
    )
    agent = ReActAgent(llm=llm, critic_llm=llm, config=config)

    async def fail_lean_check(action, trace):
        assert action.name == "lean_check"
        return ToolObservation(
            success=False,
            output="Lean verification: FAILED",
            lean_code=failed_code,
            error="type mismatch",
        )

    agent._execute_action = fail_lean_check

    solution = await agent.solve("Prove False.")

    assert failed_code not in solution.lean_proofs
    assert solution.lean_proofs == []


@pytest.mark.asyncio
async def test_formal_verification_marks_successful_lean_check_verified():
    verified_code = "theorem true_claim : True := by trivial"
    evidence_id = _formal_id("lean_check", "Prove True.", verified_code)
    llm = FakeLLM([
        json.dumps({
            "thought": "Check the proof.",
            "action": {"name": "lean_check", "args": {"code": verified_code}},
        }),
        _conclude("True follows by trivial.", evidence_id),
    ])
    config = AgentConfig(max_react_steps=2, reviewers_enabled=[], planning_enabled=False)
    agent = ReActAgent(llm=llm, critic_llm=llm, config=config)

    async def pass_lean_check(action, trace):
        assert action.name == "lean_check"
        return ToolObservation(
            success=True,
            output="Lean verification: PASSED",
            lean_code=verified_code,
        )

    agent._execute_action = pass_lean_check

    solution = await agent.solve(
        "Prove True.",
        require_formal_verification=True,
    )

    assert solution.verification_status == "verified"
    assert solution.verification_issues == []
    assert solution.lean_proofs == [verified_code]


@pytest.mark.asyncio
async def test_formal_run_redirects_premature_conclusion_to_lean_tool():
    verified_code = "theorem true_claim : True := by trivial"
    evidence_id = _formal_id("lean_check", "Prove True.", verified_code)
    llm = FakeLLM([
        _conclude("True, informally."),
        _action("lean_check", {"code": verified_code}),
        _conclude("True follows by the checked proof.", evidence_id),
    ])
    agent = ReActAgent(
        llm=llm,
        critic_llm=llm,
        config=AgentConfig(max_react_steps=3, reviewers_enabled=[], planning_enabled=False),
    )

    async def pass_lean_check(action, trace):
        return ToolObservation(
            success=True,
            output="Lean verification: PASSED",
            lean_code=verified_code,
        )

    agent._execute_action = pass_lean_check
    solution = await agent.solve("Prove True.", require_formal_verification=True)

    assert solution.verification_status == "verified"
    assert solution.final_answer == "True follows by the checked proof."
    first = solution.turns[0]
    assert first.action.name == "conclude"
    assert first.observation.error == "missing_formal_evidence"


@pytest.mark.asyncio
async def test_diagram_request_redirects_premature_conclusion_to_plot_figure():
    embed = "![相似三角形示意图](/api/solve/figures/sess/fig-1.png)"
    llm = FakeLLM([
        _conclude("证明如上，无图。"),
        _action("plot_figure", {"code": "import matplotlib.pyplot as plt\nplt.plot([0,1])"}),
        _conclude(f"如下图：\n\n{embed}\n\n由相似得证。"),
    ])
    agent = ReActAgent(
        llm=llm,
        critic_llm=llm,
        config=AgentConfig(
            max_react_steps=3,
            reviewers_enabled=[],
            planning_enabled=False,
            tools=["plot_figure"],
        ),
    )

    async def pass_plot(action, trace):
        assert action.name == "plot_figure"
        return ToolObservation(
            success=True,
            output=f"Figure saved.\nEmbed this markdown image link:\n{embed}",
        )

    agent._execute_action = pass_plot
    solution = await agent.solve("画图解释")

    assert solution.final_answer.startswith("如下图：")
    assert embed in solution.final_answer
    first = solution.turns[0]
    assert first.action.name == "conclude"
    assert first.observation.error == "missing_diagram"
    assert any(t.action.name == "plot_figure" for t in solution.turns)


@pytest.mark.asyncio
async def test_formal_verification_rejects_wrong_evidence_id():
    verified_code = "theorem true_claim : True := by trivial"
    llm = FakeLLM([
        _action("lean_check", {"code": verified_code}),
        _conclude("True follows by trivial.", "formal-wrong"),
    ])
    agent = ReActAgent(
        llm=llm,
        critic_llm=llm,
        config=AgentConfig(
            max_react_steps=2,
            max_conclusion_revisions=0,
            reviewers_enabled=[],
            planning_enabled=False,
        ),
    )

    async def pass_lean_check(action, trace):
        return ToolObservation(
            success=True,
            output="Lean verification: PASSED",
            lean_code=verified_code,
        )

    agent._execute_action = pass_lean_check
    solution = await agent.solve(
        "Prove True.",
        require_formal_verification=True,
    )

    assert solution.verification_status == "best_effort"
    assert solution.lean_proofs == []
    assert solution.verification_issues == [
        "Formal verification report is required."
    ]


@pytest.mark.asyncio
async def test_formal_verification_revises_a_lean_pass_with_critic_failure():
    verified_code = "theorem true_claim : True := by trivial"
    evidence_id = _formal_id("lean_check", "Prove True.", verified_code)
    lean_action = json.dumps({
        "thought": "Check the proof.",
        "action": {"name": "lean_check", "args": {"code": verified_code}},
    })
    llm = FakeLLM([
        lean_action,
        _conclude("A logically incomplete explanation.", evidence_id),
        lean_action,
        _conclude("True follows by the verified trivial proof.", evidence_id),
    ])
    critic_llm = FakeLLM([
        '{"difficulty": "hard", "reason": "proof required"}',
        "VERDICT: FAIL\nISSUES: logical gap\nSUGGESTIONS: explain the proof\nCONFIDENCE: 1.0",
        "VERDICT: PASS\nISSUES: none\nSUGGESTIONS: none\nCONFIDENCE: 1.0",
    ])
    config = AgentConfig(max_react_steps=4, reviewers_enabled=["critic"], planning_enabled=False)
    agent = ReActAgent(llm=llm, critic_llm=critic_llm, config=config)

    async def pass_lean_check(action, trace):
        assert action.name == "lean_check"
        return ToolObservation(
            success=True,
            output="Lean verification: PASSED",
            lean_code=verified_code,
        )

    agent._execute_action = pass_lean_check

    solution = await agent.solve(
        "Prove True.",
        require_formal_verification=True,
    )

    assert solution.final_answer == "True follows by the verified trivial proof."
    assert solution.verification_status == "verified"
    assert [
        turn.action.args["answer"]
        for turn in solution.turns
        if turn.action.name == "conclude"
    ] == [
        "A logically incomplete explanation.",
        "True follows by the verified trivial proof.",
    ]


@pytest.mark.asyncio
async def test_formal_verification_does_not_reuse_evidence_before_previous_conclusion():
    verified_code = "theorem true_claim : True := by trivial"
    evidence_id = _formal_id("lean_check", "Prove True.", verified_code)
    llm = FakeLLM([
        json.dumps({
            "thought": "Check the proof.",
            "action": {"name": "lean_check", "args": {"code": verified_code}},
        }),
        _conclude("A rejected first explanation.", evidence_id),
        _conclude("A revised explanation without a new formal check."),
    ])
    critic_llm = FakeLLM([
        '{"difficulty": "hard", "reason": "proof required"}',
        "VERDICT: FAIL\nISSUES: logical gap\nSUGGESTIONS: revise\nCONFIDENCE: 1.0",
        "VERDICT: PASS\nISSUES: none\nSUGGESTIONS: none\nCONFIDENCE: 1.0",
    ])
    config = AgentConfig(max_react_steps=3, reviewers_enabled=["critic"], planning_enabled=False)
    agent = ReActAgent(llm=llm, critic_llm=critic_llm, config=config)

    async def pass_lean_check(action, trace):
        assert action.name == "lean_check"
        return ToolObservation(
            success=True,
            output="Lean verification: PASSED",
            lean_code=verified_code,
        )

    agent._execute_action = pass_lean_check

    solution = await agent.solve(
        "Prove True.",
        require_formal_verification=True,
    )

    assert solution.final_answer == "A revised explanation without a new formal check."
    assert solution.verification_status == "best_effort"
    assert solution.verification_issues == ["Formal verification report is required."]


@pytest.mark.asyncio
async def test_formal_verification_keeps_a_failure_after_scoped_success():
    verified_code = "theorem true_claim : True := by trivial"
    failed_code = "theorem false_claim : False := by trivial"
    failed_evidence_id = _formal_id(
        "lean_check", "Prove the second formal claim.", failed_code
    )
    llm = FakeLLM([
        json.dumps({
            "thought": "Check a valid proof.",
            "action": {"name": "lean_check", "args": {"code": verified_code}},
        }),
        json.dumps({
            "thought": "Check the proof used by the conclusion.",
            "action": {"name": "lean_check", "args": {"code": failed_code}},
        }),
        _conclude("The second formal claim holds.", failed_evidence_id),
    ])
    config = AgentConfig(max_react_steps=3, reviewers_enabled=[], planning_enabled=False)
    agent = ReActAgent(llm=llm, critic_llm=llm, config=config)

    async def execute_lean_check(action, trace):
        code = action.args["code"]
        if code == verified_code:
            return ToolObservation(
                success=True,
                output="Lean verification: PASSED",
                lean_code=code,
            )
        return ToolObservation(
            success=False,
            output="Lean verification: FAILED",
            lean_code=code,
            error="type mismatch",
        )

    agent._execute_action = execute_lean_check

    solution = await agent.solve(
        "Prove the second formal claim.",
        require_formal_verification=True,
    )

    assert solution.verification_status == "best_effort"
    assert solution.verification_issues == ["type mismatch"]
    assert solution.lean_proofs == []


@pytest.mark.asyncio
async def test_formal_verification_uses_corrected_success_after_scoped_failure():
    failed_code = "theorem false_claim : False := by trivial"
    verified_code = "theorem true_claim : True := by trivial"
    verified_evidence_id = _formal_id(
        "lean_check", "Prove the corrected formal claim.", verified_code
    )
    llm = FakeLLM([
        json.dumps({
            "thought": "Check the first proof attempt.",
            "action": {"name": "lean_check", "args": {"code": failed_code}},
        }),
        json.dumps({
            "thought": "Check the corrected proof.",
            "action": {"name": "lean_check", "args": {"code": verified_code}},
        }),
        _conclude("The corrected formal claim holds.", verified_evidence_id),
    ])
    config = AgentConfig(max_react_steps=3, reviewers_enabled=[], planning_enabled=False)
    agent = ReActAgent(llm=llm, critic_llm=llm, config=config)

    async def execute_lean_check(action, trace):
        code = action.args["code"]
        if code == verified_code:
            return ToolObservation(
                success=True,
                output="Lean verification: PASSED",
                lean_code=code,
            )
        return ToolObservation(
            success=False,
            output="Lean verification: FAILED",
            lean_code=code,
            error="type mismatch",
        )

    agent._execute_action = execute_lean_check

    solution = await agent.solve(
        "Prove the corrected formal claim.",
        require_formal_verification=True,
    )

    assert solution.verification_status == "verified"
    assert solution.verification_issues == []
    assert solution.lean_proofs == [verified_code]


@pytest.mark.asyncio
async def test_formal_verification_marks_successful_formalize_verified():
    verified_code = "theorem true_claim : True := by trivial"
    evidence_id = _formal_id("formalize", "Prove True.", verified_code)
    llm = FakeLLM([
        json.dumps({
            "thought": "Generate and verify the formal proof.",
            "action": {"name": "formalize", "args": {"statement": "True"}},
        }),
        _conclude("True follows by trivial.", evidence_id),
    ])
    config = AgentConfig(max_react_steps=2, reviewers_enabled=[], planning_enabled=False)
    agent = ReActAgent(llm=llm, critic_llm=llm, config=config)

    async def pass_formalize(action, trace):
        assert action.name == "formalize"
        return ToolObservation(
            success=True,
            output="Lean verification: PASSED",
            lean_code=verified_code,
        )

    agent._execute_action = pass_formalize

    solution = await agent.solve(
        "Prove True.",
        require_formal_verification=True,
    )

    assert solution.verification_status == "verified"
    assert solution.verification_issues == []
    assert solution.lean_proofs == [verified_code]


@pytest.mark.asyncio
async def test_formal_verification_is_blocked_when_lean_is_unavailable():
    code = "theorem true_claim : True := by trivial"
    evidence_id = _formal_id("lean_check", "Prove True.", code)
    llm = FakeLLM([
        json.dumps({
            "thought": "Check the proof.",
            "action": {
                "name": "lean_check",
                "args": {"code": code},
            },
        }),
        _conclude("True follows by trivial.", evidence_id),
    ])
    config = AgentConfig(max_react_steps=2, reviewers_enabled=[], planning_enabled=False)
    agent = ReActAgent(llm=llm, critic_llm=llm, config=config)

    solution = await agent.solve(
        "Prove True.",
        require_formal_verification=True,
    )

    assert solution.verification_status == "blocked"
    assert any("unavailable" in issue.lower() for issue in solution.verification_issues)
    assert solution.lean_proofs == []


@pytest.mark.asyncio
async def test_formal_verification_is_blocked_when_formalize_is_unavailable():
    evidence_id = _formal_id("formalize", "Prove True.", "True")
    llm = FakeLLM([
        json.dumps({
            "thought": "Generate and verify the proof.",
            "action": {"name": "formalize", "args": {"statement": "True"}},
        }),
        _conclude("True follows by trivial.", evidence_id),
    ])
    config = AgentConfig(max_react_steps=2, reviewers_enabled=[], planning_enabled=False)
    agent = ReActAgent(llm=llm, critic_llm=llm, config=config)

    solution = await agent.solve(
        "Prove True.",
        require_formal_verification=True,
    )

    assert solution.verification_status == "blocked"
    assert any("unavailable" in issue.lower() for issue in solution.verification_issues)
    assert solution.lean_proofs == []


@pytest.mark.asyncio
async def test_react_agent_tool_exception_is_caught():
    async def failing_execute(action, ctx):
        raise RuntimeError("tool blew up")

    llm = FakeLLM([
        '{"thought": "Try a tool.", "action": {"name": "compute", "args": {"code": "1/0"}}}',
        _conclude("best effort"),
    ])
    critic_llm = FakeLLM(["VERDICT: PASS\nISSUES: none\nSUGGESTIONS: none\nCONFIDENCE: 1.0"])
    config = AgentConfig(max_react_steps=5, reviewers_enabled=[], planning_enabled=False)
    agent = ReActAgent(llm=llm, critic_llm=critic_llm, config=config)
    agent.tools.execute_action = failing_execute
    solution = await agent.solve("Trigger a tool failure")
    compute_turn = next(t for t in solution.turns if t.action.name == "compute")
    assert not compute_turn.observation.success
    assert "tool blew up" in compute_turn.observation.output


@pytest.mark.asyncio
async def test_react_agent_parse_repair_succeeds():
    llm = FakeLLM([
        "not valid json",
        '{"thought": "Repaired.", "action": {"name": "conclude", "args": {"answer": "repaired answer"}}}',
    ])
    critic_llm = FakeLLM(["VERDICT: PASS\nISSUES: none\nSUGGESTIONS: none\nCONFIDENCE: 1.0"])
    config = AgentConfig(max_react_steps=5, reviewers_enabled=[], planning_enabled=False)
    agent = ReActAgent(llm=llm, critic_llm=critic_llm, config=config)
    solution = await agent.solve("Test repair")
    assert solution.final_answer == "repaired answer"


@pytest.mark.asyncio
async def test_react_agent_loop_exhaustion_synthesizes_final_answer():
    # On step-budget exhaustion the agent must synthesize a real answer,
    # not return the raw last thought fragment.
    llm = FakeLLM([
        '{"thought": "First thought.", "action": {"name": "think", "args": {"text": "thinking"}}}',
        '{"thought": "Final thought.", "action": {"name": "think", "args": {"text": "more thinking"}}}',
        "Synthesized: the answer follows from the reasoning above.",
    ])
    critic_llm = FakeLLM(["VERDICT: PASS\nISSUES: none\nSUGGESTIONS: none\nCONFIDENCE: 1.0"])
    config = AgentConfig(max_react_steps=2, reviewers_enabled=[], planning_enabled=False)
    agent = ReActAgent(llm=llm, critic_llm=critic_llm, config=config)
    solution = await agent.solve("No conclusion")
    assert solution.final_answer == "Synthesized: the answer follows from the reasoning above."
    # It must not be the raw last thought fragment.
    assert solution.final_answer != "Final thought."
    assert len(solution.turns) == 2


@pytest.mark.asyncio
async def test_tool_budget_exhaustion_synthesizes_final_answer():
    # When the tool-call budget runs out, the consumed work must still be
    # summarized into a real final answer instead of a bare status blurb.
    llm = FakeLLM([
        '{"thought": "Compute first.", "action": {"name": "compute", "args": {"code": "print(1)"}}}',
        '{"thought": "Compute more.", "action": {"name": "compute", "args": {"code": "print(2)"}}}',
        "Synthesized partial result from the gathered work.",
    ])
    config = AgentConfig(
        max_react_steps=5,
        max_tool_calls=1,
        reviewers_enabled=[],
        planning_enabled=False,
    )
    agent = ReActAgent(llm=llm, critic_llm=llm, config=config)

    solution = await agent.solve("Gather and summarize")

    assert solution.final_answer == "Synthesized partial result from the gathered work."
    assert any(
        "Tool-call budget exhausted" in issue
        for issue in solution.verification_issues
    )


@pytest.mark.asyncio
async def test_react_agent_exhaustion_returns_nonempty_solution_without_terminal_event():
    # If synthesis yields nothing usable, the returned solution must still carry
    # a clear message. The outer solve session owns the terminal client event.
    llm = FakeLLM([
        '{"thought": "t1", "action": {"name": "think", "args": {"text": "x"}}}',
        '{"thought": "t2", "action": {"name": "think", "args": {"text": "y"}}}',
        "   ",  # empty/whitespace synthesis
    ])
    critic_llm = FakeLLM(["VERDICT: PASS\nISSUES: none\nSUGGESTIONS: none\nCONFIDENCE: 1.0"])
    config = AgentConfig(max_react_steps=2, reviewers_enabled=[], planning_enabled=False)
    agent = ReActAgent(llm=llm, critic_llm=critic_llm, config=config)
    events = []

    async def on_event(event):
        events.append(event)

    solution = await agent.solve("No conclusion", on_event=on_event)
    assert solution.final_answer.strip() != ""
    assert all(event["type"] != "done" for event in events)


@pytest.mark.asyncio
async def test_react_agent_emits_heartbeat_during_slow_tool():
    # A tool that runs longer than tool_heartbeat_seconds must produce
    # tool_progress events so the client connection stays alive.
    llm = FakeLLM([
        '{"thought": "Calculate.", "action": {"name": "compute", "args": {"code": "2+2"}}}',
        _conclude("4"),
    ])
    critic_llm = FakeLLM(['{"difficulty": "easy", "reason": "trivial arithmetic"}'])
    config = AgentConfig(max_react_steps=5, reviewers_enabled=[], tool_heartbeat_seconds=0.05)
    agent = ReActAgent(llm=llm, critic_llm=critic_llm, config=config)

    original_execute = agent.tools.execute_action

    async def slow_execute(action, ctx):
        if action.name == "compute":
            await asyncio.sleep(0.2)
        return await original_execute(action, ctx)

    agent.tools.execute_action = slow_execute

    events = []

    async def on_event(event):
        events.append(event)

    solution = await agent.solve("What is 2+2?", on_event=on_event)
    assert solution.final_answer == "4"
    heartbeats = [e for e in events if e["type"] == "tool_progress"]
    assert len(heartbeats) >= 1
    assert heartbeats[0]["tool"] == "compute"
    assert heartbeats[0]["elapsed_seconds"] >= 0


@pytest.mark.asyncio
async def test_react_agent_reviewers_run_only_for_conclusions(monkeypatch):
    llm = FakeLLM([
        '{"difficulty": "easy", "reason": "trivial arithmetic"}',
        '{"thought": "Calculate.", "action": {"name": "compute", "args": {"code": "3 * 3"}}}',
        '{"thought": "Check context.", "action": {"name": "search", "args": {"query": "nine"}}}',
        _conclude("9"),
    ])
    agent = ReActAgent(
        llm=llm,
        critic_llm=llm,
        config=AgentConfig(
            max_react_steps=5,
            reviewers_enabled=[],
            skip_review_on_easy_prompt=False,
            skip_review_min_confidence=1.01,
        ),
    )
    reviewer_spies = []
    for name in ("critic", "fidelity", "formal"):
        review = AsyncMock(
            return_value=ReviewResult(
                reviewer=name,
                verdict="PASS",
                confidence=1.0,
            )
        )
        reviewer_spies.append(SimpleNamespace(name=name, review=review))
    agent.reviewers = reviewer_spies
    execute = AsyncMock(
        return_value=ToolObservation(success=True, output="tool output")
    )
    monkeypatch.setattr(agent, "_execute_with_heartbeat", execute)

    solution = await agent.solve("What is 3 * 3?")

    assert [turn.action.name for turn in solution.turns] == [
        "compute",
        "search",
        "conclude",
    ]
    assert solution.turns[0].reviews == []
    assert solution.turns[1].reviews == []
    assert [review.reviewer for review in solution.turns[2].reviews] == [
        "critic",
        "fidelity",
        "formal",
    ]
    for reviewer in reviewer_spies:
        reviewer.review.assert_awaited_once()
        reviewed_turn = reviewer.review.await_args.args[0]
        assert reviewed_turn.action.name == "conclude"


@pytest.mark.asyncio
async def test_unknown_action_is_rejected_before_tool_execution(monkeypatch):
    llm = FakeLLM([
        _action("invented_tool", {"query": "secret payload"}),
        _conclude("best effort"),
    ])
    agent = ReActAgent(
        llm=llm,
        critic_llm=llm,
        config=AgentConfig(max_react_steps=2, reviewers_enabled=[], planning_enabled=False),
    )
    execute = AsyncMock(
        return_value=ToolObservation(success=True, output="must not run")
    )
    monkeypatch.setattr(agent, "_execute_with_heartbeat", execute)

    solution = await agent.solve("Reject unknown actions")

    execute.assert_not_awaited()
    assert solution.turns[0].observation.success is False
    assert solution.turns[0].observation.error == "unknown_action"
    assert solution.final_answer == "best effort"


@pytest.mark.parametrize(
    ("action_name", "required_arg"),
    [
        ("conclude", "answer"),
        ("set_goal", "goal"),
        ("think", "text"),
        ("compute", "code"),
        ("search", "query"),
        ("fetch_url", "url"),
        ("searching", "query"),
        ("read_sources", "prompt"),
        ("add_material", "text"),
        ("search_materials", "query"),
        ("search_knowledge", "query"),
        ("relate_knowledge", "spec"),
        ("find_related", "item_id"),
        ("search_mathlib", "query"),
        ("formalize", "statement"),
        ("lean_check", "code"),
    ],
)
@pytest.mark.asyncio
async def test_action_requires_nonempty_string_argument_before_execution(
    monkeypatch,
    action_name,
    required_arg,
):
    llm = FakeLLM([
        _action(action_name, {required_arg: "   "}),
        _conclude("recovered"),
    ])
    agent = ReActAgent(
        llm=llm,
        critic_llm=llm,
        config=AgentConfig(max_react_steps=2, reviewers_enabled=[], planning_enabled=False),
    )
    execute = AsyncMock(
        return_value=ToolObservation(success=True, output="must not run")
    )
    monkeypatch.setattr(agent, "_execute_with_heartbeat", execute)

    solution = await agent.solve("Validate action arguments")

    execute.assert_not_awaited()
    assert solution.turns[0].observation.success is False
    assert solution.turns[0].observation.error == "invalid_action_args"
    assert required_arg in solution.turns[0].observation.output
    assert solution.final_answer == "recovered"


@pytest.mark.asyncio
async def test_action_rejects_non_string_required_argument(monkeypatch):
    llm = FakeLLM([
        _action("compute", {"code": 123}),
        _conclude("recovered"),
    ])
    agent = ReActAgent(
        llm=llm,
        critic_llm=llm,
        config=AgentConfig(max_react_steps=2, reviewers_enabled=[], planning_enabled=False),
    )
    execute = AsyncMock()
    monkeypatch.setattr(agent, "_execute_with_heartbeat", execute)

    solution = await agent.solve("Validate action argument types")

    execute.assert_not_awaited()
    assert solution.turns[0].observation.error == "invalid_action_args"
    assert solution.final_answer == "recovered"


@pytest.mark.asyncio
async def test_third_consecutive_identical_action_stops_before_execution(monkeypatch):
    repeated = _action("compute", {"code": "6 * 7"}, thought="Try again.")
    llm = FakeLLM([
        '{"difficulty": "easy", "reason": "trivial arithmetic"}',
        repeated,
        repeated,
        repeated,
        _conclude("must not run"),
    ])
    agent = ReActAgent(
        llm=llm,
        critic_llm=llm,
        config=AgentConfig(max_react_steps=4, reviewers_enabled=[]),
    )
    execute = AsyncMock(
        return_value=ToolObservation(success=True, output="42")
    )
    monkeypatch.setattr(agent, "_execute_with_heartbeat", execute)

    solution = await agent.solve("Compute 6 * 7")

    assert execute.await_count == 2
    assert llm.calls == 4  # difficulty classification + three streamed actions
    assert [turn.action.name for turn in solution.turns] == [
        "compute",
        "compute",
        "compute",
    ]
    assert solution.turns[-1].observation.error == "identical_action_limit"
    assert solution.trace.budget_consumption["tool_calls"] == 2
    assert any("identical action" in issue.lower() for issue in solution.verification_issues)


@pytest.mark.parametrize(
    "actions",
    [
        [
            _action("search", {"query": "same theorem"}),
            _action("search_web", {"query": "same theorem"}),
            _action("search", {"query": "same theorem"}),
        ],
        [
            _action("search", {"query": "same theorem"}),
            _action("search", {"query": "same theorem", "extra": "ignored"}),
            _action("search", {"query": "same theorem", "other": "also ignored"}),
        ],
    ],
    ids=["aliases", "irrelevant-extra-keys"],
)
@pytest.mark.asyncio
async def test_repeat_limit_fingerprints_effective_action_schema(
    monkeypatch,
    actions,
):
    llm = FakeLLM(actions + [_conclude("must not run")])
    agent = ReActAgent(
        llm=llm,
        critic_llm=llm,
        config=AgentConfig(max_react_steps=4, reviewers_enabled=[], planning_enabled=False),
    )
    execute = AsyncMock(
        return_value=ToolObservation(success=True, output="search result")
    )
    monkeypatch.setattr(agent, "_execute_with_heartbeat", execute)

    solution = await agent.solve("Find the same theorem")

    assert execute.await_count == 2
    assert llm.calls == 3
    assert solution.turns[-1].observation.error == "identical_action_limit"
    assert solution.trace.budget_consumption["tool_calls"] == 2


@pytest.mark.asyncio
async def test_thirteenth_tool_action_does_not_execute(monkeypatch):
    tool_actions = [
        _action("compute", {"code": str(index)}, thought=f"Call {index}.")
        for index in range(13)
    ]
    llm = FakeLLM(tool_actions + [_conclude("must not run")])
    agent = ReActAgent(
        llm=llm,
        # Empty critic queue: context-compaction calls fail and degrade to
        # dropping old turns, keeping the action queue untouched.
        critic_llm=FakeLLM([]),
        config=AgentConfig(max_react_steps=14, max_tool_calls=12, reviewers_enabled=[], planning_enabled=False),
    )
    execute = AsyncMock(
        return_value=ToolObservation(success=True, output="ok")
    )
    monkeypatch.setattr(agent, "_execute_with_heartbeat", execute)

    solution = await agent.solve("Use bounded tools")

    assert execute.await_count == 12
    # 13 action generations (the 13th is budget-blocked) + 1 final-answer
    # synthesis now that tool-budget exhaustion still summarizes the trace.
    assert llm.calls == 14
    assert solution.turns[-1].observation.error == "tool_call_budget_exhausted"
    assert solution.trace.budget_consumption["tool_calls"] == 12


@pytest.mark.asyncio
async def test_resume_preserves_consumed_tool_call_budget(monkeypatch):
    initial_trace = ReActTrace(
        problem="Use bounded tools",
        current_goal="finish",
        budget_consumption={"tool_calls": 12},
    )
    llm = FakeLLM([
        _action("compute", {"code": "13"}),
        _conclude("must not run"),
    ])
    agent = ReActAgent(
        llm=llm,
        critic_llm=llm,
        config=AgentConfig(max_react_steps=2, reviewers_enabled=[], planning_enabled=False),
    )
    execute = AsyncMock(
        return_value=ToolObservation(success=True, output="must not run")
    )
    monkeypatch.setattr(agent, "_execute_with_heartbeat", execute)

    solution = await agent.solve("Use bounded tools", initial_trace=initial_trace)

    execute.assert_not_awaited()
    # 1 budget-blocked action generation + 1 final-answer synthesis.
    assert llm.calls == 2
    assert solution.trace.budget_consumption["tool_calls"] == 12
    assert solution.turns[-1].observation.error == "tool_call_budget_exhausted"


@pytest.mark.parametrize("slow_phase", ["generation", "tool", "reviewer"])
@pytest.mark.asyncio
async def test_total_wall_time_exhaustion_returns_best_effort(
    monkeypatch,
    slow_phase,
):
    responses = (
        [_action("compute", {"code": "2 + 2"}), _conclude("4")]
        if slow_phase == "tool"
        else [_conclude("candidate answer")]
    )
    llm = FakeLLM(responses)
    agent = ReActAgent(
        llm=llm,
        critic_llm=llm,
        config=AgentConfig(
            max_react_steps=2,
            max_wall_seconds=0.01,
            reviewers_enabled=[],
            planning_enabled=False,
        ),
    )

    async def wait_then_generate(*_args, **_kwargs):
        await asyncio.sleep(0.05)
        return _conclude("late answer")

    async def wait_then_execute(*_args, **_kwargs):
        await asyncio.sleep(0.05)
        return ToolObservation(success=True, output="late output")

    if slow_phase == "generation":
        monkeypatch.setattr(agent, "_generate_action", wait_then_generate)
    elif slow_phase == "tool":
        monkeypatch.setattr(agent, "_execute_with_heartbeat", wait_then_execute)
    else:
        async def wait_then_review(*_args, **_kwargs):
            await asyncio.sleep(0.05)
            return ReviewResult(reviewer="critic", verdict="PASS")

        agent.reviewers = [
            SimpleNamespace(name="critic", review=wait_then_review)
        ]

    solution = await agent.solve("Respect the total wall budget")

    assert solution.verification_status == "best_effort"
    assert any("wall-time budget" in issue.lower() for issue in solution.verification_issues)


@pytest.mark.asyncio
async def test_phase_duration_logs_exclude_prompt_arguments_and_secrets(caplog):
    secret = "sk-sensitive-provider-secret"
    answer = "sensitive candidate answer"
    llm = FakeLLM([_conclude(answer)])
    critic_llm = FakeLLM([
        "VERDICT: PASS\nISSUES: none\nSUGGESTIONS: none\nCONFIDENCE: 1.0"
    ])
    agent = ReActAgent(
        llm=llm,
        critic_llm=critic_llm,
        config=AgentConfig(max_react_steps=1, reviewers_enabled=["critic"], planning_enabled=False),
    )
    caplog.set_level("INFO", logger="math_agent.agent")

    await agent.solve(f"Do not log {secret}")

    duration_logs = [
        record.getMessage()
        for record in caplog.records
        if record.getMessage().startswith("phase_duration ")
    ]
    assert any(
        "phase=model_generation model_role=main" in message
        for message in duration_logs
    )
    assert any(
        "phase=reviewer_panel model_role=critic" in message
        for message in duration_logs
    )
    assert all(secret not in message for message in duration_logs)
    assert all(answer not in message for message in duration_logs)


@pytest.mark.asyncio
async def test_react_agent_uses_tool_then_concludes():
    llm = FakeLLM([
        '{"thought": "Compute.", "action": {"name": "compute", "args": {"code": "2+2"}}}',
        '{"thought": "Done.", "action": {"name": "conclude", "args": {"answer": "4"}}}',
    ])
    critic_llm = FakeLLM(['{"difficulty": "easy", "reason": "trivial arithmetic"}'])
    config = AgentConfig(max_react_steps=5, reviewers_enabled=["critic"])
    agent = ReActAgent(llm=llm, critic_llm=critic_llm, config=config)
    solution = await agent.solve("What is 2+2?")
    assert solution.final_answer == "4"
    assert any(t.action.name == "compute" for t in solution.turns)


@pytest.mark.asyncio
async def test_react_agent_emits_generation_and_tool_progress():
    llm = SplitOnlyLLM([
        '{"thought": "Calculate.", "action": {"name": "compute", "args": {"code": "2+2"}}}',
        _conclude("4"),
    ])
    config = AgentConfig(max_react_steps=5, reviewers_enabled=[])
    agent = ReActAgent(llm=llm, critic_llm=llm, config=config)
    events = []

    async def on_event(event):
        events.append(event)

    solution = await agent.solve("What is 2+2?", on_event=on_event)

    assert solution.final_answer == "4"
    assert any(e["type"] == "llm_start" for e in events)
    assert any(e["type"] == "token" for e in events)
    assert any(e["type"] == "tool_start" and e["tool"] == "compute" for e in events)
    assert any(e["type"] == "tool_done" and e["tool"] == "compute" and e["success"] for e in events)
    compute_step = next(e for e in events if e.get("type") == "step" and e.get("action") == "compute")
    assert compute_step["verified"] is None


@pytest.mark.asyncio
async def test_react_agent_keeps_long_conclude_observation_intact():
    long_answer = (
        "设三角形三角为 A,B,C。"
        + ("推导过程。" * 80)
        + r"于是\[\frac{HD}{AD}+\frac{HE}{BE}+\frac{HF}{CF}"
        r"=cot B cot C + cot C cot A + cot A cot B.\]"
    )
    assert len(f"Conclusion: {long_answer}") > 500
    llm = FakeLLM([_conclude(long_answer)])
    critic_llm = FakeLLM([
        '{"difficulty": "hard", "reason": "proof required"}',
        "VERDICT: FAIL\nISSUES: incomplete identity application\nSUGGESTIONS: finish the sum\nCONFIDENCE: 0.7",
    ])
    config = AgentConfig(
        max_react_steps=5,
        max_conclusion_revisions=0,
        reviewers_enabled=["critic"],
        planning_enabled=False,
    )
    agent = ReActAgent(llm=llm, critic_llm=critic_llm, config=config)
    events = []

    async def on_event(event):
        events.append(event)

    solution = await agent.solve("Prove the cotangent identity.", on_event=on_event)

    assert solution.final_answer == long_answer
    conclude_steps = [e for e in events if e.get("type") == "step" and e.get("action") == "conclude"]
    assert conclude_steps
    final_step = conclude_steps[-1]
    assert "\\frac{HF}{CF}" in final_step["observation"]
    assert final_step["observation"].endswith("cot A cot B.\\]")
    assert final_step["verified"] is False
    assert final_step["reviews"]
    assert final_step["reviews"][0]["issues"]



@pytest.mark.asyncio
async def test_react_agent_calls_consolidator_when_enabled():
    llm = FakeLLM([
        '{"thought": "done", "action": {"name": "conclude", "args": {"answer": "4"}}}'
    ])
    critic_llm = FakeLLM(['{"difficulty": "easy", "reason": "trivial arithmetic"}'])
    config = AgentConfig(max_react_steps=5, reviewers_enabled=[], memory_consolidation_enabled=True)
    consolidator = StubConsolidator()
    agent = ReActAgent(
        llm=llm,
        critic_llm=critic_llm,
        config=config,
        consolidator=consolidator,
    )
    solution = await agent.solve("What is 2+2?")
    assert solution.final_answer == "4"
    assert len(consolidator.calls) == 1
    assert consolidator.calls[0] == ("What is 2+2?", "4")


@pytest.mark.asyncio
async def test_react_agent_skips_consolidator_when_disabled():
    llm = FakeLLM([
        '{"thought": "done", "action": {"name": "conclude", "args": {"answer": "4"}}}'
    ])
    critic_llm = FakeLLM(['{"difficulty": "easy", "reason": "trivial arithmetic"}'])
    config = AgentConfig(max_react_steps=5, reviewers_enabled=[], memory_consolidation_enabled=False)
    consolidator = StubConsolidator()
    agent = ReActAgent(
        llm=llm,
        critic_llm=critic_llm,
        config=config,
        consolidator=consolidator,
    )
    await agent.solve("What is 2+2?")
    assert len(consolidator.calls) == 0


@pytest.mark.asyncio
async def test_react_agent_calls_consolidator_on_failed_solve():
    llm = FakeLLM([
        '{"thought": "First thought.", "action": {"name": "think", "args": {"text": "thinking"}}}',
        '{"thought": "Final thought.", "action": {"name": "think", "args": {"text": "more thinking"}}}',
    ])
    critic_llm = FakeLLM(["VERDICT: PASS\nISSUES: none\nSUGGESTIONS: none\nCONFIDENCE: 1.0"])
    config = AgentConfig(max_react_steps=2, reviewers_enabled=[], memory_consolidation_enabled=True, planning_enabled=False)
    consolidator = StubConsolidator()
    agent = ReActAgent(
        llm=llm,
        critic_llm=critic_llm,
        config=config,
        consolidator=consolidator,
    )
    await agent.solve("No conclusion")
    assert len(consolidator.calls) == 1
    assert consolidator.calls[0][0] == "No conclusion"


@pytest.mark.asyncio
async def test_react_agent_isolates_consolidator_failure():
    llm = FakeLLM([
        '{"thought": "done", "action": {"name": "conclude", "args": {"answer": "4"}}}'
    ])
    critic_llm = FakeLLM(['{"difficulty": "easy", "reason": "trivial arithmetic"}'])
    config = AgentConfig(max_react_steps=5, reviewers_enabled=[], memory_consolidation_enabled=True)
    agent = ReActAgent(
        llm=llm,
        critic_llm=critic_llm,
        config=config,
        consolidator=FailingConsolidator(),
    )
    solution = await agent.solve("What is 2+2?")
    assert solution.final_answer == "4"


@pytest.mark.asyncio
async def test_reviewer_exception_fails_closed_instead_of_marking_answer_reviewed():
    class BrokenReviewer:
        name = "critic"

        async def review(self, turn, trace):
            raise RuntimeError("review service unavailable")

    llm = FakeLLM([_conclude("An unchecked answer")])
    agent = ReActAgent(
        llm=llm,
        critic_llm=llm,
        config=AgentConfig(
            max_react_steps=1,
            max_conclusion_revisions=0,
            reviewers_enabled=[],
            planning_enabled=False,
        ),
    )
    agent.reviewers = [BrokenReviewer()]

    solution = await agent.solve("Require a real review")

    # A crashed reviewer abstains instead of fabricating a FAIL: the answer
    # passes as unreviewed rather than being blocked or marked reviewed.
    assert solution.verification_status == "unreviewed"
    assert solution.verification_issues == []
    conclude_turn = next(
        turn for turn in solution.turns if turn.action.name == "conclude"
    )
    assert [review.verdict for review in conclude_turn.reviews] == ["UNAVAILABLE"]
    assert conclude_turn.reviews[0].issues == []


@pytest.mark.asyncio
async def test_partial_reviewer_outage_still_marks_answer_reviewed():
    from math_agent.agent.react_state import ReviewResult

    class BrokenReviewer:
        name = "completeness"

        async def review(self, turn, trace):
            raise RuntimeError("review service unavailable")

    class PassingReviewer:
        name = "critic"

        async def review(self, turn, trace):
            return ReviewResult(reviewer="critic", verdict="PASS", confidence=0.9)

    llm = FakeLLM([_conclude("A reviewed answer")])
    agent = ReActAgent(
        llm=llm,
        critic_llm=llm,
        config=AgentConfig(
            max_react_steps=1,
            reviewers_enabled=[],
            planning_enabled=False,
        ),
    )
    agent.reviewers = [BrokenReviewer(), PassingReviewer()]

    solution = await agent.solve("Require a real review")

    # The surviving reviewer's vote decides; the abstained one is ignored.
    assert solution.verification_status == "reviewed"
    conclude_turn = next(
        turn for turn in solution.turns if turn.action.name == "conclude"
    )
    assert [review.verdict for review in conclude_turn.reviews] == [
        "UNAVAILABLE",
        "PASS",
    ]


@pytest.mark.asyncio
async def test_context_compaction_summarizes_dropped_turns():
    think_actions = [
        _action("think", {"text": f"reasoning {index}"}, thought=f"Step {index}.")
        for index in range(11)
    ]
    llm = FakeLLM(think_actions + [_conclude("done after many steps")])
    critic_llm = FakeLLM(["Summary of the first ten steps."])
    agent = ReActAgent(
        llm=llm,
        critic_llm=critic_llm,
        config=AgentConfig(
            max_react_steps=12,
            reviewers_enabled=[],
            planning_enabled=False,
        ),
    )

    solution = await agent.solve("Long proof")

    assert solution.final_answer == "done after many steps"
    assert solution.trace is not None
    assert solution.trace.compacted_summary == "Summary of the first ten steps."
    assert solution.trace.compacted_turn_count == 1
    assert "Earlier work summary:" in solution.trace.context_window()


@pytest.mark.asyncio
async def test_context_compaction_failure_degrades_to_plain_truncation():
    think_actions = [
        _action("think", {"text": f"reasoning {index}"}, thought=f"Step {index}.")
        for index in range(11)
    ]
    llm = FakeLLM(think_actions + [_conclude("done after many steps")])
    # Empty critic queue: the compaction call fails and must not affect the solve.
    critic_llm = FakeLLM([])
    agent = ReActAgent(
        llm=llm,
        critic_llm=critic_llm,
        config=AgentConfig(
            max_react_steps=12,
            reviewers_enabled=[],
            planning_enabled=False,
        ),
    )

    solution = await agent.solve("Long proof")

    assert solution.final_answer == "done after many steps"
    assert solution.trace is not None
    assert solution.trace.compacted_summary == ""
    assert solution.trace.compacted_turn_count == 1


@pytest.mark.asyncio
async def test_llm_reviewers_retry_with_main_backend_after_critic_provider_denial():
    class DeniedLLM:
        async def complete(self, *args, **kwargs):
            raise PermissionError("critic provider denied the request")

    reviewed = "VERDICT: PASS\nISSUES: none\nSUGGESTIONS: none\nCONFIDENCE: 1.0"
    main_llm = FakeLLM([reviewed, reviewed])
    denied_llm = DeniedLLM()
    agent = ReActAgent(
        llm=main_llm,
        critic_llm=denied_llm,
        config=AgentConfig(reviewers_enabled=[]),
    )
    agent.reviewers = [
        CriticReviewer(denied_llm),
        StatementFidelityReviewer(denied_llm),
    ]
    trace = ReActTrace(problem="Prove P", current_goal="Prove P")
    turn = ReActTurn(
        thought="The proof is complete.",
        action=Action(name="conclude", args={"answer": "P follows."}),
        observation=ToolObservation(success=True, output="Conclusion: P follows."),
        step_num=1,
    )

    reviews = await agent._run_reviewers(turn, trace, __import__("logging").getLogger("test"))

    assert [review.verdict for review in reviews] == ["PASS", "PASS"]
    assert main_llm.calls == 2


@pytest.mark.asyncio
async def test_sublemma_evidence_cannot_conclude_main_problem_as_verified():
    """A sub-lemma's formal evidence must not verify the main problem."""
    problem = "Prove the main theorem."
    sub_lemma = "Prove the helper lemma."
    sub_code = "theorem helper : True := by trivial"
    sub_evidence_id = _formal_id("lean_check", sub_lemma, sub_code)
    llm = FakeLLM([
        _action("set_goal", {"goal": sub_lemma}),
        _action("lean_check", {"code": sub_code}),
        _action("set_goal", {"goal": problem}),
        _conclude("The main theorem holds.", sub_evidence_id),
    ])
    config = AgentConfig(max_react_steps=4, reviewers_enabled=[], planning_enabled=False)
    agent = ReActAgent(llm=llm, critic_llm=llm, config=config)

    async def pass_lean_check(action, trace):
        if action.name == "lean_check":
            return ToolObservation(
                success=True,
                output="Lean verification: PASSED",
                lean_code=action.args["code"],
            )
        return await ReActAgent._execute_action(agent, action, trace)

    agent._execute_action = pass_lean_check

    solution = await agent.solve(problem, require_formal_verification=True)

    assert solution.verification_status != "verified"
    assert solution.lean_proofs == []
    assert any(
        "bound" in issue.lower() or "claim" in issue.lower()
        for issue in solution.verification_issues
    )


@pytest.mark.asyncio
async def test_solution_carries_matching_outcome():
    from math_agent.agent.verification import legacy_label

    # 复用本文件既有的 fast/skip-review 夹具构造一个 unreviewed 结果。
    solution = await _run_minimal_unreviewed_solution()
    assert solution.verification_status == "unreviewed"
    assert solution.verification_outcome is not None
    assert legacy_label(solution.verification_outcome) == "unreviewed"
