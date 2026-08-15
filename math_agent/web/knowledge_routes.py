"""HTTP routes for project knowledge, materials, knowledge graph, selection
pipelines, review queue, and knowledge cards."""
from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any, AsyncIterator

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from math_agent.llm.factory import create_backend_from_model_string
from math_agent.llm.base import Message
from math_agent.text_utils import parse_json_blob
from math_agent.web.agent_factory import (
    _check_request_body_size,
    _maybe_knowledge_store,
    _material_store,
    _platform_api_key,
    _project_access_from_request,
    _project_store,
    _resolve_platform_model,
    _tenant_project_store,
)
from math_agent.web.knowledge_cards import KnowledgeCardService
from math_agent.web.knowledge_graph import build_knowledge_graph
from math_agent.web.knowledge_selection import (
    KNOWLEDGE_RESULT_MARKER,
    SATISFACTION_ACTIONS_MARKER,
    build_knowledge_catalogs as _build_knowledge_catalogs,
    extract_rephrased_request as _extract_rephrased_request,
    format_augmented_prompt as _format_augmented_prompt,
    normalize_conversation_history as _normalize_conversation_history,
    normalize_text_value as _normalize_text_value,
    parse_complete_json_object as _parse_complete_json_object,
    resolve_selected_ids as _resolve_selected_ids,
    selection_summary as _selection_summary,
    split_marker_response as _split_marker_response,
    split_satisfaction_response as _split_satisfaction_response,
)
from math_agent.web.knowledge_text import short_knowledge_rows, short_knowledge_texts
from math_agent.web.knowledge_translation import (
    TRANSLATION_FIELDS,
    existing_translation,
    translate_knowledge_item,
)
from math_agent.web.security import optional_auth_user, require_auth_user

web_log = logging.getLogger("math_agent.web")
_KNOWLEDGE_SELECTION_RESPONSE_LIMIT = 128_000

router = APIRouter(prefix="/api", tags=["knowledge"])


def _card_service(request: Request) -> KnowledgeCardService:
    user = require_auth_user(request)
    return KnowledgeCardService(user_id=user.user_id)


@router.get("/projects/{project_id}/review-queue")
async def list_review_queue(project_id: str, request: Request, status: str | None = None):
    _, store = _tenant_project_store(request)
    return {
        "ok": True,
        "items": await asyncio.to_thread(store.list_review_items, project_id, status=status),
    }


@router.post("/projects/{project_id}/review-queue")
async def add_review_queue_item(project_id: str, payload: dict[str, Any], request: Request):
    _, store = _tenant_project_store(request)
    item = await asyncio.to_thread(store.add_review_item, project_id, payload)
    return {"ok": True, "item": item}


@router.post("/projects/{project_id}/review-queue/{item_id}/resolve")
async def resolve_review_queue_item(
    project_id: str, item_id: str, payload: dict[str, Any], request: Request
):
    _, store = _tenant_project_store(request)
    item = await asyncio.to_thread(
        store.resolve_review_item,
        project_id,
        item_id,
        status=(payload.get("status") or "").strip().lower(),
        reason=(payload.get("reason") or "").strip(),
    )
    return {"ok": True, "item": item}


@router.post("/extract-knowledge")
async def extract_knowledge(payload: dict[str, Any], request: Request):
    _check_request_body_size(request)
    user = require_auth_user(request)
    source_text = (payload.get("source_text") or "").strip()
    source_title = (payload.get("source_title") or "").strip()
    source_url = (payload.get("source_url") or "").strip()
    project_id = (payload.get("project_id") or "default").strip()
    model = payload.get("model")
    api_key = _platform_api_key(model)
    if not source_text:
        return {"ok": False, "error": "No source_text provided."}
    try:
        model = _resolve_platform_model(model)
    except HTTPException as exc:
        return {"ok": False, "error": exc.detail}

    # Prefer locally persisted JSONL knowledge when available.
    store = _maybe_knowledge_store(user.user_id)
    if store is not None:
        try:
            existing_facts = await asyncio.to_thread(store.list_facts, project_id)
            existing_intuitions = await asyncio.to_thread(store.list_intuitions, project_id)
            existing_tricks = await asyncio.to_thread(store.list_tricks, project_id)
        except Exception as exc:
            web_log.warning("Failed to load existing knowledge from JSONL store: %s", exc)
            existing_facts = payload.get("existing_facts") or []
            existing_intuitions = payload.get("existing_intuitions") or []
            existing_tricks = payload.get("existing_tricks") or []
    else:
        existing_facts = payload.get("existing_facts") or []
        existing_intuitions = payload.get("existing_intuitions") or []
        existing_tricks = payload.get("existing_tricks") or []

    # Cap source text and existing-knowledge lists to keep the prompt within sensible bounds.
    truncated = source_text if len(source_text) <= 18000 else source_text[:18000]

    existing_blob = {
        "facts": short_knowledge_texts(existing_facts),
        "intuitions": short_knowledge_texts(existing_intuitions),
        "tricks": short_knowledge_texts(existing_tricks),
    }

    llm = create_backend_from_model_string(model, temperature=0.0, api_key=api_key)
    system = (
        "You are a math-research knowledge extractor for Conjecta.\n"
        "Given a source text (excerpt of an article, paper, or lecture), extract candidate items in three buckets:\n"
        "- facts: verbatim or near-verbatim theorem/lemma/proposition/corollary statements (mathematical claims).\n"
        "- intuitions: high-level strategic ideas, motivations, or heuristics the source relies on.\n"
        "- tricks: reusable proof techniques (e.g. 'induction on structure', 'WLOG', 'telescoping sum',\n"
        "  'pigeonhole', 'duality', 'reduction to base case', 'contradiction').\n"
        "You will be given the user's existing knowledge for each bucket; SKIP any candidate that is\n"
        "semantically already present. Too much redundant intuition is bad — be ruthless about dedupe.\n"
        "Return ONLY strict JSON with this shape:\n"
        '{ "facts": [{"statement": "...", "why": "..."}],\n'
        '  "intuitions": [{"title": "...", "body": "...", "kind": "strategy|heuristic|motivation|other"}],\n'
        '  "tricks": [{"title": "...", "body": "...", "category": "induction|wlog|contradiction|reduction|duality|pigeonhole|telescoping|other"}] }\n'
        "Limit: at most 8 facts, 5 intuitions, 5 tricks. Each `body`/`why` is one short sentence.\n"
        "If the source is not mathematical, return all three lists empty."
    )
    user = json.dumps(
        {
            "source_title": source_title,
            "source_url": source_url,
            "source_text": truncated,
            "existing": existing_blob,
        },
        ensure_ascii=False,
    )
    response = await llm.complete([Message(role="user", content=user)], system=system, temperature=0.0)
    raw = response.text
    data = parse_json_blob(raw)
    if data is None:
        return {"ok": True, "facts": [], "intuitions": [], "tricks": []}

    facts_out = _normalize_extract_list(data.get("facts"), ("statement", "why"))
    intuitions_out = _normalize_extract_list(data.get("intuitions"), ("title", "body", "kind"))
    tricks_out = _normalize_extract_list(data.get("tricks"), ("title", "body", "category"))

    # Server-side defensive dedupe against existing items (lowercase substring match).
    def _dedupe(candidates: list[dict[str, Any]], existing_strs: list[str], primary_key: str) -> list[dict[str, Any]]:
        existing_norm = [s.lower().strip() for s in existing_strs if s]
        out: list[dict[str, Any]] = []
        for cand in candidates:
            head = (cand.get(primary_key) or "").lower().strip()
            if not head:
                continue
            if any(head == e or (len(head) > 12 and head in e) or (len(e) > 12 and e in head) for e in existing_norm):
                continue
            out.append(cand)
        return out

    facts_out = _dedupe(facts_out, existing_blob["facts"], "statement")
    intuitions_out = _dedupe(intuitions_out, existing_blob["intuitions"], "title")
    tricks_out = _dedupe(tricks_out, existing_blob["tricks"], "title")

    # Persist newly extracted knowledge to the local JSONL store.
    persisted = {"facts": [], "intuitions": [], "tricks": []}
    persisted_ok = False
    if store is not None and (facts_out or intuitions_out or tricks_out):
        try:
            source = source_url or source_title or "extracted"
            fact_rows = [{"statement": f["statement"], "why": f.get("why", ""), "source": source} for f in facts_out]
            intuition_rows = [
                {"title": i["title"], "body": i["body"], "kind": i.get("kind", ""), "source": source}
                for i in intuitions_out
            ]
            trick_rows = [
                {"title": t["title"], "body": t["body"], "category": t.get("category", ""), "source": source}
                for t in tricks_out
            ]
            persisted = store.add_many(project_id, fact_rows, intuition_rows, trick_rows)
            persisted_ok = any(persisted.get(key) for key in ("facts", "intuitions", "tricks"))
            web_log.info(
                "Persisted knowledge to JSONL project=%s facts=%d intuitions=%d tricks=%d",
                project_id,
                len(persisted.get("facts") or []),
                len(persisted.get("intuitions") or []),
                len(persisted.get("tricks") or []),
            )
        except Exception as exc:
            web_log.warning("Failed to persist knowledge to JSONL store: %s", exc)

    return {
        "ok": True,
        "facts": facts_out[:8],
        "intuitions": intuitions_out[:5],
        "tricks": tricks_out[:5],
        "persisted": persisted_ok,
        "persisted_ids": persisted,
    }


