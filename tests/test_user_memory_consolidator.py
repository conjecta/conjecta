from __future__ import annotations

import json

import pytest

from math_agent.agent.user_memory import UserMemoryConsolidator
from math_agent.agent.react_state import ProjectContext, ReActSolution, ReActTrace
from math_agent.billing.models import LLMResponse
from math_agent.web.user_memory_store import MemoryStatus, UserMemoryStore, UserMemoryEntryKind


class FakeLLM:
    def __init__(self, response: str):
        self.response = response

    async def complete(self, messages, *, system=None, temperature=None):
        return LLMResponse(
            text=self.response,
            prompt_tokens=0,
            completion_tokens=0,
            total_tokens=0,
        )


@pytest.fixture
def store(tmp_path):
    return UserMemoryStore(user_id="u-1", root=tmp_path)


@pytest.mark.asyncio
async def test_consolidator_extracts_preference(store):
    llm = FakeLLM(
        '{"add":[{"kind":"preference","content":"用中文回答","why":"user asked in Chinese","weight":0.9,"scope":"global"}]}'
    )
    consolidator = UserMemoryConsolidator(llm=llm, store=store)
    trace = ReActTrace(problem="prove sqrt(2) is irrational", project_context=ProjectContext(project_id="default", user_id="u-1"))
    solution = ReActSolution(problem=trace.problem, turns=[], final_answer="...")
    result = await consolidator.consolidate(trace, solution, conversation_history=[])
    assert len(result) == 1
    assert result[0].kind == UserMemoryEntryKind.PREFERENCE
    assert result[0].content == "用中文回答"
    assert len(store.list()) == 1


@pytest.mark.asyncio
async def test_high_confidence_becomes_active_when_grounded(store):
    llm = FakeLLM(
        '{"add":[{"kind":"preference","content":"use short proofs","why":"high confidence","weight":0.85,"scope":"global"}]}'
    )
    consolidator = UserMemoryConsolidator(llm=llm, store=store)
    trace = ReActTrace(
        problem="use short proofs",
        project_context=ProjectContext(project_id="default", user_id="u-1"),
    )
    solution = ReActSolution(problem=trace.problem, turns=[], final_answer="y")
    result = await consolidator.consolidate(trace, solution, conversation_history=[])
    assert len(result) == 1
    assert result[0].status == MemoryStatus.ACTIVE


@pytest.mark.asyncio
async def test_high_confidence_without_evidence_stays_candidate(store):
    llm = FakeLLM(
        '{"add":[{"kind":"preference","content":"unverified fabricated preference","why":"high confidence","weight":0.95,"scope":"global"}]}'
    )
    consolidator = UserMemoryConsolidator(llm=llm, store=store)
    trace = ReActTrace(problem="x", project_context=ProjectContext(project_id="default", user_id="u-1"))
    solution = ReActSolution(problem="x", turns=[], final_answer="y")
    result = await consolidator.consolidate(trace, solution, conversation_history=[])
    assert len(result) == 1
    assert result[0].status == MemoryStatus.CANDIDATE


@pytest.mark.asyncio
async def test_high_confidence_from_user_turn_is_active(store):
    llm = FakeLLM(
        '{"add":[{"kind":"preference","content":"use short proofs","why":"user asked","weight":0.9,"scope":"global"}]}'
    )
    consolidator = UserMemoryConsolidator(llm=llm, store=store)
    trace = ReActTrace(problem="x", project_context=ProjectContext(project_id="default", user_id="u-1"))
    solution = ReActSolution(problem="x", turns=[], final_answer="y")
    result = await consolidator.consolidate(
        trace,
        solution,
        conversation_history=[{"role": "user", "text": "please use short proofs"}],
    )
    assert len(result) == 1
    assert result[0].status == MemoryStatus.ACTIVE


@pytest.mark.asyncio
async def test_lower_confidence_becomes_candidate(store):
    llm = FakeLLM(
        '{"add":[{"kind":"preference","content":"use short proofs","why":"lower confidence","weight":0.84,"scope":"global"}]}'
    )
    consolidator = UserMemoryConsolidator(llm=llm, store=store)
    trace = ReActTrace(problem="x", project_context=ProjectContext(project_id="default", user_id="u-1"))
    solution = ReActSolution(problem="x", turns=[], final_answer="y")
    result = await consolidator.consolidate(trace, solution, conversation_history=[])
    assert len(result) == 1
    assert result[0].status == MemoryStatus.CANDIDATE


