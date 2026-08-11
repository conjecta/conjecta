from __future__ import annotations

import hashlib

import pytest

import math_agent.agent.context_augmentor as context_augmentor
from math_agent.agent.context_augmentor import ContextAugmentor
from math_agent.agent.context_augmentor import MemorySnippet
from math_agent.agent.plan_memory import PlanMemory
from math_agent.agent.planner import FormalizationPlan


@pytest.mark.asyncio
async def test_recorded_plan_is_retrieved_and_injected(tmp_path):
    memory = PlanMemory(tmp_path / "runtime.jsonl", seed_path=None)
    memory.record(
        "Prove every even square is divisible by four",
        "theorem",
        FormalizationPlan(
            problem="Prove every even square is divisible by four",
            goal_type="theorem",
            proof_strategy="Write the even integer as twice another integer and expand.",
            lemmas=[{"statement": "An even integer has the form 2k."}],
        ),
    )

    result = await ContextAugmentor(plan_memory=memory).augment(
        "Show that an even integer has square divisible by four",
        project_id=None,
    )

    assert "Related proof strategy" in result.prompt
    assert "Write the even integer as twice another integer and expand." in result.prompt
    assert result.memories_used[0].kind == "plan"


@pytest.mark.asyncio
async def test_augmentor_emits_memory_retrieval_events():
    events = []

    async def on_event(event):
        events.append(event)

    class Store:
        def search_facts(self, *_args, **_kwargs):
            return [{
                "id": "f1",
                "statement": "A fact",
                "status": "approved",
                "score": "0.82",
                "evidence": "private proof artifact",
                "source_ref": "/srv/private/proof.lean",
            }]
        def search_intuitions(self, *_args, **_kwargs):
            return []
        def search_tricks(self, *_args, **_kwargs):
            return []

    augmentor = ContextAugmentor(knowledge_store=Store())
    await augmentor.augment("problem", "project", session_id="s-1", on_event=on_event)

    assert len(events) == 1
    assert events[0]["type"] == "memory_retrieval"
    assert events[0]["session_id"] == "s-1"
    assert events[0]["memory_id"] == "f1"
    assert events[0]["kind"] == "fact"
    assert events[0]["status"] == "approved"
    assert events[0]["rank"] == 1
    assert events[0]["retrieval_score"] == 0.82
    assert events[0]["has_evidence"] is True
    assert "evidence" not in events[0]
    assert "source_ref" not in events[0]


@pytest.mark.asyncio
async def test_plan_retrieval_uses_bounded_trusted_threshold():
    plan = FormalizationPlan(proof_strategy="Use the prior strategy.")

    class RecordingMemory:
        def __init__(self):
            self.calls = []

        def retrieve(self, problem, *, k, min_score):
            self.calls.append((problem, k, min_score))
            return [plan]

    memory = RecordingMemory()
    result = await ContextAugmentor(plan_memory=memory).augment("A problem", None)

    assert memory.calls == [("A problem", 1, 0.1)]
    assert "Use the prior strategy." in result.prompt


@pytest.mark.asyncio
async def test_augmentor_defensively_excludes_untrusted_store_results():
    class LeakyStore:
        def search_facts(self, *_args, **_kwargs):
            return [
                {"statement": "candidate memory", "status": "candidate", "confidence": "0.8"},
                {"statement": "low confidence leak", "status": "candidate", "confidence": "0.2"},
                {"statement": "questioned leak", "status": "questioned", "confidence": "0.9"},
                {"statement": "rejected leak", "status": "rejected"},
                {"statement": "deprecated leak", "status": "deprecated"},
                {"statement": "approved fact", "status": "approved"},
                {"statement": "zero confidence leak", "status": "verified", "confidence": 0},
            ]

        def search_intuitions(self, *_args, **_kwargs):
            return [{"title": "verified idea", "body": "safe", "status": "verified"}]

        def search_tricks(self, *_args, **_kwargs):
            return [{"title": "statusless leak", "body": "unsafe"}]

    result = await ContextAugmentor(knowledge_store=LeakyStore()).augment(
        "problem", "project"
    )

    assert "approved fact" in result.prompt
    assert "candidate memory" not in result.prompt
    assert "verified idea" in result.prompt
    assert "low confidence leak" not in result.prompt
    assert "questioned leak" not in result.prompt
    assert "rejected leak" not in result.prompt
    assert "deprecated leak" not in result.prompt
    assert "statusless leak" not in result.prompt
    assert "zero confidence leak" not in result.prompt
    assert [(m.kind, m.status) for m in result.memories_used] == [
        ("fact", "approved"),
        ("intuition", "verified"),
    ]


