import pytest

from math_agent.billing.models import LLMResponse
from math_agent.agent.memory_consolidation import (
    CONSOLIDATION_SYSTEM,
    ExtractedMemory,
    MemoryConsolidator,
    _REPAIR_SYSTEM,
    _apply_review_scores,
    _promote_reviewed_memories,
    parse_extracted_memory,
)
from math_agent.agent.react_state import (
    Action,
    ProjectContext,
    ReActSolution,
    ReActTrace,
    ReActTurn,
    ToolObservation,
)
from math_agent.web.project_store import ProjectStore


def test_parse_extracted_memory_valid_json():
    text = """{
        "facts": [{"statement": "sqrt(2) is irrational", "why": "Proven"}],
        "intuitions": [{"title": "Contradiction", "body": "Assume rational"}],
        "tricks": [{"title": "norm_num", "body": "Use for sqrt", "category": "tactic"}],
        "verified_code": "theorem..."
    }"""
    result = parse_extracted_memory(text)
    assert result.facts == [{"statement": "sqrt(2) is irrational", "why": "Proven"}]
    assert result.intuitions == [{"title": "Contradiction", "body": "Assume rational"}]
    assert result.tricks == [{"title": "norm_num", "body": "Use for sqrt", "category": "tactic"}]
    assert result.verified_code == "theorem..."


def test_parse_extracted_memory_preserves_metadata_fields():
    text = """{
        "facts": [{
            "statement": "If p is prime and p divides ab, then p divides a or p divides b.",
            "why": "Useful for divisibility arguments.",
            "formal_status": "lean_verified",
            "lean_name": "Nat.Prime.dvd_mul",
            "source_type": "lean_verified",
            "source_ref": "Nat.Prime.dvd_mul",
            "source_title": "Mathlib",
            "evidence": "theorem Nat.Prime.dvd_mul ...",
            "confidence": 1.0,
            "status": "verified",
            "domain": "number_theory",
            "tags": "prime,divisibility,gcd",
            "created_by": "lean_promotion",
            "review_note": "Accepted because Lean verified it."
        }],
        "intuitions": [{
            "title": "Use modular residues to rule out square forms",
            "body": "Check residues modulo a small base such as 4, 8, or 3.",
            "kind": "heuristic",
            "source_type": "agent_trace",
            "source_ref": "20260707-123000-abcd1234",
            "evidence": "The proof succeeded after checking modulo 4.",
            "confidence": 0.72,
            "status": "candidate",
            "domain": "number_theory",
            "tags": "modular_arithmetic,squares",
            "created_by": "memory_consolidation"
        }],
        "tricks": [{
            "title": "Infinite descent on coprime equation",
            "body": "Assume a minimal solution and derive a smaller one.",
            "category": "descent",
            "applicability": "Diophantine equations with a natural size measure.",
            "failure_mode": "Fails if the derived solution is not strictly smaller.",
            "source_type": "pdf",
            "source_ref": "fermat_notes.pdf#page=7",
            "source_title": "Notes on Infinite Descent",
            "evidence": "The proof constructs a smaller coprime pair.",
            "confidence": 0.81,
            "status": "candidate",
            "domain": "number_theory",
            "tags": "infinite_descent,diophantine,coprime",
            "created_by": "pdf_extraction"
        }]
    }"""

    result = parse_extracted_memory(text)

    assert result.facts[0]["formal_status"] == "lean_verified"
    assert result.facts[0]["lean_name"] == "Nat.Prime.dvd_mul"
    assert result.facts[0]["source_type"] == "lean_verified"
    assert result.facts[0]["status"] == "verified"
    assert result.facts[0]["domain"] == "number_theory"
    assert result.facts[0]["created_by"] == "lean_promotion"

    assert result.intuitions[0]["kind"] == "heuristic"
    assert result.intuitions[0]["source_type"] == "agent_trace"
    assert result.intuitions[0]["source_ref"] == "20260707-123000-abcd1234"
    assert result.intuitions[0]["status"] == "candidate"

    assert result.tricks[0]["category"] == "descent"
    assert result.tricks[0]["applicability"] == "Diophantine equations with a natural size measure."
    assert result.tricks[0]["failure_mode"] == "Fails if the derived solution is not strictly smaller."
    assert result.tricks[0]["source_type"] == "pdf"
    assert result.tricks[0]["source_title"] == "Notes on Infinite Descent"
    assert result.tricks[0]["tags"] == "infinite_descent,diophantine,coprime"


