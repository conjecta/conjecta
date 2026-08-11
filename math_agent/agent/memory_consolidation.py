from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from math_agent.agent.planner import FormalizationPlan
from math_agent.agent.react_state import ReActSolution, ReActTrace
from math_agent.llm.base import LLMBackend, Message

if TYPE_CHECKING:
    from math_agent.agent.plan_memory import PlanMemory
    from math_agent.knowledge.supabase import KnowledgeStore

log = logging.getLogger("math_agent.agent.memory_consolidation")


@dataclass
class ExtractedMemory:
    facts: list[dict[str, str]] = field(default_factory=list)
    intuitions: list[dict[str, str]] = field(default_factory=list)
    tricks: list[dict[str, str]] = field(default_factory=list)
    knowledge_graph: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    plan: FormalizationPlan | None = None
    verified_code: str = ""


class ConsolidationParseError(Exception):
    pass


def parse_extracted_memory(text: str) -> ExtractedMemory:
    data = _extract_json(text)
    if data is None:
        raise ConsolidationParseError(f"Could not parse JSON: {text[:200]}")
    if not isinstance(data, dict):
        raise ConsolidationParseError("Expected JSON object")

    return ExtractedMemory(
        facts=_normalize_items(data.get("facts", []), _FACT_FIELDS),
        intuitions=_normalize_items(data.get("intuitions", []), _INTUITION_FIELDS),
        tricks=_normalize_items(data.get("tricks", []), _TRICK_FIELDS),
        knowledge_graph=_parse_knowledge_graph(data),
        plan=_parse_plan(data.get("plan")),
        verified_code=_as_str(data.get("verified_code", "")),
    )


def _extract_json(text: str) -> Any | None:
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


_COMMON_MEMORY_FIELDS = [
    "source_type",
    "source_ref",
    "source_title",
    "evidence",
    "score",
    "status",
    "domain",
    "tags",
    "created_by",
    "review_note",
]

_FACT_FIELDS = [
    "statement",
    "why",
    "formal_status",
    "lean_name",
    *_COMMON_MEMORY_FIELDS,
]

_INTUITION_FIELDS = [
    "title",
    "body",
    "kind",
    *_COMMON_MEMORY_FIELDS,
]

_TRICK_FIELDS = [
    "title",
    "body",
    "category",
    "applicability",
    "failure_mode",
    *_COMMON_MEMORY_FIELDS,
]

_GRAPH_NODE_KINDS = {
    "definition",
    "theorem",
    "lemma",
    "proposition",
    "corollary",
    "exercise",
    "technique",
    "intuition",
    "paper",
    "question",
    "viewpoint",
    "topic",
    "source",
}

_GRAPH_EDGE_KINDS = {
    "depends_on",
    "uses_technique",
    "has_intuition",
    "generalizes",
    "special_case_of",
    "equivalent_to",
    "analogy_with",
    "formalizes_as",
    "connects_to",
    "introduces",
    "refines",
    "answers_question",
    "arises_from",
}

_GRAPH_NODE_FIELDS = [
    "ref",
    "id",
    "kind",
    "title",
    "label",
    "statement",
    "body",
    "evidence",
    "source_type",
    "source_ref",
    "source_title",
    "status",
    "score",
    "formal_status",
    "lean_name",
    "domain",
    "tags",
    "created_by",
    "review_note",
]

_GRAPH_EDGE_FIELDS = [
    "source",
    "target",
    "kind",
    "label",
    "evidence",
    "weight",
    "status",
    "score",
    "review_note",
]


def _normalize_items(items: Any, fields: list[str]) -> list[dict[str, str]]:
    if not isinstance(items, list):
        return []
    result: list[dict[str, str]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        normalized = {
            f: value
            for f in fields
            if (value := _as_str(item.get(f, "")).strip())
        }
        if any(normalized.values()):
            result.append(normalized)
    return result


def _parse_knowledge_graph(data: Any) -> dict[str, list[dict[str, Any]]]:
    if not isinstance(data, Mapping):
        return {"nodes": [], "edges": []}
    value = data.get("knowledge_graph")
    if not isinstance(value, Mapping) and ("nodes" in data or "edges" in data):
        value = data
    if not isinstance(value, Mapping):
        return {"nodes": [], "edges": []}
    nodes = _normalize_graph_nodes(value.get("nodes", []))
    edges = _normalize_graph_edges(value.get("edges", []))
    return {"nodes": nodes, "edges": edges}


def _normalize_graph_nodes(items: Any) -> list[dict[str, Any]]:
    if not isinstance(items, list):
        return []
    result: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, Mapping):
            continue
        node = {
            f: value
            for f in _GRAPH_NODE_FIELDS
            if (value := _as_str(item.get(f, "")).strip())
        }
        metadata = item.get("metadata")
        if isinstance(metadata, dict):
            node["metadata"] = {
                str(k): v
                for k, v in metadata.items()
                if isinstance(k, str) and v is not None
            }
        ref = node.get("ref") or node.get("id")
        if not ref:
            continue
        node["ref"] = ref
        kind = node.get("kind", "").lower()
        if kind and kind not in _GRAPH_NODE_KINDS:
            kind = "topic"
        if kind:
            node["kind"] = kind
        if not node.get("status"):
            node["status"] = "candidate"
        if not node.get("created_by"):
            node["created_by"] = "memory_consolidation"
        if node.get("title") or node.get("label") or node.get("statement"):
            result.append(node)
    return result


