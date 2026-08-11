import pytest

from math_agent.agent.react_state import Action, ReActTrace, ReActTurn, ToolObservation
from math_agent.agent.reviewers import StatementFidelityReviewer
from math_agent.billing.models import LLMResponse
from math_agent.llm.base import LLMBackend


class FakeLLM(LLMBackend):
    def __init__(self, response: str):
        self.response = response
        self.calls = []

    async def complete(self, messages, *, system=None, temperature=None, response_format=None):
        self.calls.append({"messages": messages, "system": system})
        return LLMResponse(
            text=self.response,
            prompt_tokens=0,
            completion_tokens=0,
            total_tokens=0,
        )

    async def stream(self, messages, *, system=None, temperature=None, response_format=None):
        yield LLMResponse(
            text=self.response,
            prompt_tokens=0,
            completion_tokens=0,
            total_tokens=0,
        )


@pytest.mark.asyncio
async def test_fidelity_pass():
    reviewer = StatementFidelityReviewer(llm=FakeLLM("VERDICT: PASS\nISSUES: none\nSUGGESTIONS: none\nCONFIDENCE: 0.95"))
    trace = ReActTrace(problem="Prove sqrt(2) is irrational")
    turn = ReActTurn(
        thought="...",
        action=Action(name="conclude", args={"answer": "Irrational (Real.sqrt 2)"}),
        observation=ToolObservation(success=True, output=""),
        step_num=1,
    )
    result = await reviewer.review(turn, trace)
    assert result.verdict == "PASS"


@pytest.mark.asyncio
async def test_fidelity_fail():
    reviewer = StatementFidelityReviewer(llm=FakeLLM("VERDICT: FAIL\nISSUES: missing irrational conclusion\nSUGGESTIONS: use Irrational\nCONFIDENCE: 0.9"))
    trace = ReActTrace(problem="Prove sqrt(2) is irrational")
    turn = ReActTurn(
        thought="...",
        action=Action(name="conclude", args={"answer": "Real.sqrt 2 > 0"}),
        observation=ToolObservation(success=True, output=""),
        step_num=1,
    )
    result = await reviewer.review(turn, trace)
    assert result.verdict == "FAIL"
    assert "missing" in result.issues[0].lower()


@pytest.mark.asyncio
async def test_prose_fidelity_does_not_demand_lean_formalization():
    llm = FakeLLM(
        "VERDICT: PASS\nISSUES: none\nSUGGESTIONS: none\nCONFIDENCE: 0.99"
    )
    reviewer = StatementFidelityReviewer(llm=llm)
    problem = "从 1 到 2n 中任取 n+1 个数，证明其中必有两个数互质。"
    trace = ReActTrace(problem=problem, current_goal=problem)
    turn = ReActTurn(
        thought="Use adjacent pairs and pigeonhole principle.",
        action=Action(
            name="conclude",
            args={"answer": "分成 n 对相邻整数；必有一对同时被选中，因此互质。"},
        ),
        observation=ToolObservation(success=True, output="Conclusion"),
        step_num=1,
    )

    result = await reviewer.review(turn, trace)

    call = llm.calls[0]
    prompt = call["messages"][0].content
    assert result.verdict == "PASS"
    assert "semantic fidelity gate" in call["system"]
    assert "This is NOT a Lean formalization review" in call["system"]
    assert "Current formal goal" not in prompt
    assert "materially different claim" in prompt


@pytest.mark.asyncio
async def test_formal_fidelity_uses_lean_specific_protocol_when_artifact_exists():
    llm = FakeLLM(
        "VERDICT: PASS\nISSUES: none\nSUGGESTIONS: none\nCONFIDENCE: 0.99"
    )
    reviewer = StatementFidelityReviewer(llm=llm)
    trace = ReActTrace(problem="Prove P")
    turn = ReActTurn(
        thought="Verified.",
        action=Action(name="conclude", args={"answer": "P"}),
        observation=ToolObservation(
            success=True,
            output="Conclusion: P",
            lean_code="theorem p : True := by trivial",
        ),
        step_num=1,
    )

    await reviewer.review(turn, trace)

    call = llm.calls[0]
    assert "formalization fidelity gate" in call["system"]
    assert "Lean formalization:" in call["messages"][0].content
