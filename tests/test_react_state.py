from dataclasses import FrozenInstanceError

import pytest

from math_agent.agent.react_state import (
    Action,
    ProjectContext,
    ReActSolution,
    ReActTrace,
    ReActTurn,
    ReviewResult,
    ToolObservation,
)


def test_trace_context_window_includes_problem_and_turns():
    trace = ReActTrace(problem="Prove sqrt(2) is irrational")
    trace.turns.append(
        ReActTurn(
            thought="I recall a standard theorem.",
            action=Action(name="search_knowledge", args={"query": "sqrt 2 irrational"}),
            observation=ToolObservation(
                success=True, output="Fact: irrational_sqrt_two exists."
            ),
            step_num=1,
        )
    )
    window = trace.context_window()
    assert "Prove sqrt(2) is irrational" in window
    assert "search_knowledge" in window
    assert "irrational_sqrt_two" in window


def test_trace_last_turn_returns_most_recent_turn():
    trace = ReActTrace(problem="P")
    assert trace.last_turn() is None
    turn1 = ReActTurn(
        thought="t1",
        action=Action(name="a1"),
        observation=ToolObservation(success=True, output="o1"),
        step_num=1,
    )
    turn2 = ReActTurn(
        thought="t2",
        action=Action(name="a2"),
        observation=ToolObservation(success=True, output="o2"),
        step_num=2,
    )
    trace.turns.extend([turn1, turn2])
    assert trace.last_turn() is turn2


def test_trace_context_window_respects_max_turns():
    trace = ReActTrace(problem="P")
    for i in range(12):
        trace.turns.append(
            ReActTurn(
                thought=f"t{i}",
                action=Action(name=f"a{i}"),
                observation=ToolObservation(success=True, output=f"o{i}"),
                step_num=i,
            )
        )
    window = trace.context_window(max_turns=5)
    assert "a11" in window
    assert "a7" in window
    assert "a6" not in window


def test_trace_context_window_includes_reviews():
    trace = ReActTrace(problem="P")
    trace.turns.append(
        ReActTurn(
            thought="t",
            action=Action(name="a"),
            observation=ToolObservation(success=True, output="o"),
            reviews=[ReviewResult(reviewer="critic", verdict="FAIL", issues=["bad"])],
            step_num=1,
        )
    )
    window = trace.context_window()
    assert "Review (critic): FAIL" in window
    assert "Issues: bad" in window


def test_trace_context_window_includes_current_goal():
    trace = ReActTrace(problem="P", current_goal="find a contradiction")
    window = trace.context_window()
    assert "Current goal: find a contradiction" in window


def test_context_window_keeps_active_goal_and_recent_turn_when_problem_is_huge():
    trace = ReActTrace(
        problem="P" * 50_000,
        current_goal="critical leaf lemma",
        research_mode=True,
    )
    trace.turns.append(
        ReActTurn(
            thought="latest local reasoning",
            action=Action(name="think", args={"text": "continue"}),
            observation=ToolObservation(success=True, output="latest evidence"),
            step_num=1,
        )
    )

    window = trace.context_window(max_chars=10_000)

    assert len(window) <= 10_000
    assert "critical leaf lemma" in window
    assert "latest local reasoning" in window
    assert "latest evidence" in window


def test_research_checkpoint_round_trip_preserves_artifacts_and_failures():
    trace = ReActTrace(
        problem="P",
        research_mode=True,
        research_artifacts=[
            {"id": "research-1", "status": "reviewed", "goal_statement": "L"}
        ],
        research_failures=[{"goal_id": "lemma", "summary": "route failed"}],
        compacted_summary="proved L",
    )

    restored = ReActTrace.from_checkpoint(trace.to_checkpoint(strategy="research"))

    assert restored.research_mode is True
    assert restored.research_artifacts[0]["id"] == "research-1"
    assert restored.research_failures[0]["summary"] == "route failed"
    assert restored.compacted_summary == "proved L"


def test_solution_summary_with_passing_reviews():
    turn = ReActTurn(
        thought="Use the irrationality theorem.",
        action=Action(name="search_knowledge"),
        observation=ToolObservation(success=True, output="found"),
        reviews=[ReviewResult(reviewer="critic", verdict="PASS")],
        step_num=1,
    )
    solution = ReActSolution(
        problem="P",
        turns=[turn],
        final_answer="Done.",
        lean_proofs=["proof1"],
    )
    summary = solution.summary()
    assert "Problem: P" in summary
    assert "✓ Step 1" in summary
    assert "Answer: Done." in summary
    assert "Lean proofs verified: 1" in summary


