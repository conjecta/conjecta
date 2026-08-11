from __future__ import annotations

from datetime import datetime, timezone

import pytest

from math_agent.billing.models import LLMResponse
from math_agent.math_news.sources import RawNewsItem
from math_agent.math_news.translation import translate_news_item


class FakeLLM:
    def __init__(self, response_text: str) -> None:
        self.response_text = response_text
        self.calls: list[tuple] = []

    async def complete(self, messages, system=None, temperature=None):
        self.calls.append((messages, system, temperature))
        return LLMResponse(text=self.response_text, prompt_tokens=0, completion_tokens=0, total_tokens=0)


@pytest.mark.asyncio
async def test_translate_returns_parsed_json():
    llm = FakeLLM('{"title_zh": "新证明", "summary_zh": "数论中的惊人结果。"}')
    item = RawNewsItem("quanta", "A New Proof", "A surprising result.", "https://x", datetime.now(timezone.utc))
    result = await translate_news_item(llm, item)
    assert result["title_zh"] == "新证明"
    assert result["summary_zh"] == "数论中的惊人结果。"


@pytest.mark.asyncio
async def test_translate_falls_back_to_original_on_bad_json():
    llm = FakeLLM("not json")
    item = RawNewsItem("quanta", "A New Proof", "A surprising result.", "https://x", datetime.now(timezone.utc))
    result = await translate_news_item(llm, item)
    assert result["title_zh"] == "A New Proof"
    assert result["summary_zh"].startswith("A surprising result")