def test_parse_extracted_memory_preserves_knowledge_graph():
    text = """{
        "facts": [{"statement": "Theorem A", "why": "Reusable"}],
        "tricks": [{"title": "Second moment", "body": "Bound variance"}],
        "knowledge_graph": {
            "nodes": [{
                "id": "question:critical-threshold",
                "kind": "question",
                "title": "Critical threshold question"
            }],
            "edges": [{
                "source": "fact:0",
                "target": "technique:0",
                "kind": "uses_technique",
                "label": "proved by",
                "evidence": "The proof applies the second moment method.",
                "weight": 0.8
            }, {
                "source": "question:critical-threshold",
                "target": "fact:0",
                "kind": "answers_question"
            }]
        }
    }"""

    result = parse_extracted_memory(text)

    assert result.knowledge_graph["nodes"] == [
        {
            "id": "question:critical-threshold",
            "ref": "question:critical-threshold",
            "kind": "question",
            "title": "Critical threshold question",
            "status": "candidate",
            "created_by": "memory_consolidation",
        }
    ]
    assert result.knowledge_graph["edges"][0]["kind"] == "uses_technique"
    assert result.knowledge_graph["edges"][0]["source"] == "fact:0"
    assert result.knowledge_graph["edges"][1]["kind"] == "answers_question"


def test_review_score_promotes_evidenced_candidate_memory():
    extracted = ExtractedMemory(
        facts=[
            {
                "statement": "A reusable lemma",
                "evidence": "Reviewed proof passage",
                "status": "candidate",
            }
        ]
    )
    solution = ReActSolution(
        problem="P",
        turns=[],
        final_answer="A",
        verification_status="reviewed",
    )

    _promote_reviewed_memories(solution, extracted)
    _apply_review_scores(
        extracted,
        {"reviews": [{"ref": "fact:0", "score": 0.82, "review_note": "Reusable and evidenced."}]},
    )

    assert extracted.facts[0]["status"] == "reviewed"
    assert extracted.facts[0]["score"] == "0.82"


def test_reviewer_questions_candidates_when_solution_has_issues():
    extracted = ExtractedMemory(
        facts=[
            {
                "statement": "A suspicious reusable lemma",
                "evidence": "Some passage",
                "status": "candidate",
            }
        ]
    )
    solution = ReActSolution(
        problem="P",
        turns=[],
        final_answer="A",
        verification_status="reviewed",
        verification_issues=["gap in the proof"],
    )

    _promote_reviewed_memories(solution, extracted)

    assert extracted.facts[0]["status"] == "questioned"
    assert "reviewer reported issues" in extracted.facts[0]["review_note"]


def test_reviewed_answer_questions_unevidenced_memory():
    extracted = ExtractedMemory(
        intuitions=[
            {
                "title": "Too vague",
                "body": "Use clever algebra.",
                "status": "candidate",
            }
        ]
    )
    solution = ReActSolution(
        problem="P",
        turns=[],
        final_answer="A",
        verification_status="reviewed",
    )

    _promote_reviewed_memories(solution, extracted)

    assert extracted.intuitions[0]["status"] == "questioned"
    assert "did not provide item evidence" in extracted.intuitions[0]["review_note"]