def test_solution_summary_with_failing_review():
    turn = ReActTurn(
        thought="Guess.",
        action=Action(name="search_knowledge"),
        observation=ToolObservation(success=True, output="found"),
        reviews=[ReviewResult(reviewer="critic", verdict="FAIL", issues=["invented"])],
        step_num=1,
    )
    solution = ReActSolution(problem="P", turns=[turn], final_answer="Done.")
    summary = solution.summary()
    assert "○ Step 1" in summary


def test_solution_verification_metadata_defaults():
    solution = ReActSolution(problem="P", turns=[], final_answer="Partial answer.")

    assert solution.verification_status == "best_effort"
    assert solution.verification_issues == []
    assert solution.trace is None


def test_action_is_frozen():
    action = Action(name="a", args={"x": 1})
    with pytest.raises(FrozenInstanceError):
        action.name = "b"


def test_tool_observation_defaults():
    obs = ToolObservation(success=False, output="err")
    assert obs.lean_code is None
    assert obs.error is None


def test_project_context_defaults():
    ctx = ProjectContext()
    assert ctx.project_id is None
    assert ctx.facts == []
    assert ctx.intuitions == []
    assert ctx.tricks == []


def test_review_result_defaults():
    review = ReviewResult(reviewer="critic", verdict="PASS")
    assert review.issues == []
    assert review.suggestions == []
    assert review.confidence == 0.0


def test_project_context_user_id():
    ctx = ProjectContext(project_id="p1", user_id="u-1")
    assert ctx.user_id == "u-1"


def test_trace_checkpoint_round_trip_preserves_user_id():
    trace = ReActTrace(
        problem="P",
        project_context=ProjectContext(project_id="p1", user_id="u-1"),
    )
    checkpoint = trace.to_checkpoint()
    assert checkpoint["project_context"]["user_id"] == "u-1"

    restored = ReActTrace.from_checkpoint(checkpoint)
    assert restored.project_context.user_id == "u-1"
    assert restored.project_context.project_id == "p1"


def test_trace_checkpoint_round_trip_preserves_resume_state_and_formal_evidence():
    trace = ReActTrace(
        problem="Formalize P in Lean 4",
        current_goal="prove helper lemma",
        plan_text="1. Prove helper.\n2. Close P.",
        context_preamble="bounded source excerpt",
        next_step_num=5,
        budget_consumption={
            "conclusion_revisions": 1,
            "search_mathlib_calls": 2,
        },
        project_context=ProjectContext(project_id="project-1"),
    )
    trace.turns.append(
        ReActTurn(
            thought="The first formal attempt failed.",
            action=Action(
                name="lean_check",
                args={"code": "theorem p : True := by trivial", "attempt": 1},
            ),
            observation=ToolObservation(
                success=False,
                output="type mismatch",
                lean_code="theorem p : True := by trivial",
                error="Lean exited with status 1",
                metadata={
                    "formal_evidence": {
                        "id": "formal-test",
                        "target_claim": "prove helper lemma",
                    }
                },
            ),
            reviews=[
                ReviewResult(
                    reviewer="critic",
                    verdict="FAIL",
                    issues=["The theorem statement changed."],
                    suggestions=["Preserve the original statement."],
                    confidence=0.91,
                )
            ],
            step_num=4,
        )
    )
    trace.proof_graph.ensure_root(trace.problem)
    trace.proof_graph.upsert_goal(
        "prove helper lemma",
        goal_id="helper",
        activate=True,
    )
    trace.proof_graph.record_formal_attempt(
        success=False,
        evidence_id="formal-test",
        issue="type mismatch",
    )

    checkpoint = trace.to_checkpoint()
    restored = ReActTrace.from_checkpoint(
        checkpoint,
        project_context=ProjectContext(project_id="project-1"),
    )

    assert restored.problem == trace.problem
    assert restored.current_goal == trace.current_goal
    assert restored.plan_text == trace.plan_text
    assert restored.context_preamble == trace.context_preamble
    assert restored.next_step_num == 5
    assert restored.budget_consumption == trace.budget_consumption
    assert restored.project_context.project_id == "project-1"
    assert restored.proof_graph.active_goal_id == "helper"
    assert restored.proof_graph.goals["helper"].attempts == 1
    assert len(restored.turns) == 1
    turn = restored.turns[0]
    assert turn.step_num == 4
    assert turn.thought == trace.turns[0].thought
    assert turn.action.name == "lean_check"
    assert turn.action.args["attempt"] == 1
    assert turn.observation.success is False
    assert turn.observation.output == "type mismatch"
    assert turn.observation.lean_code == "theorem p : True := by trivial"
    assert turn.observation.error == "Lean exited with status 1"
    assert turn.observation.metadata["formal_evidence"]["id"] == "formal-test"
    assert turn.reviews == trace.turns[0].reviews