def _normalize_graph_edges(items: Any) -> list[dict[str, Any]]:
    if not isinstance(items, list):
        return []
    result: list[dict[str, Any]] = []
    for idx, item in enumerate(items):
        if not isinstance(item, Mapping):
            continue
        edge = {
            f: value
            for f in _GRAPH_EDGE_FIELDS
            if (value := _as_str(item.get(f, "")).strip())
        }
        ref = _as_str(item.get("ref", "")).strip() or f"e{idx}"
        edge["ref"] = ref
        metadata = item.get("metadata")
        if isinstance(metadata, dict):
            edge["metadata"] = {
                str(k): v
                for k, v in metadata.items()
                if isinstance(k, str) and v is not None
            }
        source = edge.get("source", "")
        target = edge.get("target", "")
        if not source or not target or source == target:
            continue
        kind = edge.get("kind", "").lower()
        edge["kind"] = kind if kind in _GRAPH_EDGE_KINDS else "connects_to"
        if not edge.get("status"):
            edge["status"] = "candidate"
        result.append(edge)
    return result


def _parse_plan(plan_data: Any) -> FormalizationPlan | None:
    if not isinstance(plan_data, Mapping):
        return None
    recommended_imports = plan_data.get("recommended_imports", [])
    open_namespaces = plan_data.get("open_namespaces", [])
    lemmas = plan_data.get("lemmas", [])
    if not _is_string_list(recommended_imports):
        return None
    if not _is_string_list(open_namespaces):
        return None
    if not _valid_lemmas(lemmas):
        return None
    try:
        return FormalizationPlan(
            problem=_as_str(plan_data.get("problem", "")),
            restatement=_as_str(plan_data.get("restatement", "")),
            goal_type=_as_str(plan_data.get("goal_type", "")),
            is_standard_result=bool(plan_data.get("is_standard_result", False)),
            recommended_theorem=_optional_str(plan_data.get("recommended_theorem")),
            recommended_module=_optional_str(plan_data.get("recommended_module")),
            recommended_imports=list(recommended_imports),
            open_namespaces=list(open_namespaces),
            proof_strategy=_as_str(plan_data.get("proof_strategy", "")),
            notes=_as_str(plan_data.get("notes", "")),
            lemmas=[dict(lemma) for lemma in lemmas],
            verified_code=_as_str(plan_data.get("verified_code", "")),
        )
    except Exception as exc:
        log.warning("Failed to parse FormalizationPlan from consolidation: %s", exc)
        return None