@router.get("/knowledge")
async def list_knowledge(
    request: Request,
    project_id: str = "default",
    kind: str | None = None,
    limit: int = 200,
    owner_user_id: str | None = None,
):
    """List project knowledge from the authoritative store (tenant-scoped to lead)."""
    _, access = await asyncio.to_thread(_project_access_from_request, request, project_id, owner_user_id=owner_user_id)
    store = _maybe_knowledge_store(access.knowledge_tenant_id)
    if store is None:
        return {"ok": True, "facts": [], "intuitions": [], "tricks": [], "source": "none"}

    try:
        kinds = {"facts", "intuitions", "tricks"}
        selected = {kind} if kind in kinds else kinds
        result: dict[str, Any] = {"ok": True, "source": "jsonl", "owner_user_id": access.owner_user_id}
        if "facts" in selected:
            result["facts"] = await asyncio.to_thread(store.list_facts, project_id, limit=limit)
        if "intuitions" in selected:
            result["intuitions"] = await asyncio.to_thread(store.list_intuitions, project_id, limit=limit)
        if "tricks" in selected:
            result["tricks"] = await asyncio.to_thread(store.list_tricks, project_id, limit=limit)
        return result
    except Exception as exc:
        web_log.warning("Failed to list knowledge from JSONL store: %s", exc)
        return {"ok": False, "error": str(exc)}


@router.post("/knowledge/translate")
async def translate_knowledge(payload: dict[str, Any], request: Request):
    """Create and persist a Simplified Chinese version of one English knowledge item."""
    project_id = str(payload.get("project_id") or "default").strip()
    owner_user_id = str(payload.get("owner_user_id") or "").strip() or None
    _, access = await asyncio.to_thread(_project_access_from_request, request, project_id, owner_user_id=owner_user_id)
    item_id = str(payload.get("item_id") or "").strip()
    kind = str(payload.get("kind") or "").strip().lower()
    if not item_id:
        raise HTTPException(status_code=400, detail="Knowledge item id is required.")
    if kind not in TRANSLATION_FIELDS:
        raise HTTPException(status_code=400, detail="Knowledge kind must be fact, intuition, or trick.")

    store = _maybe_knowledge_store(access.knowledge_tenant_id)
    if store is None:
        raise HTTPException(status_code=503, detail="Knowledge store is unavailable.")
    list_method = getattr(store, f"list_{kind}s")
    item = next(
        (row for row in await asyncio.to_thread(list_method, project_id, limit=1000) if str(row.get("id") or "") == item_id),
        None,
    )
    if item is None:
        raise HTTPException(status_code=404, detail="Knowledge item not found.")

    saved = existing_translation(item, kind)
    if saved is not None:
        return {"ok": True, "translation": saved, "cached": True}

    model = os.getenv(
        "CONJECTA_TRANSLATION_MODEL",
        "shengsuanyun/openai/gpt-5.4-mini",
    ).strip()
    try:
        llm = create_backend_from_model_string(
            model,
            temperature=0.0,
            api_key=_platform_api_key(model),
            timeout_seconds=60.0,
        )
        translated = await translate_knowledge_item(llm, item, kind)
        if hasattr(store, "update_item"):
            await asyncio.to_thread(store.update_item, project_id, item_id, kind, translated)
        else:
            await asyncio.to_thread(store.update_knowledge_item, project_id, item_id, kind, translated)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        web_log.warning("Knowledge translation failed project=%s item=%s: %s", project_id, item_id, exc)
        raise HTTPException(status_code=502, detail="中文翻译暂时失败，请稍后重试。") from exc

    return {"ok": True, "translation": translated, "cached": False, "model": model}


_KNOWLEDGE_EDITABLE_FIELDS: dict[str, frozenset[str]] = {
    "fact": frozenset({"statement", "why", "statement_zh", "why_zh"}),
    "intuition": frozenset({"title", "body", "title_zh", "body_zh"}),
    "trick": frozenset({"title", "body", "title_zh", "body_zh"}),
}


def _find_knowledge_item(store: Any, project_id: str, kind: str, item_id: str) -> dict[str, Any] | None:
    list_method = getattr(store, f"list_{kind}s", None)
    if list_method is None:
        return None
    return next(
        (row for row in list_method(project_id, limit=1000) if str(row.get("id") or "") == item_id),
        None,
    )


