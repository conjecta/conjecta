from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import Any

import pytest

import math_agent.web.agent_factory as web_app
import math_agent.web.solve_session as solve_session
from math_agent.agent.human_interaction import create_interaction, hitl_should_pause
from math_agent.agent.react_state import HumanInputRequired, ReActTrace
from math_agent.config import HitlConfig


async def _async_agent(agent: Any) -> Any:
    return agent


def test_adaptive_hitl_skips_routine_plan_review_but_keeps_escalations():
    config = HitlConfig(enabled=True, mode="adaptive")

    assert hitl_should_pause(config, "plan_review") is False
    assert hitl_should_pause(config, "counterexample") is True
    assert hitl_should_pause(config, "reviewer_block") is True
    assert hitl_should_pause(HitlConfig(enabled=True, mode="auto"), "reviewer_block") is False


def test_hitl_plan_review_fallback_defaults_to_enabled():
    # A config object missing the attribute must fall back to the HitlConfig
    # default (True), not silently disable research plan review.
    loose = SimpleNamespace(enabled=True, mode="adaptive")

    assert hitl_should_pause(loose, "plan_review", force_plan_review=True) is True


def test_adaptive_hitl_forces_budget_extend_when_enabled():
    config = HitlConfig(enabled=True, mode="adaptive")

    assert hitl_should_pause(config, "budget_extend", force_budget_extend=True) is True
    assert (
        hitl_should_pause(
            HitlConfig(enabled=True, mode="auto"),
            "budget_extend",
            force_budget_extend=True,
        )
        is False
    )
    assert hitl_should_pause(HitlConfig(enabled=False), "budget_extend", force_budget_extend=True) is False


@pytest.mark.asyncio
async def test_solve_stream_surfaces_pause_without_persisting_completed_turn(monkeypatch):
    interaction = create_interaction(
        kind="tool_approval",
        question="Approve write?",
        stage="tool_approval",
    )
    persisted: list[str] = []
    detached: list[str] = []
    scheduled: list[tuple] = []

    class FakeAgent:
        async def solve(self, problem: str, **kwargs: Any):
            raise HumanInputRequired(interaction)

    store = SimpleNamespace(get_checkpoint=lambda _checkpoint_id: None)
    config = SimpleNamespace(lean=SimpleNamespace(enabled=False, lean_path=None))
    monkeypatch.setattr(solve_session, "load_config", lambda: config)
    monkeypatch.setattr(
        solve_session,
        "new_session_logger",
        lambda problem, model: ("pause-session", logging.getLogger("test.hitl")),
    )
    monkeypatch.setattr(
        web_app, "_build_agent", lambda **kwargs: _async_agent(FakeAgent())
    )
    monkeypatch.setattr(web_app, "_project_store", lambda user_id=None: store)
    monkeypatch.setattr(web_app, "default_model_string", lambda config: "openai/gpt-5.6-sol")
    monkeypatch.setattr(
        web_app,
        "persist_turn",
        lambda *_args, **_kwargs: persisted.append("persisted"),
    )
    def record_detached(coro):
        detached.append("created")
        coro.close()

    monkeypatch.setattr(web_app.post_solve_tasks, "create", record_detached)
    monkeypatch.setattr(
        solve_session.hitl_auto_resolve,
        "schedule_auto_resolve",
        lambda *args, **kwargs: scheduled.append(args),
    )

    events = [
        event
        async for event in solve_session.stream_solve_events(
            {"problem": "P", "mode": "react"}, user_id="user-1"
        )
    ]

    assert events[-1]["type"] == "human_input_required"
    assert events[-1]["checkpoint_id"] == "pause-session"
    assert events[-1]["request_id"] == interaction["request_id"]
    assert not persisted
    assert not detached
    # Entering the waiting state arms the auto-resolve timer for this request.
    assert scheduled == [("pause-session", interaction["request_id"])]


def test_checkpoint_decision_claim_is_once_only(tmp_path):
    from math_agent.web.project_store import ProjectStore

    store = ProjectStore(root=tmp_path)
    interaction = create_interaction(
        kind="plan_review", question="Continue?", stage="planning"
    )
    checkpoint = ReActTrace(problem="P", pending_interaction=interaction).to_checkpoint()
    checkpoint["session_id"] = "session-1"
    store.write_checkpoint(checkpoint)
    decision = {"request_id": interaction["request_id"], "decision": "approve"}

    claimed = store.claim_human_decision("session-1", decision)

    assert claimed is not None
    assert claimed["submitted_human_decision"] == decision
    assert store.claim_human_decision("session-1", decision) is None