def _is_string_list(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def _valid_lemmas(value: Any) -> bool:
    if not isinstance(value, list):
        return False
    for lemma in value:
        if not isinstance(lemma, Mapping):
            return False
        for key, field_value in lemma.items():
            if not isinstance(key, str):
                return False
            if key == "depends_on":
                if not _is_string_list(field_value):
                    return False
            elif not isinstance(field_value, str):
                return False
    return True


def _as_str(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    s = str(value).strip()
    return s or None


CONSOLIDATION_SYSTEM = """You are a memory consolidation assistant for a mathematical reasoning agent.

Given the original problem, the agent's final answer, and the full reasoning trace, extract reusable knowledge.

Output ONLY valid JSON matching this structure:
{
  "knowledge_graph": {
    "nodes": [{
      "ref": "local reference, e.g. n0",
      "kind": "definition|theorem|lemma|proposition|corollary|exercise|technique|intuition|paper|question|viewpoint|topic|source",
      "title": "short node title",
      "statement": "optional formal or informal statement",
      "body": "optional explanation",
      "evidence": "short supporting excerpt, trace observation, or Lean snippet",
      "source_type": "user_prompt|agent_trace|pdf|web|lean_verified|manual",
      "source_ref": "stable source reference",
      "source_title": "optional source title",
      "status": "candidate|verified",
      "domain": "coarse math domain",
      "tags": "comma-separated topic tags",
      "metadata": {
        "category": "optional technique category",
        "applicability": "optional technique applicability",
        "failure_mode": "optional technique failure mode"
      }
    }],
    "edges": [{
      "ref": "local reference, e.g. e0",
      "source": "source node ref, e.g. n0",
      "target": "target node ref, e.g. n1",
      "kind": "depends_on|uses_technique|has_intuition|generalizes|special_case_of|equivalent_to|analogy_with|formalizes_as|connects_to|introduces|refines|answers_question|arises_from",
      "label": "short display label",
      "evidence": "short supporting excerpt or trace observation",
      "status": "candidate",
      "metadata": {}
    }]
  },
  "facts": [],
  "intuitions": [],
  "tricks": [],
  "plan": {
    "restatement": "...",
    "goal_type": "...",
    "is_standard_result": true|false,
    "recommended_theorem": "...",
    "recommended_module": "...",
    "recommended_imports": [...],
    "open_namespaces": [...],
    "proof_strategy": "...",
    "notes": "...",
    "lemmas": [...]
  },
  "verified_code": "..."
}

Rules:
- Only extract genuinely reusable, non-obvious insights.
- If the proof failed, extract what went wrong and how to avoid it.
- Include "plan" and "verified_code" only when a Lean proof succeeded.
- Treat knowledge_graph as the primary memory model. Leave facts, intuitions,
  and tricks empty unless needed for backward compatibility.
- Use theorem, definition, lemma, proposition, corollary, or exercise for
  mathematical statement nodes; use technique for proof techniques; use
  intuition for explanatory or strategic ideas.
- Use local node refs like n0, n1 and edge refs like e0, e1. Edges must point to
  node refs from the same output.
- Only create graph edges supported by the trace. Omit speculative relationships.
- Default all newly consolidated agent-trace memories to source_type="agent_trace",
  status="candidate",
  created_by="memory_consolidation".
- Use status="verified" only when the item evidence is exactly bound to the
  accepted Lean-verified artifact for this solve.
- Do not output score or confidence; those are assigned later by the knowledge
  evaluator.
- Do not output status="reviewed", "questioned", or "approved"; those states
  are assigned by the reviewer/evaluator stages after consolidation.
- Keep each item concise.
- If no useful memory can be extracted, return empty arrays and omit plan/verified_code.
"""

_REPAIR_SYSTEM = (
    "You output only valid JSON matching this schema:\n"
    '{"facts":[{"statement":"...","why":"...","formal_status":"informal|formalized|lean_verified",'
    '"lean_name":"...","source_type":"agent_trace","source_ref":"...","source_title":"...",'
    '"evidence":"...","status":"candidate|verified",'
    '"domain":"...","tags":"tag1,tag2","created_by":"memory_consolidation","review_note":"..."}],'
    '"intuitions":[{"title":"...","body":"...","kind":"heuristic|strategy|motivation|analogy|warning|other",'
    '"source_type":"agent_trace","source_ref":"...","source_title":"...","evidence":"...",'
    '"status":"candidate|verified","domain":"...",'
    '"tags":"tag1,tag2","created_by":"memory_consolidation","review_note":"..."}],'
    '"tricks":[{"title":"...","body":"...","category":"...","applicability":"...",'
    '"failure_mode":"...","source_type":"agent_trace","source_ref":"...","source_title":"...",'
    '"evidence":"...","status":"candidate|verified",'
    '"domain":"...","tags":"tag1,tag2","created_by":"memory_consolidation","review_note":"..."}],'
    '"knowledge_graph":{"nodes":[{"ref":"n0","kind":"theorem|definition|lemma|technique|intuition|paper|question|topic",'
    '"title":"...","statement":"...","body":"...","evidence":"...","source_type":"agent_trace",'
    '"status":"candidate|verified","domain":"...","tags":"...","metadata":{}}],'
    '"edges":[{"ref":"e0","source":"n0","target":"n1","kind":"uses_technique",'
    '"label":"...","evidence":"...","status":"candidate","metadata":{}}]},'
    '"plan":{...},"verified_code":"..."}\n'
    "Preserve metadata fields from the previous response whenever possible. "
    "Omit plan and verified_code when no verified Lean proof is available."
)

REVIEW_SYSTEM = """You are the memory reviewer for a mathematical reasoning agent.

You receive newly consolidated candidate graph nodes and graph edges from one solve.
Score each item for long-term reuse quality.

Return ONLY valid JSON:
{
  "reviews": [
    {
      "ref": "node:n0",
      "score": 0.0,
      "review_note": "short reason"
    },
    {
      "ref": "edge:e0",
      "score": 0.0,
      "review_note": "short reason"
    }
  ]
}

Node scoring rubric:
- 0.85-1.00: accurate, specific, evidenced, and clearly reusable.
- 0.65-0.84: likely useful but somewhat narrow or lightly evidenced.
- 0.40-0.64: plausible but weak, vague, risky, or missing applicability.
- 0.00-0.39: wrong, duplicate, too vague, unsafe, or not reusable.

Edge scoring rubric:
- 0.85-1.00: relation type and direction are correct, evidence directly supports
  the relationship, and the edge is useful for retrieval or future reasoning.
- 0.65-0.84: relation is likely correct and useful, but evidence or direction is
  somewhat implicit.
- 0.40-0.64: relation is plausible but underspecified, weakly evidenced, too
  generic, or the edge kind may be imprecise.
- 0.00-0.39: unsupported, wrong direction, wrong relation type, connects the
  wrong nodes, duplicates trivial proximity, or would mislead downstream search.

Do not mark items verified or approved. The system will map score to status.
"""

_REVIEW_REPAIR_SYSTEM = (
    "You output only valid JSON with this shape: "
    '{"reviews":[{"ref":"node:n0","score":0.0,"review_note":"..."},'
    '{"ref":"edge:e0","score":0.0,"review_note":"..."}]}. '
    "Return empty reviews if repair is impossible."
)

_REVIEWED_SCORE_THRESHOLD = 0.75
_QUESTIONED_SCORE_THRESHOLD = 0.4


class MemoryConsolidator:
    def __init__(
        self,
        llm: LLMBackend,
        knowledge_store: KnowledgeStore | None = None,
        plan_memory: PlanMemory | None = None,
    ) -> None:
        self.llm = llm
        self.knowledge_store = knowledge_store
        self.plan_memory = plan_memory

    async def consolidate(
        self,
        trace: ReActTrace,
        solution: ReActSolution,
    ) -> ExtractedMemory:
        prompt = self._build_prompt(trace, solution)
        response = await self.llm.complete(
            [Message(role="user", content=prompt)],
            system=CONSOLIDATION_SYSTEM,
            temperature=0.2,
        )
        extracted = await self._parse_with_repair(response.text)
        _normalize_consolidated_statuses(solution, extracted)
        await self._review_candidates(solution, extracted)
        self._persist(trace, extracted)
        return extracted

    def _build_prompt(self, trace: ReActTrace, solution: ReActSolution) -> str:
        lines = [
            f"Problem: {trace.problem}",
            f"Current goal: {trace.current_goal}",
            f"Final answer: {solution.final_answer}",
            "\nReasoning trace:",
        ]
        for turn in trace.turns:
            lines.append(f"\nStep {turn.step_num}")
            lines.append(f"Thought: {turn.thought}")
            lines.append(f"Action: {turn.action.name}({turn.action.args})")
            lines.append(f"Observation: {turn.observation.output[:800]}")
            for review in turn.reviews:
                lines.append(
                    f"Review ({review.reviewer}): {review.verdict} - issues: {review.issues}; "
                    f"suggestions: {review.suggestions}"
                )
        return "\n".join(lines)

    async def _review_candidates(
        self,
        solution: ReActSolution,
        extracted: ExtractedMemory,
    ) -> None:
        _apply_reviewer_preconditions(solution, extracted)
        candidates = _candidate_review_items(extracted)
        if not candidates:
            return
        prompt = _build_review_prompt(solution, candidates)
        try:
            response = await self.llm.complete(
                [Message(role="user", content=prompt)],
                system=REVIEW_SYSTEM,
                temperature=0.1,
            )
            data = _extract_json(response.text)
        except Exception as exc:
            log.warning("Memory review failed: %s", exc)
            data = None
        if not isinstance(data, dict):
            data = await self._repair_review_response(response.text if "response" in locals() else "")
        _apply_review_scores(extracted, data)

    async def _repair_review_response(self, raw: str) -> dict[str, Any]:
        repair_prompt = (
            "The previous response was not valid JSON. Output ONLY valid JSON "
            "matching the requested memory review schema.\n\n"
            f"Previous response:\n{raw}\n\n"
            "Now output valid JSON:"
        )
        try:
            response = await self.llm.complete(
                [Message(role="user", content=repair_prompt)],
                system=_REVIEW_REPAIR_SYSTEM,
                temperature=0.0,
            )
            data = _extract_json(response.text)
            return data if isinstance(data, dict) else {"reviews": []}
        except Exception:
            log.warning("Memory review JSON repair failed")
            return {"reviews": []}

    async def _parse_with_repair(self, raw: str) -> ExtractedMemory:
        try:
            return parse_extracted_memory(raw)
        except ConsolidationParseError:
            pass
        repair_prompt = (
            "The previous response was not valid JSON. Output ONLY valid JSON matching "
            "the requested schema, with no markdown fences and no commentary.\n\n"
            f"Previous response:\n{raw}\n\n"
            "Now output valid JSON:"
        )
        try:
            response = await self.llm.complete(
                [Message(role="user", content=repair_prompt)],
                system=_REPAIR_SYSTEM,
                temperature=0.0,
            )
            return parse_extracted_memory(response.text)
        except Exception:
            log.warning("Consolidation JSON repair failed")
            return ExtractedMemory()

    def _persist(self, trace: ReActTrace, extracted: ExtractedMemory) -> None:
        project_id = trace.project_context.project_id
        if project_id and self.knowledge_store:
            if hasattr(self.knowledge_store, "add_many"):
                self._persist_jsonl(project_id, extracted)
            else:
                self._persist_legacy(project_id, extracted)

        if self.plan_memory and extracted.plan and extracted.verified_code:
            try:
                self.plan_memory.record(
                    problem=trace.problem,
                    goal_type=extracted.plan.goal_type,
                    plan=extracted.plan,
                    verified_code=extracted.verified_code,
                )
            except Exception as exc:
                log.warning("Failed to record plan: %s", exc)

    def _persist_jsonl(self, project_id: str, extracted: ExtractedMemory) -> None:
        if _graph_nodes(extracted):
            self._persist_graph_jsonl(project_id, extracted)
            return
        graph_refs: dict[str, str] = {}
        for idx, fact in enumerate(extracted.facts):
            try:
                inserted = self.knowledge_store.add_many(
                    project_id,
                    [_with_memory_defaults(fact)],
                    [],
                    [],
                )
                _record_graph_ref(graph_refs, "fact", idx, inserted.get("facts") if isinstance(inserted, dict) else None)
            except Exception as exc:
                log.warning("Failed to add fact: %s", exc)
        for idx, intuition in enumerate(extracted.intuitions):
            try:
                inserted = self.knowledge_store.add_many(
                    project_id,
                    [],
                    [_with_memory_defaults(intuition, {"kind": "consolidated"})],
                    [],
                )
                _record_graph_ref(
                    graph_refs,
                    "intuition",
                    idx,
                    inserted.get("intuitions") if isinstance(inserted, dict) else None,
                )
            except Exception as exc:
                log.warning("Failed to add intuition: %s", exc)
        for idx, trick in enumerate(extracted.tricks):
            try:
                inserted = self.knowledge_store.add_many(
                    project_id,
                    [],
                    [],
                    [_with_memory_defaults(trick)],
                )
                rows = inserted.get("tricks") if isinstance(inserted, dict) else None
                _record_graph_ref(graph_refs, "trick", idx, rows)
                _record_graph_ref(graph_refs, "technique", idx, rows)
            except Exception as exc:
                log.warning("Failed to add trick: %s", exc)
        self._persist_graph_edges(project_id, extracted, graph_refs)

    def _persist_graph_jsonl(self, project_id: str, extracted: ExtractedMemory) -> None:
        graph_refs: dict[str, str] = {}
        nodes = [_prepare_graph_node(node) for node in _graph_nodes(extracted)]
        nodes = [node for node in nodes if str(node.get("status") or "").lower() != "rejected"]
        if hasattr(self.knowledge_store, "add_knowledge_graph_nodes") and nodes:
            try:
                inserted_nodes = self.knowledge_store.add_knowledge_graph_nodes(project_id, nodes)
                for node in inserted_nodes:
                    if not isinstance(node, Mapping):
                        continue
                    ref = str(node.get("ref") or "").strip()
                    node_id = str(node.get("id") or "").strip()
                    if ref and node_id:
                        graph_refs[ref] = node_id
            except Exception as exc:
                log.warning("Failed to add knowledge graph nodes: %s", exc)
        for node in nodes:
            ref = str(node.get("ref") or "").strip()
            node_id = str(node.get("id") or "").strip()
            if ref and node_id:
                graph_refs.setdefault(ref, node_id)

        facts, intuitions, tricks = _derive_legacy_memories_from_graph(nodes)
        try:
            if facts or intuitions or tricks:
                self.knowledge_store.add_many(project_id, facts, intuitions, tricks)
        except Exception as exc:
            log.warning("Failed to add graph-derived legacy memories: %s", exc)

        self._persist_graph_edges(project_id, extracted, graph_refs)

    def _persist_graph_edges(
        self,
        project_id: str,
        extracted: ExtractedMemory,
        graph_refs: dict[str, str],
    ) -> None:
        if not hasattr(self.knowledge_store, "add_knowledge_graph_edges"):
            return
        graph = extracted.knowledge_graph if isinstance(extracted.knowledge_graph, dict) else {}
        raw_edges = graph.get("edges") if isinstance(graph.get("edges"), list) else []
        edges: list[dict[str, Any]] = []
        for edge in raw_edges:
            if not isinstance(edge, dict):
                continue
            if str(edge.get("status") or "").strip().lower() == "rejected":
                continue
            source = _resolve_graph_endpoint(str(edge.get("source") or ""), graph_refs)
            target = _resolve_graph_endpoint(str(edge.get("target") or ""), graph_refs)
            if not source or not target or source == target:
                continue
            edges.append(
                {
                    "source": source,
                    "target": target,
                    "kind": edge.get("kind") or "connects_to",
                    "label": edge.get("label") or "",
                    "evidence": edge.get("evidence") or "",
                    "weight": _graph_weight(edge.get("weight")),
                    "status": edge.get("status") or "candidate",
                    "score": edge.get("score") or "",
                    "review_note": edge.get("review_note") or "",
                    "metadata": {
                        "origin": "memory_consolidation",
                        "source_ref": edge.get("source") or "",
                        "target_ref": edge.get("target") or "",
                        **(edge.get("metadata") if isinstance(edge.get("metadata"), dict) else {}),
                    },
                }
            )
        if not edges:
            return
        try:
            self.knowledge_store.add_knowledge_graph_edges(project_id, edges)
        except Exception as exc:
            log.warning("Failed to add knowledge graph edges: %s", exc)

    def _persist_legacy(self, project_id: str, extracted: ExtractedMemory) -> None:
        for fact in extracted.facts:
            try:
                self.knowledge_store.add_fact(
                    project_id,
                    statement=fact.get("statement", ""),
                    why=fact.get("why", ""),
                    source=fact.get("source") or "memory_consolidation",
                )
            except Exception as exc:
                log.warning("Failed to add fact: %s", exc)
        for intuition in extracted.intuitions:
            try:
                self.knowledge_store.add_intuition(
                    project_id,
                    title=intuition.get("title", ""),
                    body=intuition.get("body", ""),
                    kind=intuition.get("kind") or "consolidated",
                    source=intuition.get("source") or "memory_consolidation",
                )
            except Exception as exc:
                log.warning("Failed to add intuition: %s", exc)
        for trick in extracted.tricks:
            try:
                self.knowledge_store.add_trick(
                    project_id,
                    title=trick.get("title", ""),
                    body=trick.get("body", ""),
                    category=trick.get("category", ""),
                    source=trick.get("source") or "memory_consolidation",
                )
            except Exception as exc:
                log.warning("Failed to add trick: %s", exc)


def _normalize_consolidated_statuses(
    solution: ReActSolution,
    extracted: ExtractedMemory,
) -> None:
    """Consolidation may only write candidate or Lean-bound verified memories."""
    verified_artifacts = {
        code
        for code in solution.lean_proofs
        if isinstance(code, str) and code.strip()
    }

    top_level_verified = (
        extracted.verified_code
        if extracted.verified_code and extracted.verified_code in verified_artifacts
        else ""
    )
    extracted.verified_code = top_level_verified

    plan_verified = ""
    if extracted.plan is not None:
        if (
            extracted.plan.verified_code
            and extracted.plan.verified_code in verified_artifacts
        ):
            plan_verified = extracted.plan.verified_code
        extracted.plan.verified_code = plan_verified

    graph_nodes = _graph_nodes(extracted)
    graph_edges = _graph_edges(extracted)
    for item in (*extracted.facts, *extracted.intuitions, *extracted.tricks, *graph_nodes):
        evidence = str(item.get("evidence") or "")
        if evidence and evidence in verified_artifacts:
            item["status"] = "verified"
            item["formal_status"] = "lean_verified"
            item["source_type"] = "lean_verified"
            continue
        item["status"] = "candidate"
        if str(item.get("formal_status") or "").strip().lower() == "lean_verified":
            item["formal_status"] = "informal"
        if str(item.get("source_type") or "").strip().lower() == "lean_verified":
            item["source_type"] = "agent_trace"
    for edge in graph_edges:
        if str(edge.get("status") or "candidate").strip().lower() != "verified":
            edge["status"] = "candidate"


def _record_graph_ref(
    graph_refs: dict[str, str],
    kind: str,
    idx: int,
    rows: Any,
) -> None:
    if not isinstance(rows, list) or not rows:
        return
    first = rows[0]
    if not isinstance(first, Mapping):
        return
    row_id = str(first.get("id") or "").strip()
    if row_id:
        graph_refs[f"{kind}:{idx}"] = row_id


def _resolve_graph_endpoint(ref: str, graph_refs: dict[str, str]) -> str:
    ref = ref.strip()
    if not ref:
        return ""
    return graph_refs.get(ref, ref)


def _graph_weight(value: Any) -> float:
    try:
        weight = float(value)
    except Exception:
        return 1.0
    return max(0.0, min(weight, 1.0))


def _graph_nodes(extracted: ExtractedMemory) -> list[dict[str, Any]]:
    graph = extracted.knowledge_graph if isinstance(extracted.knowledge_graph, dict) else {}
    nodes = graph.get("nodes")
    return nodes if isinstance(nodes, list) else []


def _graph_edges(extracted: ExtractedMemory) -> list[dict[str, Any]]:
    graph = extracted.knowledge_graph if isinstance(extracted.knowledge_graph, dict) else {}
    edges = graph.get("edges")
    return edges if isinstance(edges, list) else []


def _prepare_graph_node(node: dict[str, Any]) -> dict[str, Any]:
    prepared = dict(node)
    ref = str(prepared.get("ref") or prepared.get("id") or uuid.uuid4().hex).strip()
    prepared["ref"] = ref
    if not str(prepared.get("id") or "").strip():
        prepared["id"] = f"kg-{uuid.uuid4().hex}"
    prepared.setdefault("source_type", "agent_trace")
    prepared.setdefault("status", "candidate")
    prepared.setdefault("created_by", "memory_consolidation")
    return prepared


def _derive_legacy_memories_from_graph(
    nodes: list[dict[str, Any]],
) -> tuple[list[dict[str, str]], list[dict[str, str]], list[dict[str, str]]]:
    facts: list[dict[str, str]] = []
    intuitions: list[dict[str, str]] = []
    tricks: list[dict[str, str]] = []
    for node in nodes:
        status = str(node.get("status") or "").strip().lower()
        if status == "rejected":
            continue
        kind = str(node.get("kind") or "").strip().lower()
        common = _legacy_common_from_node(node)
        if kind in {"definition", "theorem", "lemma", "proposition", "corollary", "exercise"}:
            statement = str(node.get("statement") or node.get("title") or "").strip()
            if not statement:
                continue
            facts.append(
                {
                    "id": str(node.get("id") or ""),
                    "statement": statement,
                    "why": str(node.get("body") or node.get("evidence") or "").strip(),
                    "formal_status": str(node.get("formal_status") or "").strip(),
                    "lean_name": str(node.get("lean_name") or "").strip(),
                    **common,
                }
            )
        elif kind == "intuition":
            title = str(node.get("title") or node.get("statement") or "").strip()
            if not title:
                continue
            intuitions.append(
                {
                    "id": str(node.get("id") or ""),
                    "title": title,
                    "body": str(node.get("body") or node.get("statement") or "").strip(),
                    "kind": str(_metadata_value(node, "kind") or "consolidated"),
                    **common,
                }
            )
        elif kind == "technique":
            title = str(node.get("title") or node.get("statement") or "").strip()
            if not title:
                continue
            tricks.append(
                {
                    "id": str(node.get("id") or ""),
                    "title": title,
                    "body": str(node.get("body") or node.get("statement") or "").strip(),
                    "category": str(_metadata_value(node, "category") or "other"),
                    "applicability": str(_metadata_value(node, "applicability") or ""),
                    "failure_mode": str(_metadata_value(node, "failure_mode") or ""),
                    **common,
                }
            )
    return facts, intuitions, tricks


def _legacy_common_from_node(node: dict[str, Any]) -> dict[str, str]:
    common: dict[str, str] = {
        "source": "memory_consolidation",
        "source_type": str(node.get("source_type") or "agent_trace"),
        "source_ref": str(node.get("source_ref") or ""),
        "source_title": str(node.get("source_title") or ""),
        "evidence": str(node.get("evidence") or ""),
        "score": str(node.get("score") or ""),
        "status": str(node.get("status") or "candidate"),
        "domain": str(node.get("domain") or ""),
        "tags": str(node.get("tags") or ""),
        "created_by": str(node.get("created_by") or "memory_consolidation"),
        "review_note": str(node.get("review_note") or ""),
    }
    return {key: value for key, value in common.items() if value}


def _metadata_value(node: dict[str, Any], key: str) -> Any:
    metadata = node.get("metadata")
    if isinstance(metadata, dict):
        return metadata.get(key)
    return None


def _apply_reviewer_preconditions(
    solution: ReActSolution,
    extracted: ExtractedMemory,
) -> None:
    """Apply solve-level reviewer gates before item scoring.

    The item reviewer scores only candidates that are still viable after the
    solve-level review. It cannot create verified memories; verification is
    handled by `_normalize_consolidated_statuses` via exact Lean evidence.
    """
    reviewer_found_issues = bool(solution.verification_issues)
    for item in (*extracted.facts, *extracted.intuitions, *extracted.tricks, *_graph_nodes(extracted), *_graph_edges(extracted)):
        if str(item.get("status") or "candidate").strip().lower() != "candidate":
            continue
        evidence = str(item.get("evidence") or "").strip()

        if reviewer_found_issues:
            item["status"] = "questioned"
            item.setdefault(
                "review_note",
                "Questioned because the solve reviewer reported issues.",
            )
            continue

        if solution.verification_status != "reviewed":
            continue

        if not evidence:
            item["status"] = "questioned"
            item.setdefault(
                "review_note",
                "Questioned because reviewer-approved solve did not provide item evidence.",
            )


def _candidate_review_items(extracted: ExtractedMemory) -> list[tuple[str, dict[str, Any]]]:
    items: list[tuple[str, dict[str, Any]]] = []
    for node in _graph_nodes(extracted):
        if str(node.get("status") or "candidate").strip().lower() == "candidate":
            ref = str(node.get("ref") or node.get("id") or "").strip()
            if ref:
                items.append((f"node:{ref}", node))
    for edge in _graph_edges(extracted):
        if str(edge.get("status") or "candidate").strip().lower() == "candidate":
            ref = str(edge.get("ref") or "").strip()
            if ref:
                items.append((f"edge:{ref}", edge))
    for kind, rows in (
        ("fact", extracted.facts),
        ("intuition", extracted.intuitions),
        ("trick", extracted.tricks),
    ):
        for index, item in enumerate(rows):
            if str(item.get("status") or "candidate").strip().lower() == "candidate":
                items.append((f"{kind}:{index}", item))
    return items


def _build_review_prompt(
    solution: ReActSolution,
    candidates: list[tuple[str, dict[str, Any]]],
) -> str:
    payload: dict[str, Any] = {
        "final_answer": solution.final_answer[:2000],
        "verification_status": solution.verification_status,
        "verification_issues": list(solution.verification_issues or [])[:10],
        "candidate_nodes": [],
        "candidate_edges": [],
        "legacy_candidate_memories": [],
    }
    for ref, item in candidates:
        common = {
            "ref": ref,
            "kind": item.get("kind", ""),
            "statement": item.get("statement", ""),
            "title": item.get("title", ""),
            "body": item.get("body", ""),
            "evidence": item.get("evidence", ""),
            "source_type": item.get("source_type", ""),
            "domain": item.get("domain", ""),
            "tags": item.get("tags", ""),
        }
        if ref.startswith("edge:"):
            payload["candidate_edges"].append({
                **common,
                "source": item.get("source", ""),
                "target": item.get("target", ""),
                "label": item.get("label", ""),
            })
        elif ref.startswith("node:"):
            payload["candidate_nodes"].append(common)
        else:
            payload["legacy_candidate_memories"].append({
                **common,
                "why": item.get("why", ""),
                "category": item.get("category", ""),
                "applicability": item.get("applicability", ""),
                "failure_mode": item.get("failure_mode", ""),
            })
    return json.dumps(payload, ensure_ascii=False)


def _apply_review_scores(extracted: ExtractedMemory, data: dict[str, Any]) -> None:
    by_ref = dict(_candidate_review_items(extracted))
    reviews = data.get("reviews", []) if isinstance(data, dict) else []
    for review in reviews if isinstance(reviews, list) else []:
        if not isinstance(review, dict):
            continue
        ref = str(review.get("ref") or "").strip()
        item = by_ref.get(ref)
        if item is None:
            continue
        try:
            score = float(review.get("score"))
        except (TypeError, ValueError):
            continue
        score = max(0.0, min(1.0, score))
        item["score"] = f"{score:.3f}".rstrip("0").rstrip(".")
        note = str(review.get("review_note") or "").strip()[:500]
        if note:
            item["review_note"] = note
        if score >= _REVIEWED_SCORE_THRESHOLD:
            item["status"] = "reviewed"
        elif score >= _QUESTIONED_SCORE_THRESHOLD:
            item["status"] = "questioned"
        else:
            item["status"] = "rejected"


def _promote_reviewed_memories(
    solution: ReActSolution,
    extracted: ExtractedMemory,
) -> None:
    """Backward-compatible wrapper for tests/imports."""
    _apply_reviewer_preconditions(solution, extracted)


def _with_memory_defaults(
    item: dict[str, str],
    extra_defaults: dict[str, str] | None = None,
) -> dict[str, str]:
    row = dict(item)
    defaults = {
        "source": "memory_consolidation",
        "source_type": "agent_trace",
        "status": "candidate",
        "created_by": "memory_consolidation",
    }
    if extra_defaults:
        defaults.update(extra_defaults)
    for key, value in defaults.items():
        row.setdefault(key, value)
    return row
