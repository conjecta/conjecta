from __future__ import annotations

import pytest

from math_agent.agent.state import ReasoningState, ReasoningStep, StepType
from math_agent.billing.models import LLMResponse
from math_agent.config import LeanConfig
from math_agent.lean.codegen import LeanCodegen
from math_agent.lean.premise_retriever import PremiseEntry, PremiseRetriever
from math_agent.lean.result import LeanResult
from math_agent.agent.prompts import LEAN_CODEGEN_SYSTEM


class FakeLLM:
    def __init__(self, response: str):
        self.response = response

    async def complete(self, messages, system=None, temperature=None):
        return LLMResponse(
            text=self.response,
            prompt_tokens=0,
            completion_tokens=0,
            total_tokens=0,
        )


class RecordingFakeLLM:
    def __init__(self, response: str):
        self.response = response
        self.calls: list[list] = []

    async def complete(self, messages, system=None, temperature=None):
        self.calls.append(messages)
        return LLMResponse(
            text=self.response,
            prompt_tokens=0,
            completion_tokens=0,
            total_tokens=0,
        )

    @property
    def all_prompts(self) -> list[str]:
        return [messages[0].content for messages in self.calls]


class FakeRunner:
    def __init__(self, results=None):
        self._results = results or [LeanResult(success=False, errors=["unknown"])]
        self._index = 0

    async def check_proof(self, code: str) -> LeanResult:
        result = self._results[self._index]
        self._index = min(self._index + 1, len(self._results) - 1)
        return result


@pytest.mark.asyncio
async def test_codegen_prepends_premises_to_prompt():
    retriever = PremiseRetriever(
        entries=[
            PremiseEntry(
                name="Nat.Prime",
                module="Mathlib.Data.Nat.Prime",
                type="Nat -> Prop",
                docstring="",
            ),
        ]
    )
    llm = FakeLLM(
        "```lean\nimport Mathlib.Data.Nat.Prime\ntheorem ex : 1 = 1 := by rfl\n```"
    )
    codegen = LeanCodegen(
        llm=llm,
        runner=FakeRunner(),
        config=LeanConfig(),
        premise_retriever=retriever,
    )
    state = ReasoningState(problem="test")
    step = ReasoningStep(content="prove Nat.Prime 2", step_type=StepType.FORMALIZATION)
    code, _ = await codegen.generate_and_verify(step, state)
    assert "import Mathlib.Data.Nat.Prime" in code


@pytest.mark.asyncio
async def test_codegen_prompt_includes_premise_text():
    retriever = PremiseRetriever(
        entries=[
            PremiseEntry(
                name="Nat.Prime",
                module="Mathlib.Data.Nat.Prime",
                type="Nat -> Prop",
                docstring="",
            ),
        ]
    )
    llm = RecordingFakeLLM(
        "```lean\nimport Mathlib.Data.Nat.Prime\ntheorem ex : 1 = 1 := by rfl\n```"
    )
    codegen = LeanCodegen(
        llm=llm,
        runner=FakeRunner(),
        config=LeanConfig(),
        premise_retriever=retriever,
    )
    state = ReasoningState(problem="test")
    step = ReasoningStep(content="prove Nat.Prime 2", step_type=StepType.FORMALIZATION)
    await codegen.generate_and_verify(step, state)

    assert llm.calls
    prompts = llm.all_prompts
    assert any("Relevant mathlib4 declarations you may use:" in p for p in prompts)
    assert any("Nat.Prime" in p for p in prompts)
    assert any("Mathlib.Data.Nat.Prime" in p for p in prompts)


@pytest.mark.asyncio
async def test_codegen_disabled_premise_retriever_works():
    llm = RecordingFakeLLM(
        "```lean\ntheorem ex : 1 = 1 := by rfl\n```"
    )
    codegen = LeanCodegen(
        llm=llm,
        runner=FakeRunner(),
        config=LeanConfig(),
        premise_retriever=None,
    )
    state = ReasoningState(problem="test")
    step = ReasoningStep(content="prove 1 = 1", step_type=StepType.FORMALIZATION)
    code, _ = await codegen.generate_and_verify(step, state)
    assert "theorem ex : 1 = 1" in code
    assert llm.calls
    assert all(
        "Relevant mathlib4 declarations you may use:" not in p
        for p in llm.all_prompts
    )


@pytest.mark.asyncio
async def test_codegen_repair_adds_imports_for_unknown_constant():
    entries = [
        PremiseEntry(
            name="Nat.Prime",
            module="Mathlib.Data.Nat.Prime",
            type="Nat -> Prop",
            docstring="",
        ),
    ]
    retriever = PremiseRetriever(entries=entries)
    llm = RecordingFakeLLM(
        "```lean\ntheorem ex : Nat.Prime 2 := by native_decide\n```"
    )
    codegen = LeanCodegen(
        llm=llm,
        runner=FakeRunner(),
        config=LeanConfig(),
        premise_retriever=retriever,
    )
    state = ReasoningState(problem="test")
    step = ReasoningStep(content="prove Nat.Prime 2", step_type=StepType.FORMALIZATION)

    original_code = "theorem ex : Nat.Prime 2 := by native_decide"
    errors = ["unknown constant 'Nat.Prime'"]
    await codegen._repair(original_code, errors, step, state)

    assert llm.calls
    repair_prompt = llm.all_prompts[-1]
    assert "import Mathlib.Data.Nat.Prime" in repair_prompt
    assert "theorem ex : Nat.Prime 2 := by native_decide" in repair_prompt


def test_codegen_prompt_does_not_promise_exact_comment_conversion():
    assert "exact? -- term" not in LEAN_CODEGEN_SYSTEM
    assert "the comment will be converted automatically" not in LEAN_CODEGEN_SYSTEM