@pytest.mark.parametrize(
    "plan_patch",
    [
        {"recommended_imports": "Mathlib.Data.Nat.Basic"},
        {"recommended_imports": ["Mathlib.Data.Nat.Basic", 7]},
        {"open_namespaces": {"Nat": True}},
        {"open_namespaces": ["Nat", None]},
        {"lemmas": {"statement": "not a list"}},
        {"lemmas": ["not a mapping"]},
        {"lemmas": [{"statement": 42}]},
        {"lemmas": [{"statement": "Valid", "depends_on": ["first", 2]}]},
    ],
)
def test_parse_extracted_memory_omits_malformed_plan_lists(plan_patch):
    plan = {
        "problem": "P",
        "goal_type": "theorem",
        "recommended_imports": [],
        "open_namespaces": [],
        "proof_strategy": "exact",
        "lemmas": [],
    }
    plan.update(plan_patch)

    result = parse_extracted_memory(__import__("json").dumps({"plan": plan}))

    assert result.plan is None


@pytest.mark.parametrize(
    "lemma_field",
    [
        "name",
        "statement",
        "formal_statement",
        "proof_hint",
        "proof_sketch",
        "recommended_theorem",
        "recommended_module",
    ],
)
def test_parse_extracted_memory_rejects_null_lemma_string_fields(lemma_field):
    plan = {
        "problem": "P",
        "goal_type": "theorem",
        "recommended_imports": [],
        "open_namespaces": [],
        "proof_strategy": "exact",
        "lemmas": [{"statement": "True", lemma_field: None}],
    }

    result = parse_extracted_memory(__import__("json").dumps({"plan": plan}))

    assert result.plan is None


def test_consolidation_prompts_include_metadata_schema():
    assert "source_type" in CONSOLIDATION_SYSTEM
    assert '"confidence"' not in CONSOLIDATION_SYSTEM
    assert "Do not output score or confidence" in CONSOLIDATION_SYSTEM
    assert "knowledge_graph" in CONSOLIDATION_SYSTEM
    assert "uses_technique" in CONSOLIDATION_SYSTEM
    assert "status" in CONSOLIDATION_SYSTEM
    assert "applicability" in CONSOLIDATION_SYSTEM
    assert "Preserve metadata fields" in _REPAIR_SYSTEM


class FakeLLM:
    def __init__(self, response: str):
        self.response = response

    async def complete(self, messages, system=None, temperature=None, response_format=None):
        return LLMResponse(
            text=self.response,
            prompt_tokens=0,
            completion_tokens=0,
            total_tokens=0,
        )

    async def stream(self, messages, system=None, temperature=None, response_format=None):
        yield LLMResponse(
            text=self.response,
            prompt_tokens=0,
            completion_tokens=0,
            total_tokens=0,
        )


class SequenceLLM:
    def __init__(self, responses: list[str]):
        self.responses = list(responses)

    async def complete(self, messages, system=None, temperature=None, response_format=None):
        text = self.responses.pop(0)
        return LLMResponse(
            text=text,
            prompt_tokens=0,
            completion_tokens=0,
            total_tokens=0,
        )


class FakeKnowledgeStore:
    def __init__(self):
        self.facts = []
        self.intuitions = []
        self.tricks = []

    def add_fact(self, project_id, statement, why="", source=""):
        self.facts.append({"project_id": project_id, "statement": statement, "why": why})

    def add_intuition(self, project_id, title, body, kind="", source=""):
        self.intuitions.append({"project_id": project_id, "title": title, "body": body})

    def add_trick(self, project_id, title, body, category="", source=""):
        self.tricks.append({"project_id": project_id, "title": title, "body": body})


class FakePlanMemory:
    def __init__(self):
        self.calls = []

    def record(self, problem, goal_type, plan, verified_code=""):
        self.calls.append({
            "problem": problem,
            "goal_type": goal_type,
            "plan": plan,
            "verified_code": verified_code,
        })


