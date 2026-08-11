import pytest

from math_agent.agent.proof_graph import ProofGraph


def test_proof_graph_tracks_dependencies_and_ready_goals():
    graph = ProofGraph()
    root = graph.ensure_root("Prove the main theorem")
    helper = graph.upsert_goal("Prove helper", goal_id="helper")
    main = graph.upsert_goal(
        "Close main theorem",
        goal_id="main",
        depends_on=[helper.id],
    )

    assert {goal.id for goal in graph.ready_goals()} == {helper.id}
    graph.activate(helper.id)
    graph.record_formal_attempt(success=True, evidence_id="formal-helper")

    assert helper.status == "proved"
    assert helper.evidence_id == "formal-helper"
    assert {goal.id for goal in graph.ready_goals()} == {main.id}
    assert root.status == "in_progress"


def test_proof_graph_rejects_cycles_without_corrupting_existing_goals():
    graph = ProofGraph()
    graph.upsert_goal("A", goal_id="a")
    graph.upsert_goal("B", goal_id="b", depends_on=["a"])

    with pytest.raises(ValueError, match="acyclic"):
        graph.upsert_goal("A", goal_id="a", depends_on=["b"])

    assert graph.goals["a"].depends_on == []
    assert graph.goals["b"].depends_on == ["a"]


def test_proof_graph_round_trip_preserves_evidence_and_active_goal():
    graph = ProofGraph()
    graph.ensure_root("Root")
    helper = graph.upsert_goal("Helper", goal_id="helper", activate=True)
    graph.record_formal_attempt(success=False, evidence_id="formal-failed", issue="gap")

    restored = ProofGraph.from_dict(graph.to_dict())

    assert restored.active_goal_id == helper.id
    assert restored.goals[helper.id].attempts == 1
    assert restored.goals[helper.id].issues == ["gap"]


def test_reset_goal_cascades_to_dependents_and_reverts_root():
    graph = ProofGraph()
    root = graph.ensure_root("Prove the main theorem")
    graph.upsert_goal("Prove helper", goal_id="helper")
    graph.upsert_goal("Close main theorem", goal_id="main", depends_on=["helper"])
    root.depends_on = ["main"]
    graph.mark_proved("helper", evidence_id="artifact-helper")
    graph.mark_proved("main", evidence_id="artifact-main")
    graph.mark_proved(root.id, evidence_id="artifact-root")
    graph.record_attempt("helper", {"strategy": "direct", "status": "proved"})

    reset = graph.reset_goal("helper")

    assert reset == ["helper", "main", root.id]
    for goal_id in reset:
        goal = graph.goals[goal_id]
        assert goal.status == "pending"
        assert goal.evidence_id == ""
        assert goal.accepted_artifact_id == ""
    # attempts and attempts_log stay as an audit trail
    assert graph.goals["helper"].attempts == 1
    assert graph.goals["helper"].attempts_log == [
        {"strategy": "direct", "status": "proved"}
    ]


def test_reset_goal_leaves_unrelated_goals_alone():
    graph = ProofGraph()
    graph.ensure_root("Root")
    graph.upsert_goal("A", goal_id="a")
    graph.upsert_goal("B", goal_id="b", depends_on=["a"])
    graph.upsert_goal("Independent", goal_id="keep")
    graph.mark_proved("a", evidence_id="art-a")
    graph.mark_proved("keep", evidence_id="art-keep")

    reset = graph.reset_goal("a")

    assert reset == ["a", "b"]
    assert graph.goals["keep"].status == "proved"
    assert graph.goals["keep"].accepted_artifact_id == "art-keep"


def test_reset_goal_without_cascade_only_resets_target():
    graph = ProofGraph()
    graph.ensure_root("Root")
    graph.upsert_goal("A", goal_id="a")
    graph.upsert_goal("B", goal_id="b", depends_on=["a"])
    graph.mark_proved("a", evidence_id="art-a")
    graph.mark_proved("b", evidence_id="art-b")

    reset = graph.reset_goal("a", cascade=False)

    assert reset == ["a"]
    assert graph.goals["a"].status == "pending"
    assert graph.goals["b"].status == "proved"


def test_reset_goal_rejects_unknown_goal():
    graph = ProofGraph()
    graph.ensure_root("Root")

    with pytest.raises(KeyError, match="Unknown proof goal"):
        graph.reset_goal("nope")


def test_reset_goal_terminates_on_hand_built_dependency_cycle():
    graph = ProofGraph()
    graph.upsert_goal("A", goal_id="a")
    graph.upsert_goal("B", goal_id="b", depends_on=["a"])
    # upsert_goal forbids cycles; simulate a corrupted graph to prove the
    # cascade walk cannot loop forever.
    graph.goals["a"].depends_on.append("b")

    reset = graph.reset_goal("a")

    assert sorted(reset) == ["a", "b"]
    assert graph.goals["a"].status == "pending"
    assert graph.goals["b"].status == "pending"


def test_edit_goal_statement_keeps_id_and_resets_dependents():
    graph = ProofGraph()
    graph.ensure_root("Root")
    graph.upsert_goal("Old statement", goal_id="lemma")
    graph.upsert_goal("Downstream", goal_id="down", depends_on=["lemma"])
    graph.mark_proved("lemma", evidence_id="art-lemma")
    graph.mark_proved("down", evidence_id="art-down")

    reset = graph.edit_goal_statement("lemma", "  New precise statement  ")

    assert reset == ["lemma", "down"]
    goal = graph.goals["lemma"]
    assert goal.id == "lemma"
    assert goal.statement == "New precise statement"
    assert goal.status == "pending"
    assert goal.evidence_id == ""
    assert graph.goals["down"].status == "pending"


def test_edit_goal_statement_rejects_empty_statement_and_unknown_goal():
    graph = ProofGraph()
    graph.upsert_goal("Old statement", goal_id="lemma")

    with pytest.raises(ValueError, match="cannot be empty"):
        graph.edit_goal_statement("lemma", "   ")
    assert graph.goals["lemma"].statement == "Old statement"

    with pytest.raises(KeyError, match="Unknown proof goal"):
        graph.edit_goal_statement("nope", "Replacement")
