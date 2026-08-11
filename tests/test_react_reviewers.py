import asyncio

import pytest

from math_agent.agent.react_state import (
    Action,
    ProjectContext,
    ReActTrace,
    ReActTurn,
    ToolObservation,
)
from math_agent.agent.reviewers import (
    CompletenessReviewer,
    CriticReviewer,
    FormalReviewer,
    KnowledgeReviewer,
    _extract_confidence,
    _extract_list,
    _parse_critic_response,
)
from math_agent.agent.react_agent import ReActAgent
from math_agent.config import AgentConfig
from math_agent.llm.base import LLMBackend
from math_agent.billing.models import LLMResponse


class FakeLLM:
    def __init__(self, response: str):
        self.response = response

    async def complete(self, messages, system=None, temperature=None, response_format=None):
        return LLMResponse(
            text=self.response,
            prompt_tokens=0,
            completion_tokens=0,
            total_tokens=0,
        )

    async def stream(self, messages, system=None, temperature=None, response_format=None):
        yield LLMResponse(
            text=self.response,
            prompt_tokens=0,
            completion_tokens=0,
            total_tokens=0,
        )


class FakeLeanResult:
    def __init__(self, success, errors=None):
        self.success = success
        self.errors = errors or []


class FakeLeanCodegen:
    def __init__(self, should_formalize=True, lean_code="theorem t : True := trivial", result=None, raise_on_decide=False, raise_on_generate=False):
        self._should_formalize = should_formalize
        self._lean_code = lean_code
        self._result = result
        self._raise_on_decide = raise_on_decide
        self._raise_on_generate = raise_on_generate

    async def should_formalize(self, step, state):
        if self._raise_on_decide:
            raise RuntimeError("decision failed")
        return self._should_formalize

    async def generate_and_verify(self, step, state, timeout_seconds=None):
        if self._raise_on_generate:
            raise RuntimeError("generation failed")
        return self._lean_code, self._result


class FakeLeanCodegenWithConclude:
    def __init__(
        self,
        should_formalize=True,
        lean_code="theorem t : True := trivial",
        result=None,
        decide_delay=0.0,
        verify_delay=0.0,
    ):
        self._should_formalize = should_formalize
        self._lean_code = lean_code
        self._result = result
        self._decide_delay = decide_delay
        self._verify_delay = verify_delay

    async def should_formalize(self, step, state):
        await asyncio.sleep(self._decide_delay)
        return self._should_formalize

    async def generate_and_verify(self, step, state, timeout_seconds=None):
        if timeout_seconds is not None and self._verify_delay > timeout_seconds:
            raise asyncio.TimeoutError
        await asyncio.sleep(self._verify_delay)
        return self._lean_code, self._result


class FakeKnowledgeStore:
    def __init__(self, facts=None):
        self._facts = facts or []

    def search_facts(self, project_id, query, limit=5):
        return self._facts[:limit]


def _make_turn(thought="step", output="ok"):
    return ReActTurn(
        thought=thought,
        action=Action(name="think", args={"text": thought}),
        observation=ToolObservation(success=True, output=output),
        step_num=1,
    )


@pytest.mark.asyncio
async def test_critic_reviewer_passes_when_llm_says_pass():
    llm = FakeLLM("VERDICT: PASS\nISSUES: none\nSUGGESTIONS: none\nCONFIDENCE: 0.9")
    reviewer = CriticReviewer(llm=llm)
    turn = _make_turn()
    trace = ReActTrace(problem="P")
    result = await reviewer.review(turn, trace)
    assert result.verdict == "PASS"
    assert result.reviewer == "critic"
    assert result.confidence == pytest.approx(0.9)


@pytest.mark.asyncio
async def test_completeness_reviewer_fails_on_bare_assertions():
    llm = FakeLLM(
        "VERDICT: FAIL\n"
        "ISSUES: Assertion 1 and 2 are stated without proof\n"
        "SUGGESTIONS: Prove the matching bijection with Y\n"
        "CONFIDENCE: 0.85"
    )
    reviewer = CompletenessReviewer(llm=llm)
    turn = ReActTurn(
        thought="conclude",
        action=Action(
            name="conclude",
            args={
                "answer": (
                    "**断言1**：Y 中每个顶点都被匹配。\n"
                    "**断言2**：M 给出双射。\n"
                    "因此 d = |L|-ν。"
                )
            },
        ),
        observation=ToolObservation(success=True, output="ok"),
        step_num=1,
    )
    result = await reviewer.review(turn, ReActTrace(problem="Hall defect form"))
    assert result.reviewer == "completeness"
    assert result.verdict == "FAIL"
    assert any("Assertion" in issue or "断言" in issue for issue in result.issues)


