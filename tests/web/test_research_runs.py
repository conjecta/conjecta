"""Unit tests for research-run checkpoint projections."""
from __future__ import annotations

import math_agent.web.project_store as project_store
from math_agent.web.project_store import ProjectStore
from math_agent.web.research_runs import (
    ShareStore,
    artifact_path_for,
    inbox_entry,
    is_research_checkpoint,
    run_detail,
    run_status,
    summarize_run,
)


def _checkpoint(**overrides):
    base = {
        "session_id": "sess-1",
        "project_id": "default",
        "strategy": "research",
        "problem": "证明  √2  是无理数。\n第二行",
        "proof_graph": {
            "root_id": "root",
            "active_goal_id": "g1",
            "goals": [
                {"id": "root", "statement": "原命题", "status": "in_progress", "depends_on": ["g1", "g2"]},
                {"id": "g1", "statement": "引理一", "status": "proved", "depends_on": []},
                {"id": "g2", "statement": "引理二", "status": "pending", "depends_on": []},
            ],
        },
        "research_artifacts": [
            {
                "id": "research-abc",
                "goal_id": "g1",
                "goal_statement": "引理一",
                "attempt_index": 1,
                "strategy": "direct",
                "status": "reviewed",
                "answer": "完整证明全文，不应出现在 detail 里",
                "summary": "摘要",
                "verification_status": "reviewed",
                "verification_issues": [],
                "created_at": "2026-07-16T08:00:00+00:00",
                "path": "",
            }
        ],
        "research_failures": [{"goal_id": "g2", "reason": "verification_failed", "summary": "x" * 800}],
        "budget_consumption": {"research_budget_extensions": 2},
        "pending_interaction": None,
        "at": "2026-07-16T08:00:00+00:00",
    }
    base.update(overrides)
    return base


def test_is_research_checkpoint():
    assert is_research_checkpoint(_checkpoint())
    assert not is_research_checkpoint(_checkpoint(strategy="react"))
    assert not is_research_checkpoint(None)
    assert not is_research_checkpoint({})


def test_run_status_waiting_human_wins_over_active():
    pending = {"request_id": "r1", "kind": "budget_extend"}
    assert run_status(_checkpoint(pending_interaction=pending), active=True) == "waiting_human"


def test_run_status_running_completed_best_effort():
    assert run_status(_checkpoint(), active=True) == "running"
    proved_graph = {
        "root_id": "root",
        "goals": [{"id": "root", "statement": "原命题", "status": "proved", "depends_on": []}],
    }
    assert run_status(_checkpoint(proof_graph=proved_graph), active=False) == "completed"
    assert run_status(_checkpoint(), active=False) == "best_effort"


def test_summarize_run_counts_and_excerpt():
    summary = summarize_run(_checkpoint(), active=False)
    assert summary["goals_total"] == 2
    assert summary["goals_proved"] == 1
    assert summary["budget_extensions"] == 2
    assert summary["status"] == "best_effort"
    assert "\n" not in summary["problem_excerpt"]
    assert summary["has_pending_interaction"] is False


def test_run_detail_strips_private_sections_and_claim_fields():
    pending = {
        "request_id": "r1",
        "kind": "counterexample",
        "stage": "refutation",
        "question": "是否接受修订?",
        "details": {"goal_id": "g2"},
        "allowed_decisions": ["approve", "reject"],
        "decision_claim_id": "secret-claim",
    }
    detail = run_detail(
        _checkpoint(
            pending_interaction=pending,
            submitted_human_decision={"decision": "approve", "feedback": "owner secret"},
        ),
        active=False,
    )
    assert "answer" not in detail["research_artifacts"][0]
    assert detail["research_artifacts"][0]["summary"] == "摘要"
    assert "decision_claim_id" not in detail["pending_interaction"]
    assert detail["pending_interaction"]["request_id"] == "r1"
    assert detail["status"] == "waiting_human"
    assert len(detail["research_failures"][0]["summary"]) == 600
    for private_key in (
        "project_context",
        "context_preamble",
        "turns",
        "human_decisions",
        "submitted_human_decision",
    ):
        assert private_key not in detail