@pytest.mark.asyncio
async def test_consolidator_downgrades_non_lean_status_claims_to_candidate(tmp_path):
    llm = FakeLLM(
        """{
          "facts": [{
            "statement": "LLM promoted fact",
            "why": "because it said so",
            "evidence": "informal trace passage",
            "status": "approved"
          }],
          "intuitions": [{
            "title": "LLM reviewed intuition",
            "body": "try a substitution",
            "evidence": "informal trace passage",
            "status": "reviewed"
          }],
          "tricks": [{
            "title": "LLM questioned trick",
            "body": "maybe use descent",
            "status": "questioned"
          }]
        }"""
    )
    trace = ReActTrace(
        problem="P",
        project_context=ProjectContext(project_id="proj-1"),
    )
    store = ProjectStore(tmp_path)

    await MemoryConsolidator(llm=llm, knowledge_store=store).consolidate(
        trace,
        ReActSolution(
            problem="P",
            turns=[],
            final_answer="done",
            verification_status="best_effort",
        ),
    )

    assert store.list_facts("proj-1")[0]["status"] == "candidate"
    assert store.list_intuitions("proj-1")[0]["status"] == "candidate"
    assert store.list_tricks("proj-1")[0]["status"] == "candidate"


@pytest.mark.asyncio
async def test_consolidator_persists_reviewed_metadata_and_score(tmp_path):
    llm = SequenceLLM([
        """{
          "facts": [{
            "statement": "A reusable fact",
            "why": "It shortens similar arguments.",
            "source_type": "agent_trace",
            "source_ref": "session-1:step-2",
            "evidence": "The trace used this fact successfully.",
            "domain": "number_theory",
            "tags": "divisibility,prime",
            "status": "candidate"
          }]
        }""",
        """{
          "reviews": [{
            "ref": "fact:0",
            "score": 0.86,
            "review_note": "Specific, evidenced, and reusable."
          }]
        }""",
    ])
    trace = ReActTrace(
        problem="P",
        project_context=ProjectContext(project_id="proj-1"),
    )
    store = ProjectStore(tmp_path)

    await MemoryConsolidator(llm=llm, knowledge_store=store).consolidate(
        trace,
        ReActSolution(
            problem="P",
            turns=[],
            final_answer="done",
            verification_status="reviewed",
        ),
    )

    fact = store.list_facts("proj-1")[0]
    assert fact["statement"] == "A reusable fact"
    assert fact["why"] == "It shortens similar arguments."
    assert fact["source_type"] == "agent_trace"
    assert fact["source_ref"] == "session-1:step-2"
    assert fact["evidence"] == "The trace used this fact successfully."
    assert fact["domain"] == "number_theory"
    assert fact["tags"] == "divisibility,prime"
    assert fact["score"] == "0.86"
    assert fact["status"] == "reviewed"
    assert fact["review_note"] == "Specific, evidenced, and reusable."


@pytest.mark.asyncio
async def test_consolidator_writes_to_knowledge_store():
    llm = FakeLLM('{"facts": [{"statement": "S", "why": "W"}]}')
    store = FakeKnowledgeStore()
    consolidator = MemoryConsolidator(llm=llm, knowledge_store=store)
    trace = ReActTrace(
        problem="P",
        project_context=ProjectContext(project_id="proj-1"),
    )
    solution = ReActSolution(problem="P", turns=[], final_answer="A")
    await consolidator.consolidate(trace, solution)
    assert len(store.facts) == 1
    assert store.facts[0]["project_id"] == "proj-1"
    assert store.facts[0]["statement"] == "S"

