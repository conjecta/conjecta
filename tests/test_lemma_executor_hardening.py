from types import SimpleNamespace

import pytest

from math_agent.agent.planner import FormalizationPlan
from math_agent.lean.lemma_executor import LemmaDAGExecutor
from math_agent.lean.result import LeanResult


class FakeLLM:
    """Returns a fixed proof body for every completion request."""

    def __init__(self, response: str = "```lean\ntrivial\n```"):
        self.response = response
        self.calls = 0

    async def complete(self, messages, system=None, temperature=0.0):
        self.calls += 1
        return SimpleNamespace(text=self.response)


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


def _make_executor(plan):
    return LemmaDAGExecutor(
        llm=None,  # methods under test do not use llm
        runner=None,
        plan=plan,
        problem="Spectral theorem",
    )


def test_build_code_wraps_with_context():
    plan = FormalizationPlan(
        recommended_imports=["Mathlib.Tactic.Common"],
        variables=["{H : Type*}", "[InnerProductSpace ℝ H]"],
        assumptions=["hT : IsSelfAdjoint T"],
        lemmas=[{"name": "foo", "statement": "True", "proof_hint": ""}],
    )
    executor = _make_executor(plan)
    executor.verified_lemmas = ["lemma bar : True := trivial"]
    code = executor._build_code("lemma foo : True := trivial")
    assert "section ProblemContext" in code
    assert "variable {H : Type*}" in code
    assert "variable (hT : IsSelfAdjoint T)" in code
    assert "lemma bar : True := trivial" in code
    assert "lemma foo : True := trivial" in code
    assert "end ProblemContext" in code
    assert code.find("import") < code.find("section ProblemContext")
    assert code.find("section ProblemContext") < code.find("end ProblemContext")


def test_strip_header_repeated_lemma():
    executor = _make_executor(FormalizationPlan())
    raw = "lemma foo : True := by\n  trivial\n"
    assert executor._strip_header(raw, "lemma") == "trivial"


def test_strip_header_repeated_theorem():
    executor = _make_executor(FormalizationPlan())
    raw = "theorem main_result : True := by\n  trivial\n"
    assert executor._strip_header(raw, "theorem") == "trivial"


def test_strip_header_already_body():
    executor = _make_executor(FormalizationPlan())
    raw = "  trivial\n"
    assert executor._strip_header(raw, "lemma") == "trivial"


def test_indent_idempotent():
    executor = _make_executor(FormalizationPlan())
    body = "trivial\n  trivial"
    out = executor._indent(body)
    assert out == "  trivial\n  trivial"


@pytest.mark.asyncio
async def test_execute_prefers_formal_statement_over_informal():
    plan = FormalizationPlan(
        goal_type="True",
        lemmas=[
            {
                "name": "step_one",
                "statement": "the informal English lemma text",
                "formal_statement": "True",
                "proof_hint": "",
            }
        ],
    )
    runner = FakeRunner()
    executor = LemmaDAGExecutor(
        llm=FakeLLM(), runner=runner, plan=plan, problem="Prove True."
    )
    code = await executor.execute()
    assert code is not None
    assert "lemma step_one : True := by" in code
    assert "informal English" not in code


@pytest.mark.asyncio
async def test_execute_falls_back_to_statement_when_no_formal_statement():
    plan = FormalizationPlan(
        goal_type="True",
        lemmas=[{"name": "step_one", "statement": "True", "proof_hint": ""}],
    )
    runner = FakeRunner()
    executor = LemmaDAGExecutor(
        llm=FakeLLM(), runner=runner, plan=plan, problem="Prove True."
    )
    code = await executor.execute()
    assert code is not None
    assert "lemma step_one : True := by" in code


@pytest.mark.asyncio
async def test_execute_returns_none_on_empty_goal_type_without_calling_lean():
    plan = FormalizationPlan(
        goal_type="  ",
        lemmas=[{"name": "step_one", "statement": "True"}],
    )
    llm = FakeLLM()
    runner = FakeRunner()
    executor = LemmaDAGExecutor(llm=llm, runner=runner, plan=plan, problem="Prove True.")
    assert await executor.execute() is None
    assert llm.calls == 0
    assert runner.checked == []


@pytest.mark.asyncio
async def test_execute_returns_none_on_empty_lemma_statement_without_calling_lean():
    plan = FormalizationPlan(
        goal_type="True",
        lemmas=[{"name": "step_one", "statement": "", "formal_statement": "  "}],
    )
    llm = FakeLLM()
    runner = FakeRunner()
    executor = LemmaDAGExecutor(llm=llm, runner=runner, plan=plan, problem="Prove True.")
    assert await executor.execute() is None
    assert llm.calls == 0
    assert runner.checked == []


