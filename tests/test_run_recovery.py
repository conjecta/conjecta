"""Tests for startup recovery of interrupted solve runs."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any, Coroutine

import pytest

import math_agent.web.run_recovery as run_recovery
import math_agent.web.solve_session as solve_session


class FakeOperationsStore:
    def __init__(self, candidates: list[dict[str, Any]]) -> None:
        self._candidates = candidates
        self.mark_calls: list[dict[str, Any]] = []

    def list_running_runs(self, *, since_iso, started_before, limit):
        return self._candidates

    def mark_running_interrupted(self, *, started_before, reason):
        self.mark_calls.append({"started_before": started_before, "reason": reason})


class FakeProjectStore:
    def __init__(self, checkpoints: dict[str, Any]) -> None:
        self._checkpoints = checkpoints

    def get_checkpoint(self, session_id: str):
        return self._checkpoints.get(session_id)


class CollectingPostSolveTasks:
    """Stand-in for the post-solve task manager: keep coroutines for the test."""

    def __init__(self) -> None:
        self.coroutines: list[Coroutine[Any, Any, Any]] = []

    def create(self, coro: Coroutine[Any, Any, Any]):
        self.coroutines.append(coro)
        return None


async def _instant_sleep(_seconds: float) -> None:
    return None


def _wire_recovery(
    monkeypatch: pytest.MonkeyPatch,
    *,
    candidates: list[dict[str, Any]],
    checkpoints: dict[str, Any],
) -> dict[str, Any]:
    """Patch every external dependency of recover_interrupted_runs with fakes."""
    store = FakeOperationsStore(candidates)
    post_solve = CollectingPostSolveTasks()
    finish_calls: list[dict[str, Any]] = []

    async def fake_finish_solve_run(*, session_id, status, reason=None):
        finish_calls.append(
            {"session_id": session_id, "status": status, "reason": reason}
        )

    monkeypatch.setattr(run_recovery, "service_role_configured", lambda: True)
    monkeypatch.setattr(run_recovery, "OperationsStore", lambda: store)
    monkeypatch.setattr(
        run_recovery,
        "project_store_for_user",
        lambda user_id: FakeProjectStore(checkpoints.get(user_id, {})),
    )
    monkeypatch.setattr(run_recovery, "finish_solve_run", fake_finish_solve_run)
    monkeypatch.setattr(run_recovery.agent_factory, "post_solve_tasks", post_solve)
    # run_recovery did ``import asyncio``; swapping the module attribute avoids
    # the 1s stagger per spawn without touching the global asyncio module.
    monkeypatch.setattr(
        run_recovery,
        "asyncio",
        SimpleNamespace(to_thread=asyncio.to_thread, sleep=_instant_sleep),
    )
    return {"store": store, "post_solve": post_solve, "finish_calls": finish_calls}


@pytest.mark.asyncio
async def test_recovery_skips_when_service_role_not_configured(monkeypatch):
    created: list[FakeOperationsStore] = []
    monkeypatch.setattr(run_recovery, "service_role_configured", lambda: False)
    monkeypatch.setattr(
        run_recovery,
        "OperationsStore",
        lambda: created.append(FakeOperationsStore([])) or created[0],
    )

    await run_recovery.recover_interrupted_runs()

    assert created == []


@pytest.mark.asyncio
async def test_recovery_skips_candidate_without_checkpoint(monkeypatch):
    wired = _wire_recovery(
        monkeypatch,
        candidates=[
            {"id": "sess-1", "user_id": "user-1", "project_id": "p1", "mode": "react"}
        ],
        checkpoints={"user-1": {}},
    )

    await run_recovery.recover_interrupted_runs()

    assert len(wired["store"].mark_calls) == 1
    assert wired["store"].mark_calls[0]["reason"] == "server_restart"
    assert wired["post_solve"].coroutines == []
    assert wired["finish_calls"] == []


@pytest.mark.asyncio
async def test_recovery_marks_pending_interaction_waiting_without_resume(monkeypatch):
    wired = _wire_recovery(
        monkeypatch,
        candidates=[
            {"id": "sess-2", "user_id": "user-1", "project_id": "p1", "mode": "react"}
        ],
        checkpoints={
            "user-1": {
                "sess-2": {
                    "session_id": "sess-2",
                    "problem": "P",
                    "pending_interaction": {"request_id": "req-1"},
                }
            }
        },
    )

    await run_recovery.recover_interrupted_runs()

    assert wired["finish_calls"] == [{"session_id": "sess-2", "status": "waiting", "reason": None}]
    assert wired["post_solve"].coroutines == []


@pytest.mark.asyncio
async def test_recovery_resumes_run_with_pinned_session_id(monkeypatch):
    wired = _wire_recovery(
        monkeypatch,
        candidates=[
            {
                "id": "sess-3",
                "user_id": "user-1",
                "project_id": "proj-9",
                "mode": "research",
            }
        ],
        checkpoints={
            "user-1": {"sess-3": {"session_id": "sess-3", "problem": "Prove it."}}
        },
    )
    captured: dict[str, Any] = {}

    async def fake_stream_solve_events(msg, *, user_id=None):
        captured["msg"] = msg
        captured["user_id"] = user_id
        yield {"type": "session", "session_id": msg.get("session_id")}

    monkeypatch.setattr(solve_session, "stream_solve_events", fake_stream_solve_events)

    await run_recovery.recover_interrupted_runs()

    assert len(wired["post_solve"].coroutines) == 1
    await asyncio.gather(*wired["post_solve"].coroutines)

    assert captured["user_id"] == "user-1"
    assert captured["msg"] == {
        "checkpoint_id": "sess-3",
        "session_id": "sess-3",
        "project_id": "proj-9",
        "mode": "research",
    }
    assert wired["finish_calls"] == []


@pytest.mark.asyncio
async def test_recovery_swallows_store_scan_errors(monkeypatch):
    class ExplodingStore:
        def list_running_runs(self, **_kwargs):
            raise RuntimeError("supabase down")

        def mark_running_interrupted(self, **_kwargs):
            raise AssertionError("must not be reached after list failure")

    post_solve = CollectingPostSolveTasks()
    monkeypatch.setattr(run_recovery, "service_role_configured", lambda: True)
    monkeypatch.setattr(run_recovery, "OperationsStore", ExplodingStore)
    monkeypatch.setattr(run_recovery.agent_factory, "post_solve_tasks", post_solve)

    await run_recovery.recover_interrupted_runs()

    assert post_solve.coroutines == []
