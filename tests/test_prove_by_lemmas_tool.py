from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from math_agent.agent.planner import FormalizationPlan
from math_agent.agent.tools import ToolContext, ToolRegistry
from math_agent.lean.lemma_executor import LemmaDAGExecutor
from math_agent.lean.result import LeanResult


class QueueFakeLLM:
    """Fake LLM returning queued responses first, then a default proof body."""

    def __init__(
        self,
        responses: list[str] | None = None,
        default: str = "```lean\ntrivial\n```",
    ):
        self.responses = list(responses or [])
        self.default = default
        self.calls = 0

    async def complete(self, messages, system=None, temperature=0.0):
        self.calls += 1
        text = self.responses.pop(0) if self.responses else self.default
        return SimpleNamespace(text=text)


class FakeRunner:
    """Pretends every proof checks (or fails) without invoking Lean."""

    def __init__(self, success: bool = True):
        self.success = success
        self.checked: list[str] = []

    async def check_proof(self, code: str):
        self.checked.append(code)
        return LeanResult(
            success=self.success, errors=[] if self.success else ["type mismatch"]
        )

    async def print_axioms(self, code: str, declaration: str):
        return ["propext"]


def _registry(llm, runner, **kwargs):
    return ToolRegistry(
        enabled_tools=["prove_by_lemmas"], lean_runner=runner, llm=llm, **kwargs
    )


def test_prove_by_lemmas_registered_when_lean_configured():
    registry = _registry(llm=object(), runner=AsyncMock())
    assert "prove_by_lemmas" in registry.available


@pytest.mark.asyncio
async def test_prove_by_lemmas_unavailable_without_llm():
    registry = ToolRegistry(enabled_tools=["prove_by_lemmas"], lean_runner=AsyncMock())
    result = await registry.call("prove_by_lemmas", '{"statement": "True"}', ToolContext())
    assert result.success is False
    assert "unavailable" in result.output.lower()


@pytest.mark.asyncio
async def test_prove_by_lemmas_unavailable_without_runner():
    registry = ToolRegistry(enabled_tools=["prove_by_lemmas"], llm=object())
    result = await registry.call("prove_by_lemmas", '{"statement": "True"}', ToolContext())
    assert result.success is False
    assert "unavailable" in result.output.lower()


@pytest.mark.asyncio
async def test_prove_by_lemmas_with_supplied_lemmas_succeeds():
    runner = FakeRunner()
    llm = QueueFakeLLM()
    registry = _registry(llm=llm, runner=runner)
    lemmas = json.dumps(
        [
            {
                "name": "step_one",
                "statement": "True",
                "proof_hint": "trivial",
                "depends_on": [],
            }
        ]
    )
    ctx = ToolContext(
        formalization_plan={"goal_type": "True", "recommended_imports": []}
    )
    result = await registry.call(
        "prove_by_lemmas",
        json.dumps({"statement": "Prove True.", "lemmas": lemmas}),
        ctx,
    )
    assert result.success is True
    assert "PASSED" in result.output
    assert "Axioms: propext" in result.output
    assert result.lean_code is not None
    assert "lemma step_one : True := by" in result.lean_code
    assert "theorem conjecta_target : True := by" in result.lean_code


@pytest.mark.asyncio
async def test_prove_by_lemmas_supplied_lemmas_plan_goal_type_without_ctx_plan():
    """Model-supplied lemmas with no ctx plan: the planner fills in goal_type."""
    plan_json = json.dumps(
        {
            "restatement": "Prove True",
            "goal_type": "True",
            "recommended_imports": [],
            "lemmas": [],
        }
    )
    runner = FakeRunner()
    llm = QueueFakeLLM(responses=[plan_json])
    registry = _registry(llm=llm, runner=runner)
    lemmas = json.dumps([{"name": "my_lemma", "statement": "True"}])
    result = await registry.call(
        "prove_by_lemmas",
        json.dumps({"statement": "Prove True.", "lemmas": lemmas}),
        ToolContext(),
    )
    assert result.success is True
    assert result.lean_code is not None
    # The model's lemmas override the planner's (empty) lemma list.
    assert "lemma my_lemma : True := by" in result.lean_code
    assert "theorem conjecta_target : True := by" in result.lean_code


@pytest.mark.asyncio
async def test_prove_by_lemmas_uses_ctx_formalization_plan_lemmas():
    runner = FakeRunner()
    llm = QueueFakeLLM()
    registry = _registry(llm=llm, runner=runner)
    ctx = ToolContext(
        formalization_plan={
            "goal_type": "True",
            "recommended_imports": [],
            "lemmas": [{"name": "ctx_step", "statement": "True", "proof_hint": ""}],
        }
    )
    result = await registry.call(
        "prove_by_lemmas", '{"statement": "Prove True."}', ctx
    )
    assert result.success is True
    assert result.lean_code is not None
    assert "lemma ctx_step : True := by" in result.lean_code