@pytest.mark.asyncio
async def test_rejected_memory_blocks_recreation(store):
    original = store.add(
        content="never use induction first",
        kind=UserMemoryEntryKind.TECHNIQUE,
        why="user deleted",
        weight=0.9,
        status=MemoryStatus.ACTIVE,
    )
    store.delete(original.id)

    llm = FakeLLM(
        '{"add":[{"kind":"technique","content":"never use induction first","why":"seems useful","weight":0.9,"scope":"global"}]}'
    )
    consolidator = UserMemoryConsolidator(llm=llm, store=store)
    trace = ReActTrace(problem="x", project_context=ProjectContext(project_id="default", user_id="u-1"))
    solution = ReActSolution(problem="x", turns=[], final_answer="y")
    result = await consolidator.consolidate(trace, solution, conversation_history=[])
    assert len(result) == 0
    assert len(store.list(status=MemoryStatus.ACTIVE)) == 0


@pytest.mark.asyncio
async def test_within_batch_duplicate_is_deduplicated(store):
    llm = FakeLLM(
        '{"add":['
        '{"kind":"preference","content":"use induction sparingly","why":"...","weight":0.9,"scope":"global"},'
        '{"kind":"preference","content":"use induction sparingly","why":"...","weight":0.9,"scope":"global"}'
        ']}'
    )
    consolidator = UserMemoryConsolidator(llm=llm, store=store)
    trace = ReActTrace(problem="x", project_context=ProjectContext(project_id="default", user_id="u-1"))
    solution = ReActSolution(problem="x", turns=[], final_answer="y")
    result = await consolidator.consolidate(trace, solution, conversation_history=[])
    assert len(result) == 1
    assert len(store.list()) == 1


@pytest.mark.asyncio
async def test_project_scope_is_forced_to_current_project(store):
    llm = FakeLLM(
        '{"add":[{"kind":"context","content":"project convention","why":"stated by user",'
        '"weight":0.9,"scope":"project:some-other-project"}]}'
    )
    consolidator = UserMemoryConsolidator(llm=llm, store=store)
    trace = ReActTrace(
        problem="project convention",
        project_context=ProjectContext(project_id="current-project", user_id="u-1"),
    )
    solution = ReActSolution(problem=trace.problem, turns=[], final_answer="y")

    result = await consolidator.consolidate(trace, solution)

    assert len(result) == 1
    assert str(result[0].scope) == "project:current-project"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "content",
    [
        "email me at person@example.com",
        "my phone is 138 1234 5678",
        "api_key=sk-sensitive-provider-key",
    ],
)
async def test_sensitive_personal_data_is_not_persisted(store, content):
    llm = FakeLLM(
        '{"add":[{"kind":"context","content":'
        + json.dumps(content)
        + ',"why":"user said it","weight":0.9,"scope":"global"}]}'
    )
    consolidator = UserMemoryConsolidator(llm=llm, store=store)
    trace = ReActTrace(problem="x", project_context=ProjectContext(user_id="u-1"))
    solution = ReActSolution(problem="x", turns=[], final_answer="y")

    assert await consolidator.consolidate(trace, solution) == []
    assert store.list() == []


@pytest.mark.asyncio
async def test_source_session_is_persisted(store):
    llm = FakeLLM(
        '{"add":[{"kind":"preference","content":"prefer tables","why":"explicit request",'
        '"weight":0.9,"scope":"global"}]}'
    )
    consolidator = UserMemoryConsolidator(llm=llm, store=store)
    trace = ReActTrace(problem="prefer tables", project_context=ProjectContext(user_id="u-1"))
    solution = ReActSolution(problem=trace.problem, turns=[], final_answer="y")

    result = await consolidator.consolidate(trace, solution, source_session_id="session-42")

    assert result[0].source_session_id == "session-42"