def test_legacy_checkpoint_without_version_still_hydrates():
    restored = ReActTrace.from_checkpoint(
        {
            "problem": "P",
            "current_goal": "G",
            "turns": [
                {
                    "step_num": 2,
                    "thought": "legacy",
                    "action": {"name": "think", "args": {"text": "x"}},
                    "observation": "recorded",
                }
            ],
        }
    )

    assert restored.problem == "P"
    assert restored.current_goal == "G"
    assert restored.turns[0].observation.output == "recorded"


def test_checkpoint_rejects_unsupported_schema_version():
    with pytest.raises(ValueError, match="Unsupported checkpoint schema version"):
        ReActTrace.from_checkpoint({"schema_version": 5, "problem": "P", "turns": []})


@pytest.mark.parametrize("problem", [None, 17, ["P"], {"text": "P"}])
def test_checkpoint_rejects_non_string_problem(problem):
    with pytest.raises(ValueError, match="checkpoint.*problem.*string"):
        ReActTrace.from_checkpoint(
            {"schema_version": 2, "problem": problem, "turns": []}
        )


@pytest.mark.parametrize(
    "checkpoint",
    [
        {"problem": "P", "turns": "not-a-list"},
        {"problem": "P", "turns": None},
        {"problem": "P", "turns": ["not-an-object"]},
        {"problem": "P", "turns": [], "project_context": "not-an-object"},
        {"problem": "P", "turns": [], "project_context": None},
        {
            "problem": "P",
            "turns": [],
            "project_context": {"facts": "not-a-list"},
        },
        {
            "problem": "P",
            "turns": [],
            "project_context": {"facts": None},
        },
        {
            "problem": "P",
            "turns": [],
            "project_context": {"intuitions": "not-a-list"},
        },
        {
            "problem": "P",
            "turns": [],
            "project_context": {"tricks": "not-a-list"},
        },
        {"problem": "P", "turns": [], "budget_consumption": "not-an-object"},
        {"problem": "P", "turns": [], "budget_consumption": None},
        {
            "problem": "P",
            "turns": [{"action": {"name": "think"}, "reviews": "not-a-list"}],
        },
        {
            "problem": "P",
            "turns": [{"action": {"name": "think"}, "reviews": None}],
        },
        {
            "problem": "P",
            "turns": [{"action": {"name": "think"}, "reviews": ["not-an-object"]}],
        },
        {
            "problem": "P",
            "turns": [
                {
                    "action": {"name": "think"},
                    "reviews": [{"issues": "not-a-list"}],
                }
            ],
        },
        {
            "problem": "P",
            "turns": [
                {
                    "action": {"name": "think"},
                    "reviews": [{"suggestions": "not-a-list"}],
                }
            ],
        },
    ],
)
def test_checkpoint_rejects_malformed_containers(checkpoint):
    with pytest.raises(ValueError, match="checkpoint"):
        ReActTrace.from_checkpoint(checkpoint)


def test_context_window_includes_compacted_summary_outside_research_mode():
    trace = ReActTrace(problem="P", compacted_summary="proved lemma L earlier")
    trace.turns.append(
        ReActTurn(
            thought="continue from lemma L",
            action=Action(name="think", args={"text": "use L"}),
            observation=ToolObservation(success=True, output="ok"),
            step_num=12,
        )
    )

    window = trace.context_window(max_chars=10_000)

    assert "Earlier work summary:" in window
    assert "proved lemma L earlier" in window


def test_compacted_turn_count_checkpoint_round_trip():
    trace = ReActTrace(
        problem="P",
        compacted_summary="summary so far",
        compacted_turn_count=4,
    )

    restored = ReActTrace.from_checkpoint(trace.to_checkpoint())

    assert restored.compacted_summary == "summary so far"
    assert restored.compacted_turn_count == 4


def test_legacy_checkpoint_without_compaction_fields_restores_defaults():
    checkpoint = {
        "schema_version": 4,
        "problem": "P",
        "turns": [],
    }

    restored = ReActTrace.from_checkpoint(checkpoint)

    assert restored.compacted_summary == ""
    assert restored.compacted_turn_count == 0


def test_review_result_abstained_only_for_unavailable():
    assert ReviewResult(reviewer="critic", verdict="UNAVAILABLE").abstained
    assert not ReviewResult(reviewer="critic", verdict="PASS").abstained
    assert not ReviewResult(reviewer="critic", verdict="FAIL").abstained