@router.patch("/knowledge/{kind}/{item_id}")
async def update_knowledge_item(
    kind: str,
    item_id: str,
    payload: dict[str, Any],
    request: Request,
    project_id: str = "default",
    owner_user_id: str | None = None,
):
    """Update editable content fields on one project knowledge item."""
    user, access = await asyncio.to_thread(_project_access_from_request, request, project_id, owner_user_id=owner_user_id)
    kind = str(kind or "").strip().lower()
    item_id = str(item_id or "").strip()
    if kind not in _KNOWLEDGE_EDITABLE_FIELDS:
        raise HTTPException(status_code=400, detail="Knowledge kind must be fact, intuition, or trick.")
    if not item_id:
        raise HTTPException(status_code=400, detail="Knowledge item id is required.")

    store = _maybe_knowledge_store(access.knowledge_tenant_id)
    if store is None:
        raise HTTPException(status_code=503, detail="Knowledge store is unavailable.")
    if await asyncio.to_thread(_find_knowledge_item, store, project_id, kind, item_id) is None:
        raise HTTPException(status_code=404, detail="Knowledge item not found.")

    allowed = _KNOWLEDGE_EDITABLE_FIELDS[kind]
    fields = {
        key: value
        for key, value in (payload or {}).items()
        if key in allowed and isinstance(value, str)
    }
    if not fields:
        raise HTTPException(status_code=400, detail="No editable fields provided.")

    if hasattr(store, "update_item"):
        await asyncio.to_thread(store.update_item, project_id, item_id, kind, fields)
        try:
            existing = await asyncio.to_thread(_find_knowledge_item, store, project_id, kind, item_id) or {}
            existing_meta = (
                existing.get("metadata") if isinstance(existing.get("metadata"), dict) else {}
            )
            await asyncio.to_thread(
                store.update_item,
                project_id,
                item_id,
                kind,
                {"metadata": {**existing_meta, "updated_by": user.user_id}},
            )
        except Exception:
            pass
    else:
        await asyncio.to_thread(store.update_knowledge_item, project_id, item_id, kind, fields)

    updated = await asyncio.to_thread(_find_knowledge_item, store, project_id, kind, item_id)
    return {"ok": True, "item": updated or {"id": item_id, **fields}}


@router.delete("/knowledge/{kind}/{item_id}")
async def delete_knowledge_item(
    kind: str,
    item_id: str,
    request: Request,
    project_id: str = "default",
    owner_user_id: str | None = None,
):
    """Delete one project knowledge item (fact, intuition, or trick)."""
    _, access = await asyncio.to_thread(_project_access_from_request, request, project_id, owner_user_id=owner_user_id)
    kind = str(kind or "").strip().lower()
    item_id = str(item_id or "").strip()
    if kind not in _KNOWLEDGE_EDITABLE_FIELDS:
        raise HTTPException(status_code=400, detail="Knowledge kind must be fact, intuition, or trick.")
    if not item_id:
        raise HTTPException(status_code=400, detail="Knowledge item id is required.")

    store = _maybe_knowledge_store(access.knowledge_tenant_id)
    if store is None:
        raise HTTPException(status_code=503, detail="Knowledge store is unavailable.")
    if await asyncio.to_thread(_find_knowledge_item, store, project_id, kind, item_id) is None:
        raise HTTPException(status_code=404, detail="Knowledge item not found.")

    if hasattr(store, "delete_item"):
        await asyncio.to_thread(store.delete_item, project_id, item_id, kind)
    else:
        await asyncio.to_thread(store.delete_knowledge_item, project_id, item_id, kind)
    return {"ok": True}


@router.get("/materials")
async def list_materials(request: Request, project_id: str = "default", limit: int = 200):
    user = require_auth_user(request)
    try:
        store = _material_store(user.user_id)
        bounded = max(0, min(int(limit), 1000))
        materials = await asyncio.to_thread(store.list, project_id, limit=bounded)
        return {
            "ok": True,
            "project_id": project_id,
            "materials": [material.to_dict() for material in materials],
            "source": "jsonl",
        }
    except Exception as exc:
        web_log.warning("Failed to list materials: %s", exc)
        return {"ok": False, "error": str(exc)}


@router.get("/knowledge/graph")
async def list_knowledge_graph(request: Request, project_id: str = "default"):
    user = require_auth_user(request)
    try:
        project_store = _project_store(user.user_id)
        knowledge_store = _maybe_knowledge_store(user.user_id) or project_store
        material_store = _material_store(user.user_id)
        return await asyncio.to_thread(
            build_knowledge_graph,
            knowledge_store,
            material_store,
            project_id,
            edge_store=project_store,
        )
    except Exception as exc:
        web_log.warning("Failed to build knowledge graph: %s", exc)
        return {"ok": False, "error": str(exc)}


@router.post("/knowledge/graph/explore")
async def explore_knowledge_graph(payload: dict[str, Any], request: Request):
    user = require_auth_user(request)
    project_id = (payload.get("project_id") or "default").strip()
    focus = (payload.get("focus") or "").strip()
    model = payload.get("model")
    api_key = _platform_api_key(model)
    if not model:
        return {"ok": False, "error": "Model is required."}

    project_store = _project_store(user.user_id)
    knowledge_store = _maybe_knowledge_store(user.user_id) or project_store
    material_store = _material_store(user.user_id)
    current = await asyncio.to_thread(
        build_knowledge_graph,
        knowledge_store,
        material_store,
        project_id,
        edge_store=project_store,
    )
    nodes = current.get("nodes") or []
    if not nodes:
        return current

    catalog = [
        {
            "id": node.get("id"),
            "kind": node.get("kind"),
            "label": node.get("label"),
            "body": str(node.get("body") or "")[:300],
        }
        for node in nodes
        if node.get("kind") != "source"
    ][:80]
    if len(catalog) < 2:
        return current

    llm = create_backend_from_model_string(model, temperature=0.1, api_key=api_key)
    system = (
        "You are building a knowledge graph for Conjecta, a math research workbench.\n"
        "Given project knowledge nodes, propose only high-confidence relationships between existing node ids.\n"
        "Allowed edge kinds: derived_from, supports, uses, related_to, contradicts, references.\n"
        "Return ONLY strict JSON: {\"edges\":[{\"source\":\"id\",\"target\":\"id\",\"kind\":\"supports\","
        "\"label\":\"short relation\",\"evidence\":\"one short reason\",\"weight\":0.8}]}.\n"
        "Do not invent node ids. Keep at most 12 edges."
    )
    response = await llm.complete(
        [Message(role="user", content=json.dumps({"focus": focus, "nodes": catalog}, ensure_ascii=False))],
        system=system,
        temperature=0.1,
    )
    raw = response.text
    data = parse_json_blob(raw)
    raw_edges = data.get("edges") if isinstance(data, dict) else []
    node_ids = {str(node.get("id") or "") for node in nodes}
    edges: list[dict[str, Any]] = []
    if isinstance(raw_edges, list):
        for edge in raw_edges[:12]:
            if not isinstance(edge, dict):
                continue
            source = str(edge.get("source") or "").strip()
            target = str(edge.get("target") or "").strip()
            if source not in node_ids or target not in node_ids:
                continue
            edges.append(edge)

    project_store.add_knowledge_graph_edges(project_id, edges)
    return build_knowledge_graph(
        knowledge_store,
        material_store,
        project_id,
        edge_store=project_store,
    )


