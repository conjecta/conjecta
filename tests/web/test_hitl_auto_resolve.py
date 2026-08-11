from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

import math_agent.web.solve_session as solve_session
from math_agent.web import agent_factory, hitl_auto_resolve


def _pending_checkpoint(
    request_id: str = "hitl-1", allowed: list[str] | None = None
) -> dict[str, Any]:
    return {
        "session_id": "sess-1",
        "project_id": "default",
        "strategy": "react",
        "pending_interaction": {
            "request_id": request_id,
            "kind": "reviewer_block",
            "allowed_decisions": allowed
            or ["approve", "reject", "edit", "respond"],
        },
    }


class _FakeStore:
    def __init__(self, checkpoint: dict[str, Any] | None, *, claimable: bool = True):
        self._checkpoint = checkpoint
        self._claimable = claimable
        self.claims: list[dict[str, Any]] = []

    def get_checkpoint(self, _session_id: str) -> dict[str, Any] | None:
        return self._checkpoint

    def claim_human_decision(
        self, _session_id: str, decision: dict[str, Any]
    ) -> dict[str, Any] | None:
        self.claims.append(decision)
        if not self._claimable:
            return None
        self._claimable = False
        return self._checkpoint


def _patch_stream(monkeypatch, sink: list) -> None:
    async def _fake_stream(msg, *, user_id=None):
        sink.append((msg, user_id))
        yield {"type": "done"}

    monkeypatch.setattr(solve_session, "stream_solve_events", _fake_stream)


@pytest.mark.asyncio
async def test_auto_resolve_approves_and_resumes_after_timeout(monkeypatch):
    store = _FakeStore(_pending_checkpoint())
    monkeypatch.setattr(agent_factory, "_project_store", lambda user_id=None: store)
    resumed: list = []
    _patch_stream(monkeypatch, resumed)

    hitl_auto_resolve.schedule_auto_resolve(
        "sess-1", "hitl-1", user_id="user-1", timeout_seconds=0.01
    )
    await asyncio.sleep(0.5)
    await hitl_auto_resolve.shutdown_auto_resolve_timers()

    assert len(store.claims) == 1
    assert store.claims[0]["decision"] == "approve"
    assert store.claims[0]["request_id"] == "hitl-1"
    assert "auto-resolved" in store.claims[0]["feedback"]
    assert len(resumed) == 1
    msg, user_id = resumed[0]
    assert user_id == "user-1"
    assert msg["checkpoint_id"] == "sess-1"
    assert msg["session_id"] == "sess-1"
    assert msg["human_decision"]["request_id"] == "hitl-1"
    assert ("sess-1", "hitl-1") not in hitl_auto_resolve._timers


@pytest.mark.asyncio
async def test_auto_resolve_uses_first_allowed_decision_without_approve(monkeypatch):
    store = _FakeStore(_pending_checkpoint(allowed=["reject", "respond"]))
    monkeypatch.setattr(agent_factory, "_project_store", lambda user_id=None: store)
    resumed: list = []
    _patch_stream(monkeypatch, resumed)

    hitl_auto_resolve.schedule_auto_resolve(
        "sess-1", "hitl-1", user_id="user-1", timeout_seconds=0.01
    )
    await asyncio.sleep(0.5)
    await hitl_auto_resolve.shutdown_auto_resolve_timers()

    assert store.claims[0]["decision"] == "reject"
    assert len(resumed) == 1


@pytest.mark.asyncio
async def test_auto_resolve_backs_off_when_human_claimed_first(monkeypatch):
    store = _FakeStore(_pending_checkpoint(), claimable=False)
    monkeypatch.setattr(agent_factory, "_project_store", lambda user_id=None: store)
    resumed: list = []
    _patch_stream(monkeypatch, resumed)

    hitl_auto_resolve.schedule_auto_resolve(
        "sess-1", "hitl-1", user_id="user-1", timeout_seconds=0.01
    )
    await asyncio.sleep(0.5)
    await hitl_auto_resolve.shutdown_auto_resolve_timers()

    # The claim was attempted but lost the race, so no resume must start.
    assert len(store.claims) == 1
    assert not resumed


@pytest.mark.asyncio
async def test_auto_resolve_skips_stale_request(monkeypatch):
    store = _FakeStore(_pending_checkpoint(request_id="hitl-other"))
    monkeypatch.setattr(agent_factory, "_project_store", lambda user_id=None: store)
    resumed: list = []
    _patch_stream(monkeypatch, resumed)

    hitl_auto_resolve.schedule_auto_resolve(
        "sess-1", "hitl-1", user_id="user-1", timeout_seconds=0.01
    )
    await asyncio.sleep(0.5)
    await hitl_auto_resolve.shutdown_auto_resolve_timers()

    assert not store.claims
    assert not resumed


@pytest.mark.asyncio
async def test_auto_resolve_disabled_when_timeout_not_positive(monkeypatch):
    store = _FakeStore(_pending_checkpoint())
    monkeypatch.setattr(agent_factory, "_project_store", lambda user_id=None: store)

    hitl_auto_resolve.schedule_auto_resolve(
        "sess-1", "hitl-1", user_id="user-1", timeout_seconds=0
    )
    hitl_auto_resolve.schedule_auto_resolve(
        "sess-1", "hitl-2", user_id="user-1", timeout_seconds=-5
    )
    await asyncio.sleep(0.1)

    assert not store.claims
    assert not hitl_auto_resolve._timers


def test_configured_timeout_from_config(monkeypatch):
    config = SimpleNamespace(
        agent=SimpleNamespace(hitl=SimpleNamespace(auto_resolve_seconds=2.5))
    )
    monkeypatch.setattr(hitl_auto_resolve, "load_config", lambda: config)
    assert hitl_auto_resolve.configured_timeout() == 2.5

    config.agent.hitl.auto_resolve_seconds = 0
    assert hitl_auto_resolve.configured_timeout() == 0.0

    monkeypatch.setattr(
        hitl_auto_resolve,
        "load_config",
        lambda: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    assert hitl_auto_resolve.configured_timeout() == 0.0


@pytest.mark.asyncio
async def test_schedule_respects_disabled_config(monkeypatch):
    config = SimpleNamespace(
        agent=SimpleNamespace(hitl=SimpleNamespace(auto_resolve_seconds=0))
    )
    monkeypatch.setattr(hitl_auto_resolve, "load_config", lambda: config)
    store = _FakeStore(_pending_checkpoint())
    monkeypatch.setattr(agent_factory, "_project_store", lambda user_id=None: store)

    hitl_auto_resolve.schedule_auto_resolve("sess-1", "hitl-1", user_id="user-1")
    await asyncio.sleep(0.05)

    assert not store.claims
    assert not hitl_auto_resolve._timers


@pytest.mark.asyncio
async def test_cancel_auto_resolve_stops_timer(monkeypatch):
    store = _FakeStore(_pending_checkpoint())
    monkeypatch.setattr(agent_factory, "_project_store", lambda user_id=None: store)

    hitl_auto_resolve.schedule_auto_resolve(
        "sess-1", "hitl-1", user_id="user-1", timeout_seconds=60
    )
    assert ("sess-1", "hitl-1") in hitl_auto_resolve._timers

    hitl_auto_resolve.cancel_auto_resolve("sess-1", "hitl-1")
    await asyncio.sleep(0.05)

    assert not hitl_auto_resolve._timers
    assert not store.claims