@pytest.mark.asyncio
async def test_consolidator_preserves_metadata_in_project_store(tmp_path):
    verified_code = "theorem prime_dvd_mul : True := by trivial"
    llm = FakeLLM(
        """{
            "facts": [{
                "statement": "If p is prime and p divides ab, then p divides a or p divides b.",
                "why": "Useful for divisibility arguments.",
                "formal_status": "lean_verified",
                "lean_name": "Nat.Prime.dvd_mul",
                "source_type": "lean_verified",
                "source_ref": "Nat.Prime.dvd_mul",
                "evidence": "theorem prime_dvd_mul : True := by trivial",
                "confidence": 1.0,
                "status": "verified",
                "domain": "number_theory",
                "tags": "prime,divisibility",
                "created_by": "lean_promotion"
            }],
            "intuitions": [{
                "title": "Use modular residues",
                "body": "Check residues modulo a small base.",
                "kind": "heuristic",
                "confidence": 0.72
            }],
            "tricks": [{
                "title": "Infinite descent",
                "body": "Derive a smaller solution.",
                "category": "descent",
                "applicability": "Diophantine equations.",
                "failure_mode": "No smaller solution."
            }],
            "verified_code": "theorem prime_dvd_mul : True := by trivial"
        }"""
    )
    store = ProjectStore(tmp_path)
    consolidator = MemoryConsolidator(llm=llm, knowledge_store=store)
    trace = ReActTrace(
        problem="P",
        project_context=ProjectContext(project_id="proj-1"),
        turns=[
            ReActTurn(
                thought="verify",
                action=Action(name="lean_check", args={"code": verified_code}),
                observation=ToolObservation(
                    success=True, output="PASSED", lean_code=verified_code
                ),
                step_num=1,
            )
        ],
    )
    solution = ReActSolution(
        problem="P",
        turns=[],
        final_answer="A",
        lean_proofs=[verified_code],
        verification_status="verified",
    )

    await consolidator.consolidate(trace, solution)

    fact = store.list_facts("proj-1")[0]
    intuition = store.list_intuitions("proj-1")[0]
    trick = store.list_tricks("proj-1")[0]
    assert fact["formal_status"] == "lean_verified"
    assert fact["lean_name"] == "Nat.Prime.dvd_mul"
    assert fact["source_type"] == "lean_verified"
    assert fact["confidence"] == ""
    assert fact["status"] == "verified"
    assert fact["domain"] == "number_theory"
    assert fact["tags"] == "prime,divisibility"
    assert intuition["kind"] == "heuristic"
    assert intuition["confidence"] == ""
    assert intuition["source_type"] == "agent_trace"
    assert intuition["status"] == "candidate"
    assert trick["applicability"] == "Diophantine equations."
    assert trick["failure_mode"] == "No smaller solution."
    assert trick["created_by"] == "memory_consolidation"


@pytest.mark.asyncio
async def test_consolidator_persists_knowledge_graph_edges_with_real_ids(tmp_path):
    llm = FakeLLM(
        """{
            "facts": [{
                "statement": "A theorem",
                "why": "It is reusable.",
                "evidence": "The proof used a named method."
            }],
            "tricks": [{
                "title": "Second moment method",
                "body": "Control the first two moments.",
                "evidence": "The proof used a named method."
            }],
            "knowledge_graph": {
                "edges": [{
                    "source": "fact:0",
                    "target": "technique:0",
                    "kind": "uses_technique",
                    "label": "uses",
                    "evidence": "The proof used a named method.",
                    "weight": 0.7
                }]
            }
        }"""
    )
    store = ProjectStore(tmp_path)
    trace = ReActTrace(
        problem="P",
        project_context=ProjectContext(project_id="proj-1"),
    )

    await MemoryConsolidator(llm=llm, knowledge_store=store).consolidate(
        trace,
        ReActSolution(
            problem="P",
            turns=[],
            final_answer="done",
            verification_status="reviewed",
        ),
    )

    fact = store.list_facts("proj-1")[0]
    technique = store.list_tricks("proj-1")[0]
    edges = store.list_knowledge_graph_edges("proj-1")
    assert edges == [
        {
            "id": f"{fact['id']}:uses_technique:{technique['id']}",
            "source": fact["id"],
            "target": technique["id"],
            "kind": "uses_technique",
            "label": "uses",
            "evidence": "The proof used a named method.",
            "weight": 0.7,
            "status": "candidate",
            "score": "",
            "review_note": "",
            "created_at": edges[0]["created_at"],
            "updated_at": edges[0]["updated_at"],
            "metadata": {
                "origin": "memory_consolidation",
                "source_ref": "fact:0",
                "target_ref": "technique:0",
            },
        }
    ]