def _normalize_extract_list(raw: Any, keys: tuple[str, ...]) -> list[dict[str, Any]]:
    """Coerce LLM output into a list of dicts containing only the expected keys."""
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        normalized = {k: (str(item.get(k) or "").strip()) for k in keys}
        # Drop empty primary fields.
        if not normalized.get(keys[0]):
            continue
        out.append(normalized)
    return out


@router.post("/next-steps")
async def next_steps(payload: dict[str, Any], request: Request):
    require_auth_user(request)
    problem = (payload.get("problem") or "").strip()
    summary = (payload.get("summary") or "").strip()
    steps_summary = payload.get("steps_summary") or []
    project_snapshot = payload.get("project_snapshot") or {}
    model = payload.get("model")
    api_key = _platform_api_key(model)
    if not problem and not summary:
        return {"ok": False, "error": "No session context provided."}
    if not model:
        return {"ok": False, "error": "Model is required."}

    llm = create_backend_from_model_string(model, temperature=0.2, api_key=api_key)
    system = (
        "You are the post-session advisor for Conjecta, a math research assistant.\n"
        "After a solve completes, suggest 2 to 4 concrete actionable next steps the user could take.\n"
        "Suggestion vocabulary (use the `action` field):\n"
        "- invoke_lean: verify a specific statement with the Lean 4 verifier.\n"
        "- extract_knowledge: extract a fact / intuition / trick from the conversation or a referenced source.\n"
        "- open_subsession: start a new sub-session for an open lemma or sub-goal.\n"
        "- fetch_reference: inspect a specific URL relevant to the open problem.\n"
        "- note: plain advice the user might consider.\n"
        "Return ONLY strict JSON of the form:\n"
        '{ "suggestions": [{ "id": "s1", "title": "...", "action": "invoke_lean|extract_knowledge|open_subsession|fetch_reference|note", "detail": "...", "target_step": "" }, ...] }\n'
        "Each title is one imperative sentence (<= 80 chars). Each detail is one short justification.\n"
        "Be concrete: reference theorem numbers, statement fragments, or URLs from context when available.\n"
        "Avoid duplicating items the user already has in the project snapshot."
    )
    user = json.dumps(
        {
            "problem": problem,
            "final_summary": summary,
            "steps_summary": steps_summary,
            "project_snapshot": project_snapshot,
        },
        ensure_ascii=False,
    )
    response = await llm.complete([Message(role="user", content=user)], system=system, temperature=0.2)
    raw = response.text
    data = parse_json_blob(raw)
    if data is None:
        return {"ok": True, "suggestions": []}

    suggestions_raw = data.get("suggestions") if isinstance(data, dict) else None
    if not isinstance(suggestions_raw, list):
        return {"ok": True, "suggestions": []}

    valid_actions = {"invoke_lean", "extract_knowledge", "open_subsession", "fetch_reference", "note"}
    out: list[dict[str, Any]] = []
    for idx, item in enumerate(suggestions_raw[:4]):
        if not isinstance(item, dict):
            continue
        action = (item.get("action") or "note").strip()
        if action not in valid_actions:
            action = "note"
        title = (item.get("title") or "").strip()
        if not title:
            continue
        out.append(
            {
                "id": (item.get("id") or f"s{idx + 1}").strip() or f"s{idx + 1}",
                "title": title[:160],
                "action": action,
                "detail": (item.get("detail") or "").strip()[:400],
                "target_step": (item.get("target_step") or "").strip(),
            }
        )

    return {"ok": True, "suggestions": out}


@router.post("/explore-knowledge")
async def explore_knowledge(payload: dict[str, Any], request: Request):
    """Relate the project's existing facts/intuitions/tricks to each other,
    to common LLM knowledge, and (when relevant) to recommended external references.
    No file/URL inspection happens here — this is a pure synthesis endpoint."""
    require_auth_user(request)
    focus = (payload.get("focus") or "").strip()
    facts = payload.get("facts") or []
    intuitions = payload.get("intuitions") or []
    tricks = payload.get("tricks") or []
    model = payload.get("model")
    api_key = _platform_api_key(model)
    if not model:
        return {"ok": False, "error": "Model is required."}
    if not (facts or intuitions or tricks):
        return {
            "ok": True,
            "internal_links": [],
            "common_knowledge": [],
            "external_references": [],
            "summary": "Project knowledge base is empty — add a fact, intuition, or trick first.",
        }

    payload_blob = {
        "focus": focus,
        "facts": short_knowledge_rows(facts),
        "intuitions": short_knowledge_rows(intuitions),
        "tricks": short_knowledge_rows(tricks),
    }

    llm = create_backend_from_model_string(model, temperature=0.3, api_key=api_key)
    system = (
        "You are the explore-mode synthesizer for Conjecta, a math research assistant.\n"
        "Given the user's project knowledge (facts, intuitions, tricks) plus an optional focus,\n"
        "produce a synthesis with three buckets:\n"
        "- internal_links: pairs of items already in the project that meaningfully relate, with a one-line\n"
        "  reason. Use the items' exact titles/statements as `from` and `to` strings.\n"
        "- common_knowledge: classical results / named theorems / standard machinery from your training\n"
        "  data that bear on these items but are NOT yet in the project.\n"
        "- external_references: 0 to 3 specific, well-known sources (textbook chapters, survey papers,\n"
        "  lecture notes) that would deepen the user's understanding. Provide title + brief reason.\n"
        "Also produce a one-paragraph `summary` (<= 80 words) describing the overall shape of the project.\n"
        "Return ONLY strict JSON of this shape:\n"
        '{ "summary": "...",\n'
        '  "internal_links": [{"from": "...", "to": "...", "relation": "..."}],\n'
        '  "common_knowledge": [{"title": "...", "detail": "..."}],\n'
        '  "external_references": [{"title": "...", "detail": "...", "url": ""}] }\n'
        "Keep the lists tight — at most 6 internal_links, 5 common_knowledge, 3 external_references."
    )
    user = json.dumps(payload_blob, ensure_ascii=False)
    response = await llm.complete([Message(role="user", content=user)], system=system, temperature=0.3)
    raw = response.text
    data = parse_json_blob(raw)
    if data is None:
        return {"ok": True, "summary": "", "internal_links": [], "common_knowledge": [], "external_references": []}

    summary = (data.get("summary") or "").strip()[:600] if isinstance(data, dict) else ""
    internal = _normalize_extract_list(data.get("internal_links") if isinstance(data, dict) else None,
                                       ("from", "to", "relation"))[:6]
    common = _normalize_extract_list(data.get("common_knowledge") if isinstance(data, dict) else None,
                                     ("title", "detail"))[:5]
    external_raw = data.get("external_references") if isinstance(data, dict) else None
    external: list[dict[str, str]] = []
    if isinstance(external_raw, list):
        for item in external_raw[:3]:
            if not isinstance(item, dict):
                continue
            title = (item.get("title") or "").strip()
            if not title:
                continue
            external.append({
                "title": title[:200],
                "detail": (item.get("detail") or "").strip()[:300],
                "url": (item.get("url") or "").strip()[:300],
            })

    return {
        "ok": True,
        "summary": summary,
        "internal_links": internal,
        "common_knowledge": common,
        "external_references": external,
    }


