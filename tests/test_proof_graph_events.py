"""The ReAct engine streams proof_graph snapshots when the goal DAG mutates."""
from __future__ import annotations

import pytest

from math_agent.agent.react_agent import ReActAgent
from math_agent.agent.react_state import ReActTrace


def _agent() -> ReActAgent:
    # Only the emit helper is under test; no backends are touched.
    from math_agent.config import AgentConfig

    return ReActAgent(llm=None, critic_llm=None, config=AgentConfig())  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_proof_graph_event_emitted_once_per_mutation():
    agent = _agent()
    trace = ReActTrace(problem="Prove P", current_goal="Prove P")
    trace.proof_graph.ensure_root("Prove P")

    events: list[dict] = []

    async def emit(event: dict) -> None:
        events.append(event)

    await agent._emit_proof_graph_if_changed(trace, emit)
    assert len(events) == 1
    payload = events[0]
    assert payload["type"] == "proof_graph"
    graph = payload["proof_graph"]
    assert graph["root_id"]
    assert graph["active_goal_id"] == graph["root_id"]
    assert [goal["status"] for goal in graph["goals"]] == ["in_progress"]

    # Unchanged graph: no duplicate event.
    await agent._emit_proof_graph_if_changed(trace, emit)
    assert len(events) == 1

    # A subgoal addition is a new snapshot.
    root = trace.proof_graph.goals[trace.proof_graph.root_id]
    sub = trace.proof_graph.upsert_goal("prove the inductive step", activate=True)
    root.depends_on.append(sub.id)
    await agent._emit_proof_graph_if_changed(trace, emit)
    assert len(events) == 2
    assert len(events[1]["proof_graph"]["goals"]) == 2

    # A status flip (proved) re-emits.
    trace.proof_graph.mark_proved(sub.id, evidence_id="ev-1")
    await agent._emit_proof_graph_if_changed(trace, emit)
    assert len(events) == 3
    statuses = {
        goal["id"]: goal["status"] for goal in events[2]["proof_graph"]["goals"]
    }
    assert statuses[sub.id] == "proved"


@pytest.mark.asyncio
async def test_proof_graph_event_skipped_when_empty():
    agent = _agent()
    trace = ReActTrace(problem="P", current_goal="P")
    events: list[dict] = []

    async def emit(event: dict) -> None:
        events.append(event)

    await agent._emit_proof_graph_if_changed(trace, emit)
    assert events == []