@pytest.mark.asyncio
async def test_consolidator_persists_reviewed_graph_and_syncs_legacy_views(tmp_path):
    llm = SequenceLLM([
        """{
            "knowledge_graph": {
                "nodes": [{
                    "ref": "n0",
                    "kind": "theorem",
                    "title": "Second moment existence theorem",
                    "statement": "A positive second moment ratio implies existence.",
                    "body": "Reusable probabilistic existence criterion.",
                    "evidence": "The proof bounded the first two moments."
                }, {
                    "ref": "n1",
                    "kind": "technique",
                    "title": "Second moment method",
                    "body": "Control first and second moments.",
                    "evidence": "The proof bounded the first two moments.",
                    "metadata": {
                        "category": "probabilistic",
                        "applicability": "Existence problems with a random count.",
                        "failure_mode": "Fails when variance is too large."
                    }
                }],
                "edges": [{
                    "ref": "e0",
                    "source": "n0",
                    "target": "n1",
                    "kind": "uses_technique",
                    "evidence": "The theorem is proved by bounding the first two moments."
                }]
            }
        }""",
        """{
            "reviews": [
                {"ref": "node:n0", "score": 0.86, "review_note": "Accurate and reusable."},
                {"ref": "node:n1", "score": 0.9, "review_note": "Clear technique."},
                {"ref": "edge:e0", "score": 0.68, "review_note": "Relation is supported but broad."}
            ]
        }""",
    ])
    store = ProjectStore(tmp_path)
    trace = ReActTrace(
        problem="P",
        project_context=ProjectContext(project_id="proj-1"),
    )

    await MemoryConsolidator(llm=llm, knowledge_store=store).consolidate(
        trace,
        ReActSolution(
            problem="P",
            turns=[],
            final_answer="done",
            verification_status="reviewed",
        ),
    )

    nodes = {node["ref"]: node for node in store.list_knowledge_graph_nodes("proj-1")}
    assert nodes["n0"]["status"] == "reviewed"
    assert nodes["n0"]["score"] == "0.86"
    assert nodes["n1"]["status"] == "reviewed"

    edges = store.list_knowledge_graph_edges("proj-1")
    assert edges[0]["kind"] == "uses_technique"
    assert edges[0]["status"] == "questioned"
    assert edges[0]["score"] == "0.68"
    assert edges[0]["source"] == nodes["n0"]["id"]
    assert edges[0]["target"] == nodes["n1"]["id"]

    fact = store.list_facts("proj-1")[0]
    trick = store.list_tricks("proj-1")[0]
    assert fact["statement"] == "A positive second moment ratio implies existence."
    assert fact["status"] == "reviewed"
    assert trick["title"] == "Second moment method"
    assert trick["category"] == "probabilistic"


@pytest.mark.asyncio
async def test_consolidator_jsonl_persists_items_independently(tmp_path):
    llm = FakeLLM(
        """{
            "facts": [
                {"statement": "good fact", "why": "keep", "source_type": "agent_trace"},
                {"statement": "bad fact", "why": "fails", "source_type": "agent_trace"}
            ],
            "intuitions": [{"title": "good intuition", "body": "keep"}],
            "tricks": [{"title": "good trick", "body": "keep", "category": "descent"}]
        }"""
    )
    store = ProjectStore(tmp_path)
    original_add_many = store.add_many

    def flaky_add_many(project_id, facts, intuitions, tricks):
        if facts and facts[0].get("statement") == "bad fact":
            raise RuntimeError("simulated item failure")
        return original_add_many(project_id, facts, intuitions, tricks)

    store.add_many = flaky_add_many
    consolidator = MemoryConsolidator(llm=llm, knowledge_store=store)
    trace = ReActTrace(
        problem="P",
        project_context=ProjectContext(project_id="proj-1"),
    )
    solution = ReActSolution(problem="P", turns=[], final_answer="A")

    await consolidator.consolidate(trace, solution)

    facts = store.list_facts("proj-1")
    intuitions = store.list_intuitions("proj-1")
    tricks = store.list_tricks("proj-1")
    assert [fact["statement"] for fact in facts] == ["good fact"]
    assert intuitions[0]["title"] == "good intuition"
    assert tricks[0]["title"] == "good trick"