@pytest.mark.asyncio
async def test_augmentor_ranks_status_score_and_confidence():
    class RankedStore:
        def search_facts(self, *_args, **_kwargs):
            return [
                {"statement": "candidate high score", "status": "candidate", "score": "1.0", "confidence": "0.95"},
                {"statement": "approved low score", "status": "approved", "score": "0.1", "confidence": "0.6"},
                {"statement": "verified no score", "status": "verified", "confidence": "0.5"},
                {"statement": "approved high score", "status": "approved", "score": "0.9", "confidence": "0.6"},
                {"statement": "approved lower confidence", "status": "approved", "score": "0.9", "confidence": "0.5"},
            ]

        def search_intuitions(self, *_args, **_kwargs):
            return []

        def search_tricks(self, *_args, **_kwargs):
            return []

    result = await ContextAugmentor(knowledge_store=RankedStore()).augment(
        "problem", "project"
    )

    assert [m.text for m in result.memories_used] == [
        "verified no score",
        "approved high score",
        "approved lower confidence",
        "approved low score",
    ]
    assert "candidate high score" not in result.prompt


@pytest.mark.asyncio
async def test_memory_snippet_carries_id_and_evidence():
    class Store:
        def search_facts(self, *_args, **_kwargs):
            return [{
                "id": "fact-123",
                "statement": "A theorem",
                "status": "verified",
                "evidence": "Lean artifact A17",
                "source_ref": "lean://A17",
                "confidence": "0.95",
                "score": "0.9",
            }]
        def search_intuitions(self, *_args, **_kwargs):
            return []
        def search_tricks(self, *_args, **_kwargs):
            return []

    result = await ContextAugmentor(knowledge_store=Store()).augment("problem", "project")
    snippet = result.memories_used[0]
    assert snippet.memory_id == "fact-123"
    assert snippet.evidence == "Lean artifact A17"
    assert snippet.source_ref == "lean://A17"
    assert snippet.status == "verified"


@pytest.mark.asyncio
async def test_prompt_renders_typed_sections():
    class Store:
        def search_facts(self, *_args, **_kwargs):
            return [{"id": "f1", "statement": "A fact", "status": "verified", "evidence": "lean"}]
        def search_intuitions(self, *_args, **_kwargs):
            return [{"id": "i1", "title": "An intuition", "body": "body", "status": "reviewed"}]
        def search_tricks(self, *_args, **_kwargs):
            return [{
                "id": "t1",
                "title": "A trick",
                "body": "body",
                "applicability": "when X",
                "failure_mode": "fails if Y",
                "status": "approved",
            }]

    result = await ContextAugmentor(knowledge_store=Store()).augment("Solve this", "project")
    prompt = result.prompt
    assert "Verified / reviewed facts:" in prompt
    assert "[f1, verified]" in prompt or "f1" in prompt
    assert "Useful intuitions:" in prompt
    assert "[i1, reviewed]" in prompt
    assert "Applicable strategies:" in prompt
    assert "[t1, approved]" in prompt
    assert "Applies when:" in prompt
    assert "Failure mode:" in prompt
    assert "Applies when: when X" in prompt
    assert "Failure mode: fails if Y" in prompt
    assert "Limitation:" in prompt or "Limitation" in prompt


@pytest.mark.asyncio
async def test_memory_section_is_bounded_to_max_chars():
    """The rendered memory portion (excluding the problem) must not exceed 5000 chars."""
    long_text = "x" * 3000

    class Store:
        def search_facts(self, *_args, **_kwargs):
            return [
                {"id": "f1", "statement": long_text, "status": "verified"},
                {"id": "f2", "statement": long_text, "status": "verified"},
            ]

        def search_intuitions(self, *_args, **_kwargs):
            return []

        def search_tricks(self, *_args, **_kwargs):
            return []

    result = await ContextAugmentor(knowledge_store=Store()).augment("Solve this", "project")
    prompt = result.prompt
    memory_end = prompt.find("\n\nSolve this")
    assert memory_end != -1
    memory_portion = prompt[:memory_end]
    assert len(memory_portion) <= 5000
    assert "Solve this" in prompt


@pytest.mark.asyncio
async def test_plan_renders_with_verified_status_bracket():
    """Plans render with a verified status bracket and a stable traceable ID."""

    class FakePlanMemory:
        def retrieve(self, problem, *, k, min_score):
            return [FormalizationPlan(
                proof_strategy="Use induction on n.",
                verified_code="theorem prior : True := by trivial",
            )]

    result = await ContextAugmentor(plan_memory=FakePlanMemory()).augment("problem", None)
    prompt = result.prompt
    plan_id = hashlib.sha256("Use induction on n.".encode()).hexdigest()[:16]

    assert f"[{plan_id}, verified]" in prompt
    assert "Related successful plan:" in prompt
    assert "Use induction on n." in prompt
    assert f"- [{plan_id}, verified] Related proof strategy: Use induction on n." in prompt


@pytest.mark.asyncio
async def test_plan_without_verified_code_is_rendered_as_reviewed():
    class FakePlanMemory:
        def retrieve(self, problem, *, k, min_score):
            return [FormalizationPlan(
                proof_strategy="Try induction on n.",
                verification_status="verified",
            )]

    result = await ContextAugmentor(plan_memory=FakePlanMemory()).augment("problem", None)

    assert result.memories_used[0].status == "reviewed"
    assert ", reviewed] Related proof strategy: Try induction on n." in result.prompt


