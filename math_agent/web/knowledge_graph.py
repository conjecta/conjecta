from __future__ import annotations

import hashlib
import re
from typing import Any

from math_agent.agent.materials import MaterialStore
from math_agent.web.project_store import ProjectStore

# Internal pipeline tags that were incorrectly promoted to "source" nodes.
_INTERNAL_SOURCE_LABELS = frozenset(
    {
        "memory_consolidation",
        "knowledge_evaluator",
        "agent_trace",
        "extracted",
        "project",
        "jsonl",
        "none",
        "consolidated",
        "manual",
        "user_prompt",
        "lean_verified",
        "pdf",
        "web",
    }
)


def is_displayable_source(label: str) -> bool:
    """Return True for real references (URLs, papers, notes), not pipeline tags."""
    text = (label or "").strip()
    if not text:
        return False
    lowered = text.lower()
    if lowered in _INTERNAL_SOURCE_LABELS:
        return False
    if re.fullmatch(r"[a-z][a-z0-9_]{2,40}", lowered) and "_" in lowered:
        # snake_case identifiers like memory_consolidation / knowledge_evaluator
        return False
    return True


def build_knowledge_graph(
    knowledge_store: Any,
    material_store: MaterialStore | None,
    project_id: str,
    *,
    edge_store: ProjectStore | None = None,
) -> dict[str, Any]:
    if edge_store is None:
        edge_store = knowledge_store
    nodes: list[dict[str, Any]] = []
    seen_nodes: set[str] = set()

    def add_node(node: dict[str, Any]) -> None:
        node_id = str(node.get("id") or "").strip()
        label = str(node.get("label") or "").strip()
        if not node_id or not label or node_id in seen_nodes:
            return
        seen_nodes.add(node_id)
        normalized = {
            "id": node_id,
            "kind": str(node.get("kind") or "fact"),
            "label": label,
            "body": str(node.get("body") or ""),
            "status": str(node.get("status") or "verified"),
            "confidence": node.get("confidence"),
            "source": str(node.get("source") or ""),
            "created_at": str(node.get("created_at") or ""),
            "metadata": node.get("metadata") if isinstance(node.get("metadata"), dict) else {},
        }
        nodes.append(normalized)

    def add_source(label: str) -> None:
        label = label.strip()
        if not is_displayable_source(label):
            return
        source_id = "source:" + hashlib.sha1(label.lower().encode("utf-8")).hexdigest()[:12]
        add_node(
            {
                "id": source_id,
                "kind": "source",
                "label": label,
                "status": "reference",
                "metadata": {},
            }
        )

    for row in knowledge_store.list_facts(project_id, limit=1000):
        add_node(
            {
                "id": row.get("id"),
                "kind": "fact",
                "label": row.get("statement"),
                "body": row.get("why") or "",
                "confidence": row.get("confidence"),
                "source": row.get("source") or "",
                "created_at": row.get("created_at") or row.get("createdAt") or "",
                "metadata": {"project_id": project_id},
            }
        )
        add_source(str(row.get("source") or ""))

    for row in knowledge_store.list_intuitions(project_id, limit=1000):
        add_node(
            {
                "id": row.get("id"),
                "kind": "intuition",
                "label": row.get("title"),
                "body": row.get("body") or "",
                "confidence": row.get("confidence"),
                "source": row.get("source") or "",
                "created_at": row.get("created_at") or row.get("createdAt") or "",
                "metadata": {"kind": row.get("kind") or ""},
            }
        )
        add_source(str(row.get("source") or ""))

    for row in knowledge_store.list_tricks(project_id, limit=1000):
        add_node(
            {
                "id": row.get("id"),
                "kind": "technique",
                "label": row.get("title") or row.get("name"),
                "body": row.get("body") or row.get("description") or "",
                "confidence": row.get("confidence"),
                "source": row.get("source") or "",
                "created_at": row.get("created_at") or row.get("createdAt") or "",
                "metadata": {"category": row.get("category") or ""},
            }
        )
        add_source(str(row.get("source") or ""))

    if material_store is not None:
        for material in material_store.list(project_id, limit=1000):
            add_node(
                {
                    "id": f"material:{material.id}",
                    "kind": "material",
                    "label": material.label,
                    "body": material.text[:500],
                    "status": "candidate",
                    "source": material.source,
                    "created_at": material.created_at,
                    "metadata": {"material_id": material.id, "material_kind": material.kind},
                }
            )
            add_source(material.source)

    valid_ids = {node["id"] for node in nodes}
    edges: list[dict[str, Any]] = []
    seen_edges: set[str] = set()
    for edge in edge_store.list_knowledge_graph_edges(project_id):
        source = str(edge.get("source") or "").strip()
        target = str(edge.get("target") or "").strip()
        edge_id = str(edge.get("id") or f"{source}:{edge.get('kind') or 'related_to'}:{target}")
        if not source or not target or source not in valid_ids or target not in valid_ids:
            continue
        if edge_id in seen_edges:
            continue
        seen_edges.add(edge_id)
        edges.append(
            {
                "id": edge_id,
                "source": source,
                "target": target,
                "kind": str(edge.get("kind") or "related_to"),
                "label": str(edge.get("label") or ""),
                "evidence": str(edge.get("evidence") or ""),
                "weight": float(edge.get("weight") if edge.get("weight") is not None else 1.0),
                "created_at": str(edge.get("created_at") or ""),
                "metadata": edge.get("metadata") if isinstance(edge.get("metadata"), dict) else {},
            }
        )

    return {
        "ok": True,
        "project_id": project_id,
        "nodes": nodes,
        "edges": edges,
        "summary": _graph_summary(nodes, edges),
        "source": "jsonl",
    }


def _graph_summary(nodes: list[dict[str, Any]], edges: list[dict[str, Any]]) -> str:
    if not nodes:
        return "Project knowledge graph is empty."
    return f"{len(nodes)} nodes and {len(edges)} relationships."