@pytest.mark.asyncio
async def test_prove_by_lemmas_plans_decomposition_when_no_lemmas_given():
    plan_json = json.dumps(
        {
            "restatement": "Prove True",
            "goal_type": "True",
            "recommended_imports": [],
            "lemmas": [
                {
                    "name": "planned_step",
                    "statement": "True",
                    "depends_on": [],
                    "proof_hint": "trivial",
                }
            ],
        }
    )
    runner = FakeRunner()
    llm = QueueFakeLLM(responses=[plan_json])
    registry = _registry(llm=llm, runner=runner)
    result = await registry.call(
        "prove_by_lemmas", '{"statement": "Prove True."}', ToolContext()
    )
    assert result.success is True
    assert result.lean_code is not None
    assert "lemma planned_step : True := by" in result.lean_code


@pytest.mark.asyncio
async def test_prove_by_lemmas_fails_cleanly_when_planner_yields_no_lemmas():
    plan_json = json.dumps(
        {"restatement": "Prove True", "goal_type": "True", "lemmas": []}
    )
    runner = FakeRunner()
    llm = QueueFakeLLM(responses=[plan_json])
    registry = _registry(llm=llm, runner=runner)
    result = await registry.call(
        "prove_by_lemmas", '{"statement": "Prove True."}', ToolContext()
    )
    assert result.success is False
    assert "decomposition" in result.output
    assert "formalize" in result.output
    # The executor never ran: Lean was never invoked.
    assert runner.checked == []


@pytest.mark.asyncio
async def test_prove_by_lemmas_fails_cleanly_when_planner_output_is_not_json():
    runner = FakeRunner()
    llm = QueueFakeLLM(responses=["not a plan", "still not a plan"])
    registry = _registry(llm=llm, runner=runner)
    result = await registry.call(
        "prove_by_lemmas", '{"statement": "Prove True."}', ToolContext()
    )
    assert result.success is False
    assert "decomposition" in result.output
    assert runner.checked == []


@pytest.mark.asyncio
async def test_prove_by_lemmas_reports_failure_when_executor_cannot_verify(monkeypatch):
    monkeypatch.setattr(LemmaDAGExecutor, "_candidates_block", lambda self, diag: "")
    runner = FakeRunner(success=False)
    llm = QueueFakeLLM()
    registry = _registry(llm=llm, runner=runner)
    ctx = ToolContext(
        formalization_plan={
            "goal_type": "True",
            "lemmas": [{"name": "step_one", "statement": "True"}],
        }
    )
    result = await registry.call(
        "prove_by_lemmas", '{"statement": "Prove True."}', ctx
    )
    assert result.success is False
    assert "could not be verified" in result.output
    assert result.lean_code is None


@pytest.mark.asyncio
async def test_prove_by_lemmas_requires_non_empty_statement():
    registry = _registry(llm=QueueFakeLLM(), runner=FakeRunner())
    result = await registry.call(
        "prove_by_lemmas", '{"statement": "   "}', ToolContext()
    )
    assert result.success is False
    assert "non-empty" in result.output


@pytest.mark.asyncio
async def test_prove_by_lemmas_tolerates_raw_string_statement():
    plan_json = json.dumps(
        {
            "goal_type": "True",
            "lemmas": [{"name": "raw_step", "statement": "True"}],
        }
    )
    runner = FakeRunner()
    llm = QueueFakeLLM(responses=[plan_json])
    registry = _registry(llm=llm, runner=runner)
    result = await registry.call("prove_by_lemmas", "Prove True.", ToolContext())
    assert result.success is True
    assert result.lean_code is not None
    assert "lemma raw_step : True := by" in result.lean_code


@pytest.mark.asyncio
async def test_prove_by_lemmas_reports_progress_via_event_callback():
    """Executor progress reaches the ToolContext event_callback (and survives
    the registry's _context_with_defaults re-wrapping)."""
    runner = FakeRunner()
    llm = QueueFakeLLM()
    registry = _registry(llm=llm, runner=runner)
    messages: list[str] = []

    async def record(message: str) -> None:
        messages.append(message)

    ctx = ToolContext(
        formalization_plan={"goal_type": "True", "recommended_imports": []},
        event_callback=record,
    )
    result = await registry.call(
        "prove_by_lemmas",
        json.dumps(
            {
                "statement": "Prove True.",
                "lemmas": json.dumps([{"name": "step_one", "statement": "True"}]),
            }
        ),
        ctx,
    )

    assert result.success is True
    assert any("`step_one` 验证通过" in message for message in messages)
    assert any("组装主定理" in message for message in messages)
    assert messages[-1] == "主定理 `conjecta_target` 验证通过"