@pytest.mark.asyncio
async def test_global_budget_limits_total_items_and_chars():
    class Store:
        def search_facts(self, *_args, **_kwargs):
            return [
                {"id": f"f{i}", "statement": f"Fact {i} " + "x" * 200, "status": "verified"}
                for i in range(10)
            ]
        def search_intuitions(self, *_args, **_kwargs):
            return [
                {"id": f"i{i}", "title": f"Intuition {i}", "body": "y" * 200, "status": "approved"}
                for i in range(10)
            ]
        def search_tricks(self, *_args, **_kwargs):
            return []

    result = await ContextAugmentor(knowledge_store=Store()).augment("problem", "project")
    assert len(result.memories_used) <= 8
    assert result.memories_used[0].kind == "fact"
    memory_end = result.prompt.find("\n\nproblem")
    assert memory_end != -1
    memory_portion = result.prompt[:memory_end]
    assert len(memory_portion) <= 5000


@pytest.mark.asyncio
async def test_global_budget_skips_oversized_first_item():
    """A single memory larger than the char budget must not be injected,
    but smaller, lower-ranked items may still be selected."""

    class Store:
        def search_facts(self, *_args, **_kwargs):
            return [
                {"id": "huge-fact", "statement": "x" * 13_000, "status": "verified"},
                {"id": "small-fact", "statement": "small fact", "status": "approved"},
            ]

        def search_intuitions(self, *_args, **_kwargs):
            return []

        def search_tricks(self, *_args, **_kwargs):
            return []

    result = await ContextAugmentor(knowledge_store=Store()).augment("problem", "project")
    used_ids = {m.memory_id for m in result.memories_used}
    assert "huge-fact" not in used_ids
    assert "small-fact" in used_ids
    assert result.memories_used[0].kind == "fact"
    memory_end = result.prompt.find("\n\nproblem")
    assert memory_end != -1
    assert len(result.prompt[:memory_end]) <= 5000


@pytest.mark.asyncio
async def test_global_budget_returns_empty_when_only_oversized_items():
    """If no memory fits the budget, nothing is injected."""

    class Store:
        def search_facts(self, *_args, **_kwargs):
            return [
                {"id": "huge-fact", "statement": "x" * 13_000, "status": "verified"},
            ]

        def search_intuitions(self, *_args, **_kwargs):
            return []

        def search_tricks(self, *_args, **_kwargs):
            return []

    result = await ContextAugmentor(knowledge_store=Store()).augment("problem", "project")
    assert result.memories_used == []
    assert result.prompt == "problem"


@pytest.mark.asyncio
async def test_global_budget_preserves_memory_type_diversity():
    class Store:
        def search_facts(self, *_args, **_kwargs):
            return [
                {"id": f"f{i}", "statement": f"Fact {i}", "status": "verified"}
                for i in range(20)
            ]

        def search_intuitions(self, *_args, **_kwargs):
            return [{
                "id": "i1",
                "title": "Intuition",
                "body": "look for symmetry",
                "status": "reviewed",
            }]

        def search_tricks(self, *_args, **_kwargs):
            return [{
                "id": "t1",
                "title": "Strategy",
                "body": "normalize first",
                "status": "approved",
            }]

    plan = FormalizationPlan(
        proof_strategy="Reuse a successful decomposition.",
        verified_code="theorem prior : True := by trivial",
    )

    class Memory:
        def retrieve(self, *_args, **_kwargs):
            return [plan]

    result = await ContextAugmentor(Store(), Memory()).augment("problem", "project")

    assert {item.kind for item in result.memories_used} == {
        "fact",
        "intuition",
        "trick",
        "plan",
    }
    assert len(result.memories_used) == 8


def test_oversized_item_does_not_consume_section_header_budget(monkeypatch):
    plan = MemorySnippet(
        kind="plan",
        text="A compact plan",
        status="verified",
    )
    exact_budget = (
        len(context_augmentor._PREAMBLE)
        + 2
        + len(context_augmentor._section_header("plan"))
        + 3
        + context_augmentor._estimate_item_chars(plan)
    )
    monkeypatch.setattr(context_augmentor, "_MAX_MEMORY_CHARS", exact_budget)

    chosen = context_augmentor._apply_budget([
        MemorySnippet(kind="fact", text="x" * 6000, status="verified"),
        plan,
    ])

    assert chosen == [plan]


def test_profile_budget_counts_rendered_text_not_line_count(monkeypatch):
    monkeypatch.setattr(context_augmentor, "_USER_PROFILE_MAX_CHARS", 100)
    profile = MemorySnippet(
        kind="user_profile",
        text="x" * 80,
        status="active",
        confidence=1.0,
    )

    assert context_augmentor._apply_budget([profile]) == []
