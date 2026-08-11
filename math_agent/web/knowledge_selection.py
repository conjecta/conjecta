from __future__ import annotations

import json
from typing import Any

from math_agent.knowledge.trust import KnowledgeTrustPolicy


SATISFACTION_ACTIONS_MARKER = "---ACTIONS---"
KNOWLEDGE_RESULT_MARKER = "---RESULT---"
TRUSTED_KNOWLEDGE_STATUSES = KnowledgeTrustPolicy.SOLVE_RETRIEVAL


def normalize_text_value(value: Any, *, limit: int | None = None) -> str:
    """Return a bounded text representation for untrusted JSON values."""
    if value is None:
        return ""
    try:
        if isinstance(value, str):
            text = value
        elif isinstance(value, (dict, list, tuple)):
            text = json.dumps(value, ensure_ascii=False, default=str)
        else:
            text = str(value)
    except Exception:
        return ""
    text = text.strip()
    return text[:limit] if limit is not None else text


def catalog_item(
    item: Any,
    *,
    id_key: str = "id",
    title_keys: tuple[str, ...],
    body_keys: tuple[str, ...],
) -> dict[str, str] | None:
    if not isinstance(item, dict):
        return None
    item_id = normalize_text_value(item.get(id_key))
    title = ""
    for key in title_keys:
        title = normalize_text_value(item.get(key), limit=320)
        if title:
            break
    if not item_id or not title:
        return None
    body = ""
    for key in body_keys:
        body = normalize_text_value(item.get(key), limit=400)
        if body:
            break
    return {"id": item_id, "title": title, "body": body}


def resolve_selected_ids(
    ids: Any,
    catalog: list[dict[str, str]],
    *,
    limit: int,
) -> list[dict[str, str]]:
    if not isinstance(ids, list) or not catalog:
        return []
    by_id = {item["id"]: item for item in catalog}
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for raw_id in ids:
        item_id = normalize_text_value(raw_id)
        if not item_id or item_id in seen:
            continue
        item = by_id.get(item_id)
        if not item:
            continue
        seen.add(item_id)
        out.append(item)
        if len(out) >= limit:
            break
    return out


def parse_json_blob(raw: str) -> dict[str, Any] | None:
    try:
        data = json.loads(raw)
    except Exception:
        start = raw.find("{")
        end = raw.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return None
        try:
            data = json.loads(raw[start:end + 1])
        except Exception:
            return None
    return data if isinstance(data, dict) else None


def parse_complete_json_object(raw: str) -> dict[str, Any] | None:
    """Parse the first complete JSON object, returning None while it is partial."""
    candidate = raw.lstrip()
    if not candidate:
        return None
    try:
        data, _ = json.JSONDecoder().raw_decode(candidate)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def normalize_conversation_history(conversation_history: Any) -> list[dict[str, str]]:
    history_blob: list[dict[str, str]] = []
    if not isinstance(conversation_history, list):
        return history_blob
    for turn in conversation_history[-12:]:
        if not isinstance(turn, dict):
            continue
        role = normalize_text_value(turn.get("role")).lower()
        text = normalize_text_value(turn.get("text"), limit=500)
        if not text:
            text = normalize_text_value(turn.get("content"), limit=500)
        if role in {"user", "agent", "assistant"} and text:
            history_blob.append({"role": role, "text": text})
    return history_blob


def build_knowledge_catalogs(
    facts_in: list[Any],
    intuitions_in: list[Any],
    tricks_in: list[Any],
) -> tuple[list[dict[str, str]], list[dict[str, str]], list[dict[str, str]]]:
    facts_in = _trusted_items(facts_in)
    intuitions_in = _trusted_items(intuitions_in)
    tricks_in = _trusted_items(tricks_in)
    fact_catalog: list[dict[str, str]] = []
    for item in facts_in[:50]:
        entry = catalog_item(item, title_keys=("statement", "title"), body_keys=("note", "why", "body"))
        if entry:
            fact_catalog.append(entry)

    intuition_catalog: list[dict[str, str]] = []
    for item in intuitions_in[:40]:
        entry = catalog_item(item, title_keys=("title",), body_keys=("body",))
        if entry:
            intuition_catalog.append(entry)

    trick_catalog: list[dict[str, str]] = []
    for item in tricks_in[:40]:
        entry = catalog_item(item, title_keys=("title",), body_keys=("body",))
        if entry:
            trick_catalog.append(entry)

    return fact_catalog, intuition_catalog, trick_catalog


def _trusted_items(items: list[Any]) -> list[Any]:
    if not isinstance(items, list):
        return []
    return [
        item
        for item in items
        if isinstance(item, dict)
        and normalize_text_value(item.get("status")).lower()
        in TRUSTED_KNOWLEDGE_STATUSES
    ]


def format_augmented_prompt(
    request_text: str,
    facts: list[dict[str, str]],
    intuitions: list[dict[str, str]],
    tricks: list[dict[str, str]],
) -> str:
    blocks: list[str] = []
    if facts or intuitions or tricks:
        blocks.append("=== Relevant Project Knowledge ===")
    if facts:
        blocks.extend(["", "[Facts]"])
        for idx, fact in enumerate(facts, 1):
            line = f"{fact['title']} — {fact['body']}" if fact.get("body") else fact["title"]
            blocks.append(f"{idx}. {line}")
    if intuitions:
        blocks.extend(["", "[Intuitions]"])
        for idx, item in enumerate(intuitions, 1):
            line = f"{item['title']}: {item['body']}" if item.get("body") else item["title"]
            blocks.append(f"{idx}. {line}")
    if tricks:
        blocks.extend(["", "[Techniques]"])
        for idx, item in enumerate(tricks, 1):
            line = f"{item['title']}: {item['body']}" if item.get("body") else item["title"]
            blocks.append(f"{idx}. {line}")
    blocks.extend(["", "=== Current Request ===", request_text])
    return "\n".join(blocks)


def extract_rephrased_request(augmented: str, fallback: str) -> str:
    marker = "=== Current Request ==="
    if marker not in augmented:
        return fallback
    rephrased = augmented.split(marker, 1)[1].strip()
    return rephrased or fallback


def selection_summary(
    facts: list[dict[str, str]],
    intuitions: list[dict[str, str]],
    tricks: list[dict[str, str]],
) -> str:
    parts: list[str] = []
    if facts:
        parts.append(f"{len(facts)} fact(s): " + "; ".join(item["title"][:80] for item in facts))
    if intuitions:
        parts.append(f"{len(intuitions)} intuition(s): " + "; ".join(item["title"][:80] for item in intuitions))
    if tricks:
        parts.append(f"{len(tricks)} technique(s): " + "; ".join(item["title"][:80] for item in tricks))
    return "\n".join(parts) if parts else "No relevant project knowledge selected."


def split_marker_response(raw: str, marker: str) -> tuple[str, dict[str, Any]]:
    if marker in raw:
        prose, _, json_part = raw.partition(marker)
        return prose.strip(), parse_json_blob(json_part.strip()) or {}
    return raw.strip(), {}


def split_satisfaction_response(raw: str) -> tuple[str, dict[str, Any]]:
    return split_marker_response(raw, SATISFACTION_ACTIONS_MARKER)