@pytest.mark.asyncio
async def test_completeness_reviewer_passes_complete_writeup():
    llm = FakeLLM("VERDICT: PASS\nISSUES: none\nSUGGESTIONS: optional diagram\nCONFIDENCE: 0.9")
    reviewer = CompletenessReviewer(llm=llm)
    turn = ReActTurn(
        thought="conclude",
        action=Action(name="conclude", args={"answer": "Full proof with justified steps."}),
        observation=ToolObservation(success=True, output="ok"),
        step_num=1,
    )
    result = await reviewer.review(turn, ReActTrace(problem="P"))
    assert result.verdict == "PASS"
    assert result.suggestions


@pytest.mark.asyncio
async def test_completeness_reviewer_logs_completeness_phase(caplog):
    llm = FakeLLM("VERDICT: PASS\nISSUES: none\nSUGGESTIONS: none\nCONFIDENCE: 1.0")
    reviewer = CompletenessReviewer(llm=llm)
    turn = ReActTurn(
        thought="t",
        action=Action(name="conclude", args={"answer": "ok"}),
        observation=ToolObservation(success=True, output="ok"),
        step_num=1,
    )
    caplog.set_level("INFO", logger="math_agent.agent.reviewers")
    await reviewer.review(turn, ReActTrace(problem="P"))
    assert any(
        "phase_duration phase=completeness_review" in record.getMessage()
        for record in caplog.records
    )


def test_react_agent_registers_completeness_reviewer():
    class _SilentLLM(LLMBackend):
        async def complete(self, messages, system=None, temperature=None, response_format=None):
            return LLMResponse(text="", prompt_tokens=0, completion_tokens=0, total_tokens=0)

        async def stream(self, messages, system=None, temperature=None, response_format=None):
            if False:
                yield LLMResponse(text="", prompt_tokens=0, completion_tokens=0, total_tokens=0)

    agent = ReActAgent(
        llm=_SilentLLM(),
        critic_llm=_SilentLLM(),
        config=AgentConfig(
            reviewers_enabled=["critic", "fidelity", "completeness"],
            planning_enabled=False,
        ),
    )
    names = [r.name for r in agent.reviewers]
    assert "completeness" in names
    assert "critic" in names
    assert "fidelity" in names


@pytest.mark.asyncio
async def test_critic_reviewer_fails_when_llm_says_fail():
    llm = FakeLLM(
        "VERDICT: FAIL\nISSUES: gap\nSUGGESTIONS: justify\nCONFIDENCE: 0.8"
    )
    reviewer = CriticReviewer(llm=llm)
    turn = _make_turn()
    trace = ReActTrace(problem="P")
    result = await reviewer.review(turn, trace)
    assert result.verdict == "FAIL"
    assert result.issues == ["gap"]
    assert result.suggestions == ["justify"]


@pytest.mark.asyncio
async def test_critic_reviewer_logs_only_phase_role_and_duration(caplog):
    secret = "sk-reviewer-secret"
    llm = FakeLLM(
        "VERDICT: PASS\nISSUES: none\nSUGGESTIONS: none\nCONFIDENCE: 1.0"
    )
    reviewer = CriticReviewer(llm=llm)
    turn = ReActTurn(
        thought="do not log thought",
        action=Action(name="conclude", args={"answer": f"do not log {secret}"}),
        observation=ToolObservation(success=True, output="do not log observation"),
        step_num=1,
    )
    caplog.set_level("INFO", logger="math_agent.agent.reviewers")

    await reviewer.review(turn, ReActTrace(problem="do not log problem"))

    duration_logs = [
        record.getMessage()
        for record in caplog.records
        if record.getMessage().startswith("phase_duration ")
    ]
    assert len(duration_logs) == 1
    assert "phase=critic_review model_role=critic" in duration_logs[0]
    assert secret not in duration_logs[0]


@pytest.mark.asyncio
async def test_formal_reviewer_passes_when_codegen_unavailable():
    reviewer = FormalReviewer(lean_codegen=None)
    turn = _make_turn()
    trace = ReActTrace(problem="P")
    result = await reviewer.review(turn, trace)
    assert result.verdict == "PASS"
    assert result.reviewer == "formal"
    assert "unavailable" in result.suggestions[0].lower()