@pytest.mark.asyncio
async def test_consolidator_repairs_invalid_json():
    class TwoShotLLM:
        def __init__(self, responses):
            self.responses = list(responses)
            self.index = 0

        async def complete(self, messages, system=None, temperature=None, response_format=None):
            response = self.responses[self.index]
            self.index += 1
            return LLMResponse(
                text=response,
                prompt_tokens=0,
                completion_tokens=0,
                total_tokens=0,
            )

        async def stream(self, messages, system=None, temperature=None, response_format=None):
            yield LLMResponse(
                text=self.responses[min(self.index, len(self.responses) - 1)],
                prompt_tokens=0,
                completion_tokens=0,
                total_tokens=0,
            )

    store = FakeKnowledgeStore()
    consolidator = MemoryConsolidator(llm=TwoShotLLM(["not json", '{"facts": [{"statement": "repaired", "why": "fixed"}]}']), knowledge_store=store)
    trace = ReActTrace(
        problem="P",
        project_context=ProjectContext(project_id="proj-1"),
    )
    solution = ReActSolution(problem="P", turns=[], final_answer="A")
    await consolidator.consolidate(trace, solution)
    assert len(store.facts) == 1
    assert store.facts[0]["statement"] == "repaired"


@pytest.mark.asyncio
async def test_consolidator_rejects_verified_code_without_successful_formal_observation():
    plan_json = (
        '{"plan": {'
        '"problem": "P",'
        '"restatement": "prove P",'
        '"goal_type": "theorem",'
        '"is_standard_result": false,'
        '"recommended_theorem": null,'
        '"recommended_module": null,'
        '"recommended_imports": [],'
        '"open_namespaces": [],'
        '"proof_strategy": "contradiction",'
        '"notes": "",'
        '"lemmas": [],'
        '"verified_code": ""'
        '}, "verified_code": "theorem P := by simp"}'
    )
    llm = FakeLLM(plan_json)
    plan_memory = FakePlanMemory()
    consolidator = MemoryConsolidator(llm=llm, plan_memory=plan_memory)
    trace = ReActTrace(problem="P")
    solution = ReActSolution(problem="P", turns=[], final_answer="done")
    extracted = await consolidator.consolidate(trace, solution)

    assert extracted.verified_code == ""
    assert extracted.plan is not None
    assert extracted.plan.verified_code == ""
    assert plan_memory.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("action_name", ["formalize", "lean_check"])
async def test_consolidator_records_exact_successful_formal_artifact(action_name):
    code = "theorem P : True := by trivial"
    plan_json = (
        '{"plan": {'
        '"problem": "P", "restatement": "prove P", "goal_type": "theorem", '
        '"proof_strategy": "exact", "lemmas": [{"statement": "True"}]'
        f'}}, "verified_code": {__import__("json").dumps(code)}}}'
    )
    plan_memory = FakePlanMemory()
    consolidator = MemoryConsolidator(llm=FakeLLM(plan_json), plan_memory=plan_memory)
    trace = ReActTrace(
        problem="P",
        turns=[
            ReActTurn(
                thought="verify",
                action=Action(name=action_name, args={}),
                observation=ToolObservation(success=True, output="PASSED", lean_code=code),
                step_num=1,
            )
        ],
    )

    extracted = await consolidator.consolidate(
        trace,
        ReActSolution(
            problem="P",
            turns=trace.turns,
            final_answer="done",
            lean_proofs=[code],
            verification_status="verified",
        ),
    )

    assert extracted.verified_code == code
    assert plan_memory.calls[0]["verified_code"] == code