class SequenceFakeRunner:
    """Returns queued LeanResults in order, then a generic failure."""

    def __init__(self, outcomes: list[LeanResult]):
        self.outcomes = list(outcomes)
        self.checked: list[str] = []

    async def check_proof(self, code: str):
        self.checked.append(code)
        if self.outcomes:
            return self.outcomes.pop(0)
        return LeanResult(success=False, errors=["type mismatch"])

    async def print_axioms(self, code: str, declaration: str):
        return ["propext"]


class SlowFakeRunner:
    """Simulates a Lean check that hangs (e.g. missing dependency oleans)."""

    async def check_proof(self, code: str):
        await asyncio.sleep(30)
        return LeanResult(success=True)


@pytest.mark.asyncio
async def test_prove_by_lemmas_short_circuits_on_infra_failure():
    """Infra failures (lean_unavailable/timeout/unsafe) must not trigger LLM
    repair attempts; the lemma is abandoned after the first check."""
    runner = SequenceFakeRunner(
        [
            LeanResult(
                success=False,
                errors=["lean executable not found"],
                failure_kind="lean_unavailable",
            )
        ]
    )
    llm = QueueFakeLLM()
    registry = _registry(llm=llm, runner=runner)
    ctx = ToolContext(
        formalization_plan={
            "goal_type": "True",
            "lemmas": [{"name": "step_one", "statement": "True"}],
        }
    )
    result = await registry.call(
        "prove_by_lemmas", '{"statement": "Prove True."}', ctx
    )
    assert result.success is False
    # One generation + one difficulty estimate + one verification; no repair
    # loop ran.
    assert llm.calls == 2
    assert len(runner.checked) == 1
    assert "failure_kind=lean_unavailable" in result.output


@pytest.mark.asyncio
async def test_executor_overall_wall_budget_aborts_run():
    """The whole execute() run is bounded by wall_seconds."""
    plan = FormalizationPlan(
        goal_type="True",
        recommended_imports=[],
        lemmas=[{"name": "slow_lemma", "statement": "True"}],
    )
    executor = LemmaDAGExecutor(
        llm=QueueFakeLLM(),
        runner=SlowFakeRunner(),
        plan=plan,
        problem="Prove True.",
        wall_seconds=0.05,
    )
    assert await executor.execute() is None
    assert executor.last_failure is not None
    assert executor.last_failure["failure_kind"] == "timeout"


@pytest.mark.asyncio
async def test_prove_by_lemmas_failure_returns_verified_lemmas_and_diagnostic(
    monkeypatch,
):
    """A failed run still hands back the lemmas verified so far plus a
    diagnostic naming the failed lemma and its failure_kind."""
    monkeypatch.setattr(LemmaDAGExecutor, "_candidates_block", lambda self, diag: "")
    runner = SequenceFakeRunner(
        [
            LeanResult(success=True),  # step_one verifies
            # step_two keeps failing with a repairable error through the repairs
            # (1 initial attempt + lean.max_repair_attempts=3 default repairs).
            LeanResult(success=False, errors=["type mismatch"], failure_kind="type_mismatch"),
            LeanResult(success=False, errors=["type mismatch"], failure_kind="type_mismatch"),
            LeanResult(success=False, errors=["type mismatch"], failure_kind="type_mismatch"),
            LeanResult(success=False, errors=["type mismatch"], failure_kind="type_mismatch"),
        ]
    )
    llm = QueueFakeLLM()
    registry = _registry(llm=llm, runner=runner)
    ctx = ToolContext(
        formalization_plan={
            "goal_type": "True",
            "lemmas": [
                {"name": "step_one", "statement": "True"},
                {"name": "step_two", "statement": "True"},
            ],
        }
    )
    result = await registry.call(
        "prove_by_lemmas", '{"statement": "Prove True."}', ctx
    )
    assert result.success is False
    assert "could not be verified" in result.output
    # Partial progress is returned for the agent to build on.
    assert "Verified lemmas before the failure" in result.output
    assert "lemma step_one : True := by" in result.output
    # Diagnostic names the failed lemma and its failure_kind.
    assert "lemma=step_two" in result.output
    assert "failure_kind=type_mismatch" in result.output
    assert "type mismatch" in result.output


@pytest.mark.asyncio
async def test_prove_by_lemmas_success_path_unchanged():
    """Regression: success still returns the full code and PASSED output."""
    runner = FakeRunner()
    llm = QueueFakeLLM()
    registry = _registry(llm=llm, runner=runner)
    ctx = ToolContext(
        formalization_plan={
            "goal_type": "True",
            "lemmas": [{"name": "step_one", "statement": "True"}],
        }
    )
    result = await registry.call(
        "prove_by_lemmas", '{"statement": "Prove True."}', ctx
    )
    assert result.success is True
    assert "PASSED" in result.output
    assert result.lean_code is not None
    assert "theorem conjecta_target : True := by" in result.lean_code