async def _knowledge_selection_events(
    payload: dict[str, Any], *, user_id: str | None = None
) -> AsyncIterator[dict[str, Any]]:
    problem = (payload.get("problem") or "").strip()
    conversation_history = payload.get("conversation_history") or []
    project_id = (payload.get("project_id") or "default").strip()
    facts_in = payload.get("facts")
    intuitions_in = payload.get("intuitions")
    tricks_in = payload.get("tricks")
    model = payload.get("model")
    api_key = _platform_api_key(model)

    # Fall back to JSONL-backed project knowledge when explicit lists are not provided.
    if facts_in is None or intuitions_in is None or tricks_in is None:
        store = _maybe_knowledge_store(user_id)
        if store is not None:
            try:
                if facts_in is None:
                    facts_in = store.list_facts(project_id)
                if intuitions_in is None:
                    intuitions_in = store.list_intuitions(project_id)
                if tricks_in is None:
                    tricks_in = store.list_tricks(project_id)
            except Exception as exc:
                web_log.warning("Failed to load project knowledge from JSONL store: %s", exc)
                facts_in = facts_in or []
                intuitions_in = intuitions_in or []
                tricks_in = tricks_in or []

    facts_in = facts_in or []
    intuitions_in = intuitions_in or []
    tricks_in = tricks_in or []

    if not problem:
        yield {"type": "error", "message": "No prompt provided."}
        return
    if not model:
        yield {"type": "error", "message": "Model is required."}
        return

    fact_catalog, intuition_catalog, trick_catalog = _build_knowledge_catalogs(
        facts_in,
        intuitions_in,
        tricks_in,
    )
    if not (fact_catalog or intuition_catalog or trick_catalog):
        yield {
            "type": "done",
            "ok": True,
            "analysis": "",
            "selection_reasoning": "",
            "facts": [],
            "intuitions": [],
            "tricks": [],
            "augmented_prompt": problem,
            "reason": "No usable project knowledge.",
        }
        return

    history_blob = _normalize_conversation_history(conversation_history)
    llm = create_backend_from_model_string(model, temperature=0.0, api_key=api_key)

    yield {"type": "phase_start", "phase": "prepare", "label": "Prepare context"}

    system = (
        "You are the context-preparation subagent for Conjecta, a math research assistant.\n"
        "Think step by step in plain prose: analyze the prompt and conversation, select helpful\n"
        "catalog items by id, and compose the final prompt for the main reasoning agent.\n"
        "You may rephrase the user's goal for clarity. Stream your reasoning naturally.\n"
        f"After your reasoning, on its own line, output exactly {KNOWLEDGE_RESULT_MARKER}\n"
        "then strict JSON (no markdown fences):\n"
        '{ "selected_fact_ids": ["..."], "selected_intuition_ids": ["..."], "selected_trick_ids": ["..."],\n'
        '  "augmented_prompt": "full structured prompt for the reasoning agent",\n'
        '  "rephrased_prompt": "the current-request section only" }\n'
        "Use exact catalog ids. The augmented_prompt should weave in selected knowledge and a clear goal.\n"
        "If nothing is selected, augmented_prompt may be a rephrased user goal only."
    )
    user = json.dumps(
        {
            "current_prompt": problem[:2000],
            "conversation_history": history_blob,
            "catalog": {
                "facts": fact_catalog,
                "intuitions": intuition_catalog,
                "tricks": trick_catalog,
            },
        },
        ensure_ascii=False,
    )

    acc = ""
    prose_sent = 0
    streamed_result: dict[str, Any] | None = None
    terminal_error: dict[str, Any] | None = None
    llm_stream = llm.stream([Message(role="user", content=user)], system=system, temperature=0.0)
    try:
        async for response in llm_stream:
            chunk_text = response.text
            remaining = _KNOWLEDGE_SELECTION_RESPONSE_LIMIT - len(acc)
            if len(chunk_text) > remaining:
                if remaining:
                    acc += chunk_text[:remaining]
                    if KNOWLEDGE_RESULT_MARKER in acc:
                        prose = acc.split(KNOWLEDGE_RESULT_MARKER, 1)[0]
                    else:
                        prose = acc
                        for prefix_len in range(min(len(KNOWLEDGE_RESULT_MARKER) - 1, len(prose)), 0, -1):
                            if KNOWLEDGE_RESULT_MARKER.startswith(prose[-prefix_len:]):
                                prose = prose[:-prefix_len]
                                break
                    if len(prose) > prose_sent:
                        yield {"type": "token", "phase": "prepare", "text": prose[prose_sent:]}
                web_log.warning(
                    "select-knowledge response exceeded %d characters",
                    _KNOWLEDGE_SELECTION_RESPONSE_LIMIT,
                )
                terminal_error = {
                    "type": "error",
                    "message": "Knowledge selection response exceeded the safe size limit.",
                }
                break
            acc += chunk_text
            if KNOWLEDGE_RESULT_MARKER in acc:
                prose, json_part = acc.split(KNOWLEDGE_RESULT_MARKER, 1)
                streamed_result = _parse_complete_json_object(json_part)
            else:
                prose = acc
                for prefix_len in range(min(len(KNOWLEDGE_RESULT_MARKER) - 1, len(prose)), 0, -1):
                    if KNOWLEDGE_RESULT_MARKER.startswith(prose[-prefix_len:]):
                        prose = prose[:-prefix_len]
                        break
            if len(prose) > prose_sent:
                yield {"type": "token", "phase": "prepare", "text": prose[prose_sent:]}
                prose_sent = len(prose)
            if streamed_result is not None:
                break
    finally:
        close_stream = getattr(llm_stream, "aclose", None)
        if close_stream is not None:
            try:
                await close_stream()
            except Exception as exc:
                web_log.debug("Failed to close select-knowledge LLM stream: %s", exc)

    if terminal_error is not None:
        yield terminal_error
        return

    if streamed_result is None:
        _, result_data = _split_marker_response(acc, KNOWLEDGE_RESULT_MARKER)
    else:
        result_data = streamed_result
    facts_out = _resolve_selected_ids(result_data.get("selected_fact_ids"), fact_catalog, limit=6)
    intuitions_out = _resolve_selected_ids(result_data.get("selected_intuition_ids"), intuition_catalog, limit=4)
    tricks_out = _resolve_selected_ids(result_data.get("selected_trick_ids"), trick_catalog, limit=4)
    augmented = _normalize_text_value(result_data.get("augmented_prompt"))
    if not augmented or "=== Current Request ===" not in augmented:
        rephrased = _normalize_text_value(result_data.get("rephrased_prompt")) or problem
        augmented = _format_augmented_prompt(rephrased, facts_out, intuitions_out, tricks_out)
    rephrased = _normalize_text_value(
        result_data.get("rephrased_prompt")
    ) or _extract_rephrased_request(augmented, problem)
    selection_summary = _selection_summary(facts_out, intuitions_out, tricks_out)

    yield {"type": "phase_done", "phase": "prepare", "text": selection_summary or augmented[:400]}

    web_log.info(
        "select-knowledge: facts=%d intuitions=%d tricks=%d rephrased=%s",
        len(facts_out),
        len(intuitions_out),
        len(tricks_out),
        rephrased[:80],
    )
    yield {
        "type": "done",
        "ok": True,
        "facts": facts_out,
        "intuitions": intuitions_out,
        "tricks": tricks_out,
        "augmented_prompt": augmented,
        "rephrased_prompt": rephrased,
        "reason": selection_summary,
    }