@pytest.mark.asyncio
async def test_execute_returns_none_when_runner_rejects(monkeypatch):
    monkeypatch.setattr(LemmaDAGExecutor, "_candidates_block", lambda self, diag: "")
    plan = FormalizationPlan(
        goal_type="True",
        lemmas=[{"name": "step_one", "statement": "True"}],
    )
    runner = FakeRunner(success=False)
    executor = LemmaDAGExecutor(
        llm=FakeLLM(),
        runner=runner,
        plan=plan,
        problem="Prove True.",
        max_repair_attempts=1,
    )
    assert await executor.execute() is None


@pytest.mark.asyncio
async def test_search_hook_solves_lemma_without_llm_codegen():
    """A REPL-search hook proof body is used directly (and still re-verified)."""
    plan = FormalizationPlan(
        goal_type="True",
        lemmas=[{"name": "step_one", "statement": "True", "proof_hint": ""}],
    )
    llm = FakeLLM()
    runner = FakeRunner()

    async def hook(name: str, statement: str) -> str:
        # ProofSearch returns whole files; the executor extracts the body.
        return f"import Mathlib.Tactic.Common\n\nlemma {name} : {statement} := by\n  trivial"

    executor = LemmaDAGExecutor(
        llm=llm,
        runner=runner,
        plan=plan,
        problem="Prove True.",
        search_hook=hook,
    )
    code = await executor.execute()
    assert code is not None
    assert "lemma step_one : True := by" in code
    # Two LLM calls remain: the (wasted, parallel) difficulty estimate and the
    # main theorem assembly — no codegen call was needed for the lemma itself.
    assert llm.calls == 2
    assert runner.checked


@pytest.mark.asyncio
async def test_search_hook_failure_falls_back_to_llm():
    plan = FormalizationPlan(
        goal_type="True",
        lemmas=[{"name": "step_one", "statement": "True", "proof_hint": ""}],
    )
    llm = FakeLLM()
    runner = FakeRunner()

    async def failing_hook(name: str, statement: str):
        raise RuntimeError("repl died")

    executor = LemmaDAGExecutor(
        llm=llm,
        runner=runner,
        plan=plan,
        problem="Prove True.",
        search_hook=failing_hook,
    )
    code = await executor.execute()
    assert code is not None
    assert llm.calls >= 1


def test_lemma_levels_grouping():
    from math_agent.lean.lemma_executor import _lemma_levels

    lemmas = [
        {"name": "a", "statement": "True"},
        {"name": "b", "statement": "True"},
        {"name": "c", "statement": "True", "depends_on": ["a"]},
    ]
    levels = _lemma_levels(lemmas)
    assert [[idx for idx, _ in level] for level in levels] == [[1, 2], [3]]

    # Unknown dependency falls back to strict sequential order.
    unknown = [{"name": "a", "depends_on": ["zzz"], "statement": "True"}]
    assert [[idx for idx, _ in level] for level in _lemma_levels(unknown)] == [[1]]

    # Forward/self dependency also falls back.
    cyclic = [
        {"name": "a", "statement": "True", "depends_on": ["b"]},
        {"name": "b", "statement": "True", "depends_on": ["a"]},
    ]
    assert [[idx for idx, _ in level] for level in _lemma_levels(cyclic)] == [[1], [2]]


@pytest.mark.asyncio
async def test_independent_lemmas_prove_concurrently():
    import asyncio

    plan = FormalizationPlan(
        goal_type="True",
        lemmas=[
            {"name": "one", "statement": "True"},
            {"name": "two", "statement": "True"},
        ],
    )

    class SlowRunner:
        def __init__(self):
            self.in_flight = 0
            self.max_in_flight = 0

        async def check_proof(self, code: str):
            self.in_flight += 1
            self.max_in_flight = max(self.max_in_flight, self.in_flight)
            await asyncio.sleep(0.05)
            self.in_flight -= 1
            return LeanResult(success=True)

    runner = SlowRunner()
    executor = LemmaDAGExecutor(
        llm=FakeLLM(),
        runner=runner,
        plan=plan,
        problem="Prove True.",
        max_parallel=2,
    )
    code = await executor.execute()
    assert code is not None
    assert runner.max_in_flight == 2
    assert len(executor.verified_lemmas) == 2


