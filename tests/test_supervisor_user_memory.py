from __future__ import annotations

import logging

import pytest
from math_agent.agent.supervisor import SupervisorAgent
from math_agent.agent.react_state import ProjectContext
from math_agent.billing.models import LLMResponse
from math_agent.config import default_config
from math_agent.web.user_memory_store import MemoryStatus, UserMemoryEntryKind, UserMemoryStore


class FakeLLM:
    async def complete(self, messages, *, system=None, temperature=None):
        return LLMResponse(
            text='{"add":[{"kind":"preference","content":"用中文回答","why":"...","weight":0.9,"scope":"global"}]}',
            prompt_tokens=0,
            completion_tokens=0,
            total_tokens=0,
        )


@pytest.fixture
def store(tmp_path):
    return UserMemoryStore(user_id="u-1", root=tmp_path)


@pytest.mark.asyncio
async def test_supervisor_post_solve_extracts_user_memory(store):
    agent = SupervisorAgent(
        llm=FakeLLM(),
        critic_llm=FakeLLM(),
        config=default_config().agent,
        user_memory_store=store,
        project_context=ProjectContext(project_id="p1", user_id="u-1"),
    )
    from math_agent.agent.react_state import ReActSolution, ReActTrace
    trace = ReActTrace(problem="test", project_context=ProjectContext(project_id="p1", user_id="u-1"))
    solution = ReActSolution(problem="test", turns=[], final_answer="ok")
    await agent._run_post_solve(
        strategy="react",
        solution=solution,
        trace=trace,
        problem="test",
        session_id="session-1",
        emit=None,
        run_log=__import__("logging").getLogger("test"),
    )
    assert len(store.list()) == 1
    assert store.list()[0].source_session_id == "session-1"


def _build_agent(store) -> SupervisorAgent:
    return SupervisorAgent(
        llm=FakeLLM(),
        critic_llm=FakeLLM(),
        config=default_config().agent,
        user_memory_store=store,
        project_context=ProjectContext(project_id="p1", user_id="u-1"),
    )


def _solve_artifacts():
    from math_agent.agent.react_state import ReActSolution, ReActTrace

    trace = ReActTrace(
        problem="test",
        project_context=ProjectContext(project_id="p1", user_id="u-1"),
    )
    solution = ReActSolution(problem="test", turns=[], final_answer="ok")
    return trace, solution


@pytest.mark.asyncio
async def test_post_solve_memory_consolidation_failure_is_visible(
    store, caplog, monkeypatch
):
    from math_agent.agent.memory_consolidation import MemoryConsolidator

    async def boom(self, trace, solution):
        raise RuntimeError("consolidator exploded")

    monkeypatch.setattr(MemoryConsolidator, "consolidate", boom)
    agent = _build_agent(store)
    trace, solution = _solve_artifacts()

    with caplog.at_level(logging.WARNING):
        await agent._run_post_solve(
            strategy="react",
            solution=solution,
            trace=trace,
            problem="test",
            session_id="session-1",
            emit=None,
            run_log=logging.getLogger("test"),
        )

    warnings = [
        record
        for record in caplog.records
        if "Memory consolidation failed" in record.getMessage()
    ]
    assert warnings
    assert all(record.exc_info is not None for record in warnings)
    assert any(
        "Memory consolidation failed" in issue
        for issue in solution.verification_issues
    )
    # The failure must not abort post-solve: user memory still consolidates.
    assert len(store.list()) == 1


@pytest.mark.asyncio
async def test_post_solve_user_memory_consolidation_failure_is_visible(
    store, caplog, monkeypatch
):
    from math_agent.agent.user_memory import UserMemoryConsolidator

    async def boom(self, *args, **kwargs):
        raise RuntimeError("user memory exploded")

    monkeypatch.setattr(UserMemoryConsolidator, "consolidate", boom)
    agent = _build_agent(store)
    trace, solution = _solve_artifacts()

    with caplog.at_level(logging.WARNING):
        await agent._run_post_solve(
            strategy="react",
            solution=solution,
            trace=trace,
            problem="test",
            session_id="session-1",
            emit=None,
            run_log=logging.getLogger("test"),
        )

    warnings = [
        record
        for record in caplog.records
        if "User memory consolidation failed" in record.getMessage()
    ]
    assert warnings
    assert all(record.exc_info is not None for record in warnings)
    assert any(
        "User memory consolidation failed" in issue
        for issue in solution.verification_issues
    )


def test_supervisor_wires_user_memory_store_to_augmentor(store):
    agent = SupervisorAgent(
        llm=FakeLLM(),
        critic_llm=FakeLLM(),
        config=default_config().agent,
        user_memory_store=store,
        project_context=ProjectContext(project_id="p1", user_id="u-1"),
    )
    assert agent._augmentor.user_memory_store is store


@pytest.mark.asyncio
async def test_supervisor_augmentor_includes_user_memory_in_prompt(store):
    store.save_profile("prefers concise answers")
    store.add(
        content="用中文回答",
        kind=UserMemoryEntryKind.PREFERENCE,
        weight=0.9,
        status=MemoryStatus.ACTIVE,
    )
    agent = SupervisorAgent(
        llm=FakeLLM(),
        critic_llm=FakeLLM(),
        config=default_config().agent,
        user_memory_store=store,
        project_context=ProjectContext(project_id="p1", user_id="u-1"),
    )
    result = await agent._augmentor.augment("prove sqrt(2) is irrational", "p1", user_id="u-1")
    assert "prefers concise answers" in result.prompt
    assert "用中文回答" in result.prompt