def _sse_encode(event: dict[str, Any]) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


@router.post("/select-knowledge/stream")
async def select_knowledge_stream(payload: dict[str, Any], request: Request):
    _check_request_body_size(request)
    user = require_auth_user(request)
    try:
        _resolve_platform_model(payload.get("model"), default_to_config=True)
    except HTTPException as exc:
        return JSONResponse({"ok": False, "error": exc.detail}, status_code=exc.status_code)

    async def event_stream() -> AsyncIterator[str]:
        events = _knowledge_selection_events(payload, user_id=user.user_id)
        try:
            async for event in events:
                yield _sse_encode(event)
        finally:
            await events.aclose()

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.post("/select-knowledge")
async def select_knowledge(payload: dict[str, Any], request: Request):
    """Non-streaming fallback — collects the final result from the selection pipeline."""
    _check_request_body_size(request)
    user = require_auth_user(request)
    try:
        _resolve_platform_model(payload.get("model"), default_to_config=True)
    except HTTPException as exc:
        return JSONResponse({"ok": False, "error": exc.detail}, status_code=exc.status_code)

    events = _knowledge_selection_events(payload, user_id=user.user_id)
    try:
        async for event in events:
            if event.get("type") == "error":
                return {"ok": False, "error": event.get("message", "Selection failed.")}
            if event.get("type") == "done":
                return {
                    "ok": True,
                    "facts": event.get("facts") or [],
                    "intuitions": event.get("intuitions") or [],
                    "tricks": event.get("tricks") or [],
                    "reason": event.get("reason") or "",
                    "analysis": event.get("analysis") or "",
                    "augmented_prompt": event.get("augmented_prompt") or payload.get("problem", ""),
                    "rephrased_prompt": event.get("rephrased_prompt") or payload.get("problem", ""),
                }
    finally:
        await events.aclose()
    return {"ok": False, "error": "Selection produced no result."}


def _first_normalized_value(
    item: dict[str, Any], keys: tuple[str, ...], *, limit: int
) -> str:
    for key in keys:
        text = _normalize_text_value(item.get(key), limit=limit)
        if text:
            return text
    return ""


def _safe_int(value: Any) -> int:
    if isinstance(value, (dict, list, tuple, set)):
        return 0
    try:
        return int(value or 0)
    except (TypeError, ValueError, OverflowError):
        return 0


def _knowledge_snapshot(
    facts_in: Any,
    intuitions_in: Any,
    tricks_in: Any,
    materials_in: Any,
) -> dict[str, list[dict[str, Any]]]:
    facts: list[dict[str, Any]] = []
    for item in facts_in[:40] if isinstance(facts_in, list) else []:
        if not isinstance(item, dict):
            continue
        fid = _normalize_text_value(item.get("id"))
        title = _first_normalized_value(item, ("statement", "title"), limit=240)
        if fid and title:
            facts.append({
                "id": fid,
                "title": title,
                "body": _first_normalized_value(item, ("note", "body"), limit=280),
            })

    intuitions: list[dict[str, Any]] = []
    for item in intuitions_in[:40] if isinstance(intuitions_in, list) else []:
        if not isinstance(item, dict):
            continue
        iid = _normalize_text_value(item.get("id"))
        title = _normalize_text_value(item.get("title"), limit=240)
        if iid and title:
            intuitions.append({
                "id": iid,
                "title": title,
                "body": _normalize_text_value(item.get("body"), limit=280),
                "successCount": _safe_int(item.get("successCount")),
            })

    tricks: list[dict[str, Any]] = []
    for item in tricks_in[:40] if isinstance(tricks_in, list) else []:
        if not isinstance(item, dict):
            continue
        tid = _normalize_text_value(item.get("id"))
        title = _normalize_text_value(item.get("title"), limit=240)
        if tid and title:
            tricks.append({
                "id": tid,
                "title": title,
                "body": _normalize_text_value(item.get("body"), limit=280),
                "successCount": _safe_int(item.get("successCount")),
            })

    materials: list[dict[str, Any]] = []
    for item in materials_in[:50] if isinstance(materials_in, list) else []:
        if not isinstance(item, dict):
            continue
        status = _normalize_text_value(item.get("status")) or "candidate"
        if status != "candidate":
            continue
        mid = _normalize_text_value(item.get("id"))
        title = _normalize_text_value(item.get("title"), limit=240)
        if not mid or not title:
            continue
        kind = _normalize_text_value(item.get("kind")).lower()
        if kind not in {"fact", "intuition", "trick"}:
            kind = "fact"
        materials.append({
            "id": mid,
            "kind": kind,
            "title": title,
            "body": _normalize_text_value(item.get("body"), limit=400),
            "positiveOutcomes": _safe_int(item.get("positiveOutcomes")),
            "negativeOutcomes": _safe_int(item.get("negativeOutcomes")),
            "usageCount": _safe_int(item.get("usageCount")),
        })

    return {"facts": facts, "intuitions": intuitions, "tricks": tricks, "materials": materials}


