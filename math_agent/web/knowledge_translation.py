"""Translation helpers for persisted bilingual project knowledge."""
from __future__ import annotations

import json
from typing import Any

from math_agent.llm.base import LLMBackend, Message
from math_agent.text_utils import parse_json_blob


TRANSLATION_FIELDS: dict[str, tuple[tuple[str, str], ...]] = {
    "fact": (("statement", "statement_zh"), ("why", "why_zh")),
    "intuition": (("title", "title_zh"), ("body", "body_zh")),
    "trick": (("title", "title_zh"), ("body", "body_zh")),
}


def is_primarily_english(text: str) -> bool:
    """Avoid model calls for Chinese or symbol-only mathematical content."""
    latin = sum(char.isascii() and char.isalpha() for char in text)
    cjk = sum("\u3400" <= char <= "\u9fff" for char in text)
    return latin >= 12 and latin > cjk * 2


def existing_translation(item: dict[str, Any], kind: str) -> dict[str, str] | None:
    pairs = TRANSLATION_FIELDS.get(kind)
    if not pairs:
        return None
    translated = {
        target: str(item.get(target) or "").strip()
        for source, target in pairs
        if str(item.get(source) or "").strip()
    }
    return translated if translated and all(translated.values()) else None


async def translate_knowledge_item(
    llm: LLMBackend,
    item: dict[str, Any],
    kind: str,
) -> dict[str, str]:
    pairs = TRANSLATION_FIELDS.get(kind)
    if not pairs:
        raise ValueError("Unsupported knowledge kind.")

    source = {
        field: str(item.get(field) or "").strip()
        for field, _target in pairs
        if str(item.get(field) or "").strip()
    }
    if not source:
        raise ValueError("Knowledge item has no translatable text.")
    if not is_primarily_english(" ".join(source.values())):
        raise ValueError("Knowledge item is not primarily English.")

    expected = {field: target for field, target in pairs if field in source}
    system = (
        "You translate mathematical research notes from English to Simplified Chinese. "
        "Preserve all LaTeX, symbols, identifiers, theorem numbers, citations, and logical meaning exactly. "
        "Use concise, natural Chinese mathematical terminology. Return only a strict JSON object whose keys "
        f"are exactly: {', '.join(expected.values())}. Do not add commentary."
    )
    response = await llm.complete(
        [Message(role="user", content=json.dumps(source, ensure_ascii=False))],
        system=system,
        temperature=0.0,
    )
    data = parse_json_blob(response.text)
    if not isinstance(data, dict):
        raise ValueError("Translation model returned invalid JSON.")

    translated = {
        target: str(data.get(target) or "").strip()
        for target in expected.values()
    }
    if not translated or not all(translated.values()):
        raise ValueError("Translation model returned incomplete fields.")
    return translated