@pytest.mark.asyncio
async def test_dependent_lemma_waits_for_its_dependency():

    plan = FormalizationPlan(
        goal_type="True",
        lemmas=[
            {"name": "base", "statement": "True"},
            {"name": "step", "statement": "True", "depends_on": ["base"]},
        ],
    )
    verified_contexts: list[str] = []

    class RecordingRunner:
        async def check_proof(self, code: str):
            verified_contexts.append(code)
            return LeanResult(success=True)

    executor = LemmaDAGExecutor(
        llm=FakeLLM(),
        runner=RecordingRunner(),
        plan=plan,
        problem="Prove True.",
        max_parallel=3,
    )
    code = await executor.execute()
    assert code is not None
    # The dependent lemma was verified against a context already containing
    # its dependency's verified code.
    step_check = next(c for c in verified_contexts if "lemma step" in c)
    assert "lemma base" in step_check
    assert step_check.index("lemma base") < step_check.index("lemma step")


@pytest.mark.asyncio
async def test_rescue_decomposes_failed_lemma_and_retries():
    """A lemma that fails direct proof gets a sub-decomposition rescue round."""
    plan = FormalizationPlan(
        goal_type="True",
        lemmas=[{"name": "hard", "statement": "True"}],
    )

    class FlakyRunner:
        """Parent lemma fails until the sub-lemma is in context."""

        async def check_proof(self, code: str):
            if "lemma hard :" in code and "lemma hard_sub1" not in code:
                return LeanResult(
                    success=False, errors=["type mismatch"], failure_kind="type_mismatch"
                )
            return LeanResult(success=True)

    class DecomposingLLM:
        def __init__(self):
            self.calls = 0

        async def complete(self, messages, system=None, temperature=0.0):
            self.calls += 1
            text = messages[0].content
            if "Decompose it into" in text:
                return SimpleNamespace(
                    text='[{"statement": "True", "proof_hint": "trivial"}]'
                )
            return SimpleNamespace(text="```lean\ntrivial\n```")

    executor = LemmaDAGExecutor(
        llm=DecomposingLLM(),
        runner=FlakyRunner(),
        plan=plan,
        problem="Prove True.",
        max_repair_attempts=1,
        rescue_enabled=True,
    )
    code = await executor.execute()
    assert code is not None
    assert "lemma hard_sub1 : True := by" in code
    assert "lemma hard : True := by" in code
    assert code.index("lemma hard_sub1") < code.index("lemma hard :")


@pytest.mark.asyncio
async def test_rescue_failure_still_aborts():
    plan = FormalizationPlan(
        goal_type="True",
        lemmas=[{"name": "hard", "statement": "True"}],
    )
    llm = FakeLLM()
    runner = FakeRunner(success=False)
    executor = LemmaDAGExecutor(
        llm=llm,
        runner=runner,
        plan=plan,
        problem="Prove True.",
        max_repair_attempts=1,
        rescue_enabled=True,
    )
    assert await executor.execute() is None


@pytest.mark.asyncio
async def test_multi_route_accepts_first_verifying_body():
    """route_count>1 samples diverse bodies; the first that verifies wins."""
    plan = FormalizationPlan(
        goal_type="True",
        lemmas=[{"name": "multi", "statement": "True"}],
    )

    class RouteLLM:
        def __init__(self):
            self.temperatures: list[float] = []

        async def complete(self, messages, system=None, temperature=0.0):
            self.temperatures.append(temperature)
            # Only the first route of the lemma stage produces a bad body.
            bad = temperature == 0.0 and "the next lemma" in messages[0].content
            marker = "bad" if bad else "trivial"
            return SimpleNamespace(text=f"```lean\n{marker}\n```")

    class RouteRunner:
        async def check_proof(self, code: str):
            return LeanResult(
                success="bad" not in code,
                errors=[] if "bad" not in code else ["type mismatch"],
                failure_kind=None if "bad" not in code else "type_mismatch",
            )

    llm = RouteLLM()
    executor = LemmaDAGExecutor(
        llm=llm,
        runner=RouteRunner(),
        plan=plan,
        problem="Prove True.",
        route_count=3,
    )
    code = await executor.execute()
    assert code is not None
    assert "lemma multi : True := by" in code
    assert "bad" not in code
    # Multiple routes were sampled with diversified temperatures (the first
    # call is the difficulty estimate, which precedes route sampling).
    assert llm.temperatures[1:4] == [0.0, 0.5, 0.9]