async def _evaluate_satisfaction_events(payload: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
    problem = (payload.get("problem") or "").strip()
    conversation_history = payload.get("conversation_history") or []
    prior_session = payload.get("prior_session") or {}
    knowledge_used = payload.get("knowledge_used") or {}
    facts_in = payload.get("facts") or []
    intuitions_in = payload.get("intuitions") or []
    tricks_in = payload.get("tricks") or []
    materials_in = payload.get("materials") or []
    model = payload.get("model")
    api_key = _platform_api_key(model)

    if not problem:
        yield {"type": "error", "message": "No prompt provided."}
        return
    if not model:
        yield {"type": "error", "message": "Model is required."}
        return
    if not conversation_history:
        yield {"type": "done", "ok": True, "message": "", "actions": {}}
        return

    history_blob = _normalize_conversation_history(conversation_history)
    snapshot = _knowledge_snapshot(facts_in, intuitions_in, tricks_in, materials_in)
    llm = create_backend_from_model_string(model, temperature=0.2, api_key=api_key)

    system = (
        "You are the session-satisfaction evaluator for Conjecta, a math research assistant.\n"
        "Read the user's NEW message in light of the prior conversation and the last agent output.\n"
        "Infer whether the user seems satisfied with the previous result (explicit thanks, building on it,\n"
        "moving to the next step) or dissatisfied (corrections, confusion, starting over).\n"
        "Also judge which project facts / intuitions / techniques / candidate materials were most useful\n"
        "to outputs the user appears happy with.\n"
        "Write 2-5 sentences of natural prose TO THE USER as your entire visible reply. Be concise and warm.\n"
        "If you are nailing down a candidate intuition (see actions), say so plainly in the prose.\n"
        "Do not use bullet lists or JSON in the prose.\n"
        f"After the prose, on its own line, output exactly {SATISFACTION_ACTIONS_MARKER}\n"
        "then strict JSON (no markdown fences) with this shape:\n"
        '{ "user_satisfied": true|false|null,\n'
        '  "useful_items": [{"type": "fact|intuition|trick|material", "id": "...", "impact": "..."}],\n'
        '  "material_outcomes": [{"material_id": "...", "outcome": "positive|negative"}],\n'
        '  "nail_down": [{"material_id": "...", "rephrased_title": "...", "rephrased_body": "...", "reason": "..."}],\n'
        '  "promote_materials": [{"material_id": "...", "rephrased_title": "...", "rephrased_body": "...", "reason": "..."}] }\n'
        "Rules for actions:\n"
        "- Record positive material_outcomes when the user clearly liked a result tied to that candidate.\n"
        "- Include nail_down when a candidate intuition is ready to become verified project knowledge;\n"
        "  rephrase mildly from history and explain in your prose that you are nailing it down.\n"
        "- promote_materials: promote any candidate (fact/intuition/trick) you judge ready — your call.\n"
        "- useful_items: cite ids from knowledge_used and the snapshot when they helped a happy outcome.\n"
        "- If satisfaction is unclear, set user_satisfied to null and keep actions minimal."
    )
    user = json.dumps(
        {
            "current_prompt": problem[:2000],
            "conversation_history": history_blob,
            "prior_session": prior_session,
            "knowledge_used_in_prior_session": knowledge_used,
            "project_knowledge": snapshot,
        },
        ensure_ascii=False,
    )

    acc = ""
    prose_sent = 0
    async for response in llm.stream([Message(role="user", content=user)], system=system, temperature=0.2):
        chunk_text = response.text
        acc += chunk_text
        if SATISFACTION_ACTIONS_MARKER in acc:
            prose = acc.split(SATISFACTION_ACTIONS_MARKER, 1)[0]
        else:
            prose = acc
            for prefix_len in range(min(len(SATISFACTION_ACTIONS_MARKER) - 1, len(prose)), 0, -1):
                if SATISFACTION_ACTIONS_MARKER.startswith(prose[-prefix_len:]):
                    prose = prose[:-prefix_len]
                    break
        if len(prose) > prose_sent:
            yield {"type": "token", "text": prose[prose_sent:]}
            prose_sent = len(prose)
        if SATISFACTION_ACTIONS_MARKER in acc:
            break

    message, actions = _split_satisfaction_response(acc)
    if not isinstance(actions, dict):
        actions = {}

    web_log.info(
        "evaluate-satisfaction: satisfied=%s useful=%d outcomes=%d nail_down=%d",
        actions.get("user_satisfied"),
        len(actions.get("useful_items") or []),
        len(actions.get("material_outcomes") or []),
        len(actions.get("nail_down") or []),
    )
    yield {
        "type": "done",
        "ok": True,
        "message": message,
        "user_satisfied": actions.get("user_satisfied"),
        "actions": actions,
    }


@router.post("/evaluate-satisfaction/stream")
async def evaluate_satisfaction_stream(payload: dict[str, Any], request: Request):
    require_auth_user(request)

    async def event_stream() -> AsyncIterator[str]:
        async for event in _evaluate_satisfaction_events(payload):
            yield _sse_encode(event)

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.post("/review-materials")
async def review_materials(payload: dict[str, Any], request: Request):
    """LLM reviewer over candidate materials. Decides whether each one looks
    promotable to a real fact / intuition / trick, given the project's already-verified
    knowledge. Returns a per-candidate verdict + reason."""
    require_auth_user(request)
    candidates = payload.get("candidates") or []
    facts = payload.get("facts") or []
    intuitions = payload.get("intuitions") or []
    tricks = payload.get("tricks") or []
    model = payload.get("model")
    api_key = _platform_api_key(model)
    if not model:
        return {"ok": False, "error": "Model is required."}
    if not isinstance(candidates, list) or not candidates:
        return {"ok": True, "verdicts": []}

    cand_payload: list[dict[str, Any]] = []
    for c in candidates[:40]:
        if not isinstance(c, dict):
            continue
        cid = (c.get("id") or "").strip()
        kind = (c.get("kind") or "fact").strip().lower()
        if kind not in ("fact", "intuition", "trick"):
            kind = "fact"
        title = (c.get("title") or "").strip()
        body = (c.get("body") or "").strip()
        if not cid or not title:
            continue
        cand_payload.append({
            "id": cid,
            "kind": kind,
            "title": title[:240],
            "body": body[:600],
            "usageCount": int(c.get("usageCount") or 0),
            "positiveOutcomes": int(c.get("positiveOutcomes") or 0),
            "negativeOutcomes": int(c.get("negativeOutcomes") or 0),
        })

    if not cand_payload:
        return {"ok": True, "verdicts": []}

    blob = {
        "candidates": cand_payload,
        "verified_knowledge": {
            "facts": short_knowledge_rows(facts),
            "intuitions": short_knowledge_rows(intuitions),
            "tricks": short_knowledge_rows(tricks),
        },
    }

    llm = create_backend_from_model_string(model, temperature=0.1, api_key=api_key)
    system = (
        "You are the reviewer agent for Conjecta, a math research assistant.\n"
        "For each candidate material, decide whether it should be promoted to the project's main\n"
        "knowledge base (facts / intuitions / tricks). A candidate should be promoted when:\n"
        "- it is mathematically meaningful and well-formed,\n"
        "- it adds something the verified knowledge does not already cover, and\n"
        "- its kind (fact / intuition / trick) is appropriate for its content.\n"
        "Reject candidates that are vague, duplicates of verified knowledge, miscategorized, or trivial.\n"
        "Use the usage / outcome counters as soft evidence: high usage with positive outcomes leans promote;\n"
        "consistent negative outcomes lean reject.\n"
        "Return ONLY strict JSON of the shape:\n"
        '{ "verdicts": [{ "id": "<candidate id>", "verdict": "promote|reject|hold", "reason": "<short>" }, ...] }\n'
        "One verdict per candidate, preserving the input ids. Reasons must be one short sentence."
    )
    user = json.dumps(blob, ensure_ascii=False)
    response = await llm.complete([Message(role="user", content=user)], system=system, temperature=0.1)
    raw = response.text
    data = parse_json_blob(raw)
    if data is None:
        return {"ok": True, "verdicts": []}

    verdicts_raw = data.get("verdicts") if isinstance(data, dict) else None
    if not isinstance(verdicts_raw, list):
        return {"ok": True, "verdicts": []}

    valid = {"promote", "reject", "hold"}
    out: list[dict[str, str]] = []
    for v in verdicts_raw:
        if not isinstance(v, dict):
            continue
        vid = (v.get("id") or "").strip()
        verdict = (v.get("verdict") or "hold").strip().lower()
        if verdict not in valid:
            verdict = "hold"
        reason = (v.get("reason") or "").strip()[:300]
        if not vid:
            continue
        out.append({"id": vid, "verdict": verdict, "reason": reason})

    return {"ok": True, "verdicts": out}


@router.post("/projects/{project_id}/knowledge/{kind}/{item_id}/publish")
async def publish_knowledge_card(
    project_id: str,
    kind: str,
    item_id: str,
    payload: dict[str, Any],
    request: Request,
):
    svc = _card_service(request)
    result = await asyncio.to_thread(svc.publish_from_project_item, project_id, item_id, kind, payload)
    return {"ok": True, **result}


@router.post("/projects/{project_id}/turns/{turn_id}/publish-card")
async def publish_knowledge_card_from_turn(
    project_id: str,
    turn_id: str,
    payload: dict[str, Any],
    request: Request,
):
    svc = _card_service(request)
    try:
        result = await asyncio.to_thread(svc.publish_from_turn, project_id, turn_id, payload)
    except ValueError as exc:
        msg = str(exc)
        if msg == "Source turn not found":
            raise HTTPException(status_code=404, detail=msg) from exc
        raise HTTPException(status_code=400, detail=msg) from exc
    return {"ok": True, **result}


@router.get("/knowledge-cards")
async def list_my_knowledge_cards(request: Request):
    svc = _card_service(request)
    cards = await asyncio.to_thread(svc.list_my_cards)
    return {"ok": True, "cards": cards}


@router.get("/knowledge-cards/public")
async def list_public_knowledge_cards(
    request: Request,
    q: str = "",
    tags: str = "",
    limit: int = 20,
    offset: int = 0,
):
    svc = KnowledgeCardService(user_id="anonymous")
    cards = await asyncio.to_thread(
        svc.list_public_cards,
        query=q, tags=tags.split(",") if tags else [], limit=limit, offset=offset
    )
    return {"ok": True, "cards": cards}


@router.get("/knowledge-cards/friends")
async def list_friend_knowledge_cards(
    request: Request,
    q: str = "",
    tags: str = "",
    limit: int = 20,
    offset: int = 0,
):
    svc = _card_service(request)
    try:
        cards = await asyncio.to_thread(
            svc.list_friend_cards,
            query=q, tags=tags.split(",") if tags else [], limit=limit, offset=offset
        )
    except HTTPException:
        raise
    except Exception as exc:
        from math_agent.knowledge.supabase_client import is_transient_supabase_error

        if is_transient_supabase_error(exc):
            raise HTTPException(
                status_code=503,
                detail="服务暂时遇到问题，请稍后重试。",
            ) from exc
        raise
    return {"ok": True, "cards": cards}


@router.get("/knowledge-cards/{card_id}")
async def get_knowledge_card(card_id: str, request: Request):
    user = optional_auth_user(request)
    svc = KnowledgeCardService(user_id=user.user_id if user else "anonymous")
    card = await asyncio.to_thread(svc.get_card, card_id)
    if card is None:
        raise HTTPException(status_code=404, detail="Card not found.")
    return {"ok": True, "card": card}


@router.get("/knowledge-cards/{card_id}/export/{format}")
async def export_knowledge_card(card_id: str, format: str, request: Request):
    user = optional_auth_user(request)
    svc = KnowledgeCardService(user_id=user.user_id if user else "anonymous")
    try:
        content = await asyncio.to_thread(svc.export_card, card_id, format)
    except ValueError as exc:
        msg = str(exc)
        if msg == "Card not found":
            raise HTTPException(status_code=404, detail=msg) from exc
        if msg.startswith("Unsupported export format"):
            raise HTTPException(status_code=400, detail=msg) from exc
        raise
    return {"ok": True, "format": format, "content": content}


@router.post("/knowledge-cards/{card_id}/import")
async def import_knowledge_card(card_id: str, payload: dict[str, Any], request: Request):
    target_project_id = str(payload.get("target_project_id") or "").strip()
    if not target_project_id:
        raise HTTPException(status_code=400, detail="target_project_id is required.")
    svc = _card_service(request)
    try:
        result = await svc.import_card_into_project(card_id, target_project_id)
    except ValueError as exc:
        msg = str(exc)
        if msg in {"Card not found", "Target project not found"}:
            raise HTTPException(status_code=404, detail=msg) from exc
        raise
    return {"ok": True, **result}


@router.post("/knowledge-cards/{card_id}/revisions")
async def create_knowledge_card_revision(
    card_id: str, payload: dict[str, Any], request: Request
):
    svc = _card_service(request)
    try:
        result = await asyncio.to_thread(svc.create_revision, card_id, payload)
    except ValueError as exc:
        msg = str(exc)
        if msg == "Card not found":
            raise HTTPException(status_code=404, detail=msg) from exc
        if msg == "Not authorized to edit this card":
            raise HTTPException(status_code=403, detail=msg) from exc
        raise HTTPException(status_code=400, detail=msg) from exc
    return {"ok": True, **result}


@router.post("/knowledge-cards/{card_id}/publish")
async def publish_knowledge_card_visibility(
    card_id: str, payload: dict[str, Any], request: Request
):
    svc = _card_service(request)
    visibility = str(payload.get("visibility") or "").strip()
    try:
        result = await asyncio.to_thread(svc.publish_card, card_id, visibility)
    except ValueError as exc:
        msg = str(exc)
        if msg == "Card not found":
            raise HTTPException(status_code=404, detail=msg) from exc
        if msg == "Not authorized to publish this card":
            raise HTTPException(status_code=403, detail=msg) from exc
        raise HTTPException(status_code=400, detail=msg) from exc
    return {"ok": True, **result}


@router.post("/knowledge-cards/{card_id}/reactions")
async def react_to_card(card_id: str, payload: dict[str, Any], request: Request):
    raise HTTPException(status_code=501, detail="Card reactions are not implemented.")


@router.get("/knowledge-cards/{card_id}/comments")
async def list_card_comments(card_id: str, request: Request):
    raise HTTPException(status_code=501, detail="Card comments are not implemented.")


@router.post("/knowledge-cards/{card_id}/comments")
async def add_card_comment(card_id: str, payload: dict[str, Any], request: Request):
    raise HTTPException(status_code=501, detail="Card comments are not implemented.")