@pytest.mark.asyncio
async def test_consolidator_downgrades_spoofed_verified_metadata(tmp_path):
    claimed_code = "theorem claimed : True := by trivial"
    observed_code = "theorem observed : True := by trivial"
    llm = FakeLLM(
        """{
          "facts": [{
            "statement": "Spoofed fact",
            "formal_status": "lean_verified",
            "source_type": "lean_verified",
            "status": "verified"
          }],
          "plan": {"problem": "P", "goal_type": "theorem", "proof_strategy": "exact", "lemmas": [{"statement": "True"}]},
          "verified_code": "theorem claimed : True := by trivial"
        }"""
    )
    store = ProjectStore(tmp_path)
    plan_memory = FakePlanMemory()
    trace = ReActTrace(
        problem="P",
        project_context=ProjectContext(project_id="proj-1"),
        turns=[
            ReActTurn(
                thought="verify a different artifact",
                action=Action(name="lean_check", args={"code": observed_code}),
                observation=ToolObservation(
                    success=True, output="PASSED", lean_code=observed_code
                ),
                step_num=1,
            )
        ],
    )
    consolidator = MemoryConsolidator(
        llm=llm, knowledge_store=store, plan_memory=plan_memory
    )

    extracted = await consolidator.consolidate(
        trace,
        ReActSolution(
            problem="P",
            turns=trace.turns,
            final_answer="done",
            lean_proofs=[observed_code],
            verification_status="verified",
        ),
    )

    fact = store.list_facts("proj-1")[0]
    assert claimed_code != observed_code
    assert extracted.verified_code == ""
    assert fact["status"] == "candidate"
    assert fact["formal_status"] == "informal"
    assert fact["source_type"] == "agent_trace"
    assert plan_memory.calls == []


@pytest.mark.asyncio
async def test_consolidator_verifies_each_item_from_its_exact_evidence(tmp_path):
    code = "theorem observed : True := by trivial"
    llm = FakeLLM(
        """{
          "facts": [
            {
              "statement": "Actually supported",
              "evidence": "theorem observed : True := by trivial",
              "formal_status": "lean_verified",
              "source_type": "lean_verified",
              "status": "verified"
            },
            {
              "statement": "Borrowed global trust",
              "evidence": "different alleged proof",
              "formal_status": "lean_verified",
              "source_type": "lean_verified",
              "status": "verified"
            }
          ],
          "verified_code": "theorem observed : True := by trivial"
        }"""
    )
    trace = ReActTrace(
        problem="P",
        project_context=ProjectContext(project_id="proj-1"),
        turns=[
            ReActTurn(
                thought="verify",
                action=Action(name="lean_check", args={"code": code}),
                observation=ToolObservation(success=True, output="PASSED", lean_code=code),
                step_num=1,
            )
        ],
    )
    store = ProjectStore(tmp_path)

    await MemoryConsolidator(llm=llm, knowledge_store=store).consolidate(
        trace,
        ReActSolution(
            problem="P",
            turns=trace.turns,
            final_answer="done",
            lean_proofs=[code],
            verification_status="verified",
        ),
    )

    facts = {row["statement"]: row for row in store.list_facts("proj-1")}
    supported = facts["Actually supported"]
    unsupported = facts["Borrowed global trust"]
    assert supported["status"] == "verified"
    assert supported["formal_status"] == "lean_verified"
    assert supported["source_type"] == "lean_verified"
    assert unsupported["status"] == "candidate"
    assert unsupported["formal_status"] == "informal"
    assert unsupported["source_type"] == "agent_trace"
