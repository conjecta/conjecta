"""Small helpers for knowledge-related API payloads."""
from __future__ import annotations

from typing import Any


def short_knowledge_texts(items: list[Any], limit: int = 40) -> list[str]:
    """Extract display strings from fact/intuition/trick rows or plain strings."""
    out: list[str] = []
    for it in items[:limit]:
        if isinstance(it, str):
            text = it.strip()
        elif isinstance(it, dict):
            text = (it.get("statement") or it.get("title") or it.get("body") or "").strip()
        else:
            text = ""
        if text:
            out.append(text)
    return out


def short_knowledge_rows(items: list[Any], limit: int = 30) -> list[dict[str, str]]:
    """Compact fact/intuition/trick rows for explore/review prompts."""
    out: list[dict[str, str]] = []
    for it in items[:limit]:
        if isinstance(it, str):
            text = it.strip()
            if text:
                out.append({"title": text, "body": ""})
        elif isinstance(it, dict):
            title = (it.get("statement") or it.get("title") or "").strip()
            body = (it.get("body") or it.get("why") or it.get("note") or "").strip()
            if title:
                out.append({"title": title[:240], "body": body[:280]})
    return out
