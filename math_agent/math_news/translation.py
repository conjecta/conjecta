from __future__ import annotations

import json
import logging

from math_agent.llm.base import LLMBackend, Message
from math_agent.math_news.sources import RawNewsItem
from math_agent.text_utils import parse_json_blob

log = logging.getLogger("math_agent.math_news.translation")

_SYSTEM_PROMPT = (
    "Translate the following math news title and summary into Simplified Chinese. "
    "Preserve LaTeX, symbols, names, theorem numbers, and citations exactly. "
    "Return only a strict JSON object with keys title_zh and summary_zh. "
    "Title should be at most 40 Chinese characters; summary at most 80."
)


def _clip(text: str, max_chars: int) -> str:
    text = text.strip()
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1] + "…"


async def translate_news_item(llm: LLMBackend, item: RawNewsItem) -> dict[str, str]:
    payload = {"title": item.title, "summary": item.summary}
    try:
        response = await llm.complete(
            [Message(role="user", content=json.dumps(payload, ensure_ascii=False))],
            system=_SYSTEM_PROMPT,
            temperature=0.0,
        )
        data = parse_json_blob(response.text)
        if not isinstance(data, dict):
            raise ValueError("Model returned non-JSON.")
        title_zh = str(data.get("title_zh") or "").strip()
        summary_zh = str(data.get("summary_zh") or "").strip()
        if not title_zh or not summary_zh:
            raise ValueError("Model returned empty translation.")
        return {
            "title_zh": _clip(title_zh, 40),
            "summary_zh": _clip(summary_zh, 80),
        }
    except Exception as exc:
        log.warning("Translation failed for %s: %s", item.url, exc)
        return {
            "title_zh": item.title,
            "summary_zh": _clip(item.summary, 120),
        }