@pytest.mark.asyncio
async def test_formal_reviewer_passes_when_not_formalizable():
    codegen = FakeLeanCodegen(should_formalize=False)
    reviewer = FormalReviewer(lean_codegen=codegen)
    turn = _make_turn()
    trace = ReActTrace(problem="P")
    result = await reviewer.review(turn, trace)
    assert result.verdict == "PASS"
    assert result.confidence == pytest.approx(0.9)


@pytest.mark.asyncio
async def test_formal_reviewer_passes_when_conclude_not_formalizable():
    codegen = FakeLeanCodegen(should_formalize=False)
    reviewer = FormalReviewer(lean_codegen=codegen)
    turn = ReActTurn(
        thought="apply maximum principle",
        action=Action(name="conclude", args={"answer": "u >= 0"}),
        observation=ToolObservation(success=True, output="Conclusion: u >= 0"),
        step_num=1,
    )
    trace = ReActTrace(problem="PDE maximum principle")
    result = await reviewer.review(turn, trace)
    assert result.verdict == "PASS"
    assert "not formalizable" in result.suggestions[0].lower()


@pytest.mark.asyncio
async def test_formal_reviewer_attempts_prose_conclusion():
    codegen = FakeLeanCodegenWithConclude(
        should_formalize=True,
        lean_code="theorem sqrt2_irrational : Irrational (Real.sqrt 2) := by ...",
        result=FakeLeanResult(success=True),
    )
    reviewer = FormalReviewer(lean_codegen=codegen)
    turn = ReActTurn(
        thought="sqrt(2) is irrational",
        action=Action(name="conclude", args={"answer": "√2 is irrational."}),
        observation=ToolObservation(success=True, output="Conclusion: √2 is irrational."),
        step_num=1,
    )
    trace = ReActTrace(problem="Prove sqrt(2) is irrational")
    result = await reviewer.review(turn, trace)
    assert result.verdict == "PASS"
    assert "succeeded" in result.suggestions[0].lower()
    assert turn.observation.lean_code == codegen._lean_code
    evidence = turn.observation.metadata.get("formal_evidence")
    assert isinstance(evidence, dict)
    assert evidence.get("action") == "conclude"
    assert evidence.get("passed") is True
    assert "formal-" in str(evidence.get("id"))
    assert evidence.get("declared_claim") == "√2 is irrational."


@pytest.mark.asyncio
async def test_formal_reviewer_times_out_gracefully():
    codegen = FakeLeanCodegenWithConclude(
        should_formalize=True,
        verify_delay=10.0,  # longer than the reviewer timeout
    )
    reviewer = FormalReviewer(lean_codegen=codegen, timeout_seconds=0.1)
    turn = ReActTurn(
        thought="long proof",
        action=Action(name="conclude", args={"answer": "P holds."}),
        observation=ToolObservation(success=True, output="Conclusion"),
        step_num=1,
    )
    trace = ReActTrace(problem="Prove P")
    result = await reviewer.review(turn, trace)
    assert result.verdict == "PASS"
    assert (
        "timeout" in result.suggestions[0].lower()
        or "skipped" in result.suggestions[0].lower()
    )


@pytest.mark.asyncio
async def test_formal_reviewer_passes_when_lean_succeeds():
    codegen = FakeLeanCodegen(
        should_formalize=True,
        lean_code="theorem t : True := trivial",
        result=FakeLeanResult(success=True),
    )
    reviewer = FormalReviewer(lean_codegen=codegen)
    turn = _make_turn()
    trace = ReActTrace(problem="P")
    result = await reviewer.review(turn, trace)
    assert result.verdict == "PASS"
    assert "succeeded" in result.suggestions[0].lower()
    assert result.confidence == pytest.approx(1.0)
    assert turn.observation.lean_code == codegen._lean_code
    evidence = turn.observation.metadata.get("formal_evidence")
    assert isinstance(evidence, dict)
    assert evidence.get("action") == "think"
    assert evidence.get("passed") is True
    assert evidence.get("target_claim") == "P"