def test_inbox_entry_skips_claimed_and_computes_waiting():
    assert inbox_entry(_checkpoint()) is None
    pending = {
        "request_id": "r1",
        "kind": "plan_review",
        "question": "继续?",
        "allowed_decisions": ["approve", "reject"],
    }
    entry = inbox_entry(_checkpoint(pending_interaction=pending))
    assert entry is not None
    assert entry["session_id"] == "sess-1"
    assert entry["kind"] == "plan_review"
    assert entry["waiting_seconds"] >= 0
    claimed = dict(pending, decision_claim_id="c1")
    assert inbox_entry(_checkpoint(pending_interaction=claimed)) is None


def test_inbox_entry_uses_interaction_created_at():
    pending = {
        "request_id": "r1",
        "kind": "plan_review",
        "question": "继续?",
        "allowed_decisions": ["approve", "reject"],
        "created_at": "2026-07-16T06:00:00+00:00",
    }
    # checkpoint["at"] is 2026-07-16T08:00:00+00:00, two hours after created_at
    entry = inbox_entry(_checkpoint(pending_interaction=pending))
    assert entry is not None
    assert entry["waiting_seconds"] >= 7200


def test_artifact_path_for_validates_session_dir(tmp_path):
    artifact_root = tmp_path / "artifacts"
    session_dir = artifact_root / "sess-1"
    session_dir.mkdir(parents=True)
    good = session_dir / "g1-1-abc.json"
    good.write_text("{}", encoding="utf-8")
    nested_dir = session_dir / "nested"
    nested_dir.mkdir(parents=True)
    nested = nested_dir / "g1-2-abc.json"
    nested.write_text("{}", encoding="utf-8")
    outside = tmp_path / "elsewhere.json"
    outside.write_text("{}", encoding="utf-8")
    checkpoint = _checkpoint(research_artifacts=[{"id": "a1", "path": str(good)}])
    assert artifact_path_for(checkpoint, "a1", artifact_root=artifact_root) == good.resolve()
    nested_checkpoint = _checkpoint(research_artifacts=[{"id": "a2", "path": str(nested)}])
    assert artifact_path_for(nested_checkpoint, "a2", artifact_root=artifact_root) == nested.resolve()
    assert artifact_path_for(checkpoint, "missing", artifact_root=artifact_root) is None
    evil = _checkpoint(research_artifacts=[{"id": "a3", "path": str(outside)}])
    assert artifact_path_for(evil, "a3", artifact_root=artifact_root) is None


def test_list_checkpoints_returns_recent_first(tmp_path, monkeypatch):
    store = ProjectStore(root=tmp_path)
    times = iter(["2026-07-16T08:00:00+00:00", "2026-07-16T09:00:00+00:00"])
    monkeypatch.setattr(project_store, "_now", lambda: next(times))
    store.write_checkpoint({"session_id": "a", "strategy": "research"})
    store.write_checkpoint({"session_id": "b", "strategy": "react"})
    checkpoints = store.list_checkpoints()
    assert [c["session_id"] for c in checkpoints] == ["b", "a"]


def test_share_store_roundtrip_and_revoke(tmp_path):
    shares = ShareStore(root=tmp_path)
    record = shares.create(user_id="u_1", session_id="sess-1")
    assert record["token"] and len(record["token"]) >= 24
    again = shares.create(user_id="u_1", session_id="sess-1")
    assert again["token"] == record["token"]
    assert shares.get(record["token"])["session_id"] == "sess-1"
    assert shares.get("nope") is None
    assert shares.revoke(record["token"], user_id="u_2") is False
    assert shares.revoke(record["token"], user_id="u_1") is True
    assert shares.get(record["token"]) is None
    fresh = shares.create(user_id="u_1", session_id="sess-1")
    assert fresh["token"] != record["token"]


def test_share_store_survives_reload(tmp_path):
    record = ShareStore(root=tmp_path).create(user_id="u_1", session_id="sess-1")
    reloaded = ShareStore(root=tmp_path)
    assert reloaded.get(record["token"])["user_id"] == "u_1"