@pytest.mark.asyncio
async def test_formal_reviewer_fails_when_lean_fails():
    codegen = FakeLeanCodegen(
        should_formalize=True,
        lean_code="theorem t : True := sorry",
        result=FakeLeanResult(success=False, errors=["unsolved goals"]),
    )
    reviewer = FormalReviewer(lean_codegen=codegen)
    turn = _make_turn()
    trace = ReActTrace(problem="P")
    result = await reviewer.review(turn, trace)
    assert result.verdict == "FAIL"
    assert "unsolved goals" in result.issues[0]


@pytest.mark.asyncio
async def test_formal_reviewer_fails_when_decision_raises():
    codegen = FakeLeanCodegen(raise_on_decide=True)
    reviewer = FormalReviewer(lean_codegen=codegen)
    turn = _make_turn()
    trace = ReActTrace(problem="P")
    result = await reviewer.review(turn, trace)
    assert result.verdict == "PASS"
    assert result.confidence == pytest.approx(0.9)


@pytest.mark.asyncio
async def test_formal_reviewer_fails_when_generation_raises():
    codegen = FakeLeanCodegen(should_formalize=True, raise_on_generate=True)
    reviewer = FormalReviewer(lean_codegen=codegen)
    turn = _make_turn()
    trace = ReActTrace(problem="P")
    result = await reviewer.review(turn, trace)
    assert result.verdict == "FAIL"
    assert "generation failed" in result.issues[0].lower()


@pytest.mark.asyncio
async def test_knowledge_reviewer_passes_without_project_context():
    reviewer = KnowledgeReviewer(knowledge_store=FakeKnowledgeStore())
    turn = _make_turn()
    trace = ReActTrace(problem="P")
    result = await reviewer.review(turn, trace)
    assert result.verdict == "PASS"
    assert result.reviewer == "knowledge"


@pytest.mark.asyncio
async def test_knowledge_reviewer_passes_without_knowledge_store():
    reviewer = KnowledgeReviewer(knowledge_store=None)
    turn = _make_turn()
    trace = ReActTrace(
        problem="P", project_context=ProjectContext(project_id="proj-1")
    )
    result = await reviewer.review(turn, trace)
    assert result.verdict == "PASS"


@pytest.mark.asyncio
async def test_knowledge_reviewer_returns_suggestions_when_facts_found():
    store = FakeKnowledgeStore(facts=[{"statement": "a^2 + b^2 = c^2"}])
    reviewer = KnowledgeReviewer(knowledge_store=store)
    turn = _make_turn()
    trace = ReActTrace(
        problem="P", project_context=ProjectContext(project_id="proj-1")
    )
    result = await reviewer.review(turn, trace)
    assert result.verdict == "PASS"
    assert len(result.suggestions) == 1
    assert "a^2 + b^2 = c^2" in result.suggestions[0]
    assert result.confidence == pytest.approx(0.8)


@pytest.mark.asyncio
async def test_knowledge_reviewer_passes_when_search_raises():
    class BrokenStore:
        def search_facts(self, project_id, query, limit=5):
            raise RuntimeError("search down")

    reviewer = KnowledgeReviewer(knowledge_store=BrokenStore())
    turn = _make_turn()
    trace = ReActTrace(
        problem="P", project_context=ProjectContext(project_id="proj-1")
    )
    result = await reviewer.review(turn, trace)
    assert result.verdict == "PASS"
    assert "search down" in result.suggestions[0]


def test_parse_critic_response_pass():
    text = "VERDICT: PASS\nISSUES: none\nSUGGESTIONS: none\nCONFIDENCE: 0.95"
    result = _parse_critic_response("critic", text)
    assert result.verdict == "PASS"
    assert result.issues == []
    assert result.suggestions == []
    assert result.confidence == pytest.approx(0.95)


def test_parse_critic_response_fail_multiline_lists():
    text = (
        "VERDICT: FAIL\n"
        "ISSUES: gap\n- assumption\n"
        "SUGGESTIONS: justify\n- cite\n"
        "CONFIDENCE: 0.7"
    )
    result = _parse_critic_response("critic", text)
    assert result.verdict == "FAIL"
    assert result.issues == ["gap", "assumption"]
    assert result.suggestions == ["justify", "cite"]


def test_extract_confidence_defaults_to_half():
    assert _extract_confidence("no confidence here") == 0.5


def test_extract_list_skips_none():
    text = "ISSUES: none\nSUGGESTIONS: fix it\nCONFIDENCE: 0.6"
    assert _extract_list(text, "ISSUES:") == []
    assert _extract_list(text, "SUGGESTIONS:") == ["fix it"]
