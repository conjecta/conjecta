from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from math_agent.config import MathNewsConfig, LLMConfig
from math_agent.llm.base import LLMBackend
from math_agent.llm.factory import create_backend
from math_agent.math_news.sources import RawNewsItem, fetch_arxiv_news, fetch_quanta_news
from math_agent.math_news.store import MathNewsItem, MathNewsStore, stable_id
from math_agent.math_news.translation import translate_news_item

log = logging.getLogger("math_agent.math_news.refresh")


def _make_llm(config: MathNewsConfig) -> LLMBackend:
    """Build a cheap system LLM for news translation.

    DeepSeek goes through DeepSeekBackend directly so ``deepseek-chat`` is not
    remapped by ``normalize_model_string`` to thinking-enabled deepseek-v4-pro.
    """
    if config.provider == "deepseek":
        from math_agent.llm.deepseek import DeepSeekBackend

        return DeepSeekBackend(model=config.model, default_temperature=0.2)
    return create_backend(
        LLMConfig(
            provider=config.provider,
            model=config.model,
            temperature=0.2,
            timeout_seconds=60.0,
        )
    )


def select_news(quanta: list[RawNewsItem], arxiv: list[RawNewsItem], *, limit: int = 5) -> list[RawNewsItem]:
    seen: set[str] = set()
    unique: list[RawNewsItem] = []
    for item in quanta + arxiv:
        sid = stable_id(item.url)
        if sid in seen:
            continue
        seen.add(sid)
        unique.append(item)

    quanta_items = sorted(
        [i for i in unique if i.source == "quanta"],
        key=lambda i: i.published_at,
        reverse=True,
    )
    arxiv_items = sorted(
        [i for i in unique if i.source == "arxiv"],
        key=lambda i: i.published_at,
        reverse=True,
    )

    # Prefer ~2:3 Quanta:arXiv mix, scaled to `limit` (5 → 2+3, 10 → 4+6).
    q_ideal = (limit * 2) // 5
    a_ideal = limit - q_ideal
    q_target = min(q_ideal, len(quanta_items))
    a_target = min(a_ideal, len(arxiv_items))
    if q_target < q_ideal:
        a_target = min(limit - q_target, len(arxiv_items))
    if a_target < a_ideal:
        q_target = min(limit - a_target, len(quanta_items))

    # Keep Quanta items ahead of arXiv items rather than re-sorting by date;
    # final display ordering is applied later in refresh_math_news.
    selected = quanta_items[:q_target] + arxiv_items[:a_target]
    return selected[:limit]


async def _translate_items(items: list[RawNewsItem], llm: LLMBackend) -> list[MathNewsItem]:
    now = datetime.now(timezone.utc)

    async def _one(item: RawNewsItem) -> MathNewsItem:
        translated = await translate_news_item(llm, item)
        return MathNewsItem(
            id=stable_id(item.url),
            source=item.source,
            title_zh=translated["title_zh"],
            summary_zh=translated["summary_zh"],
            url=item.url,
            published_at=item.published_at,
            fetched_at=now,
        )

    return list(await asyncio.gather(*[_one(item) for item in items]))


async def refresh_math_news(
    store: MathNewsStore,
    config: MathNewsConfig,
    llm: LLMBackend | None = None,
) -> list[MathNewsItem]:
    now = datetime.now(timezone.utc)
    current = store.load()
    if current:
        last_fetched = max(item.fetched_at for item in current)
        if (now - last_fetched).total_seconds() < config.min_interval_seconds:
            log.info("Skipping math news refresh; last fetch was %s", last_fetched)
            return current

    try:
        quanta, arxiv = await asyncio.gather(fetch_quanta_news(), fetch_arxiv_news())
    except Exception as exc:
        log.warning("Math news fetch failed; keeping existing store: %s", exc)
        return current

    selected = select_news(quanta, arxiv, limit=10)
    if not selected:
        log.warning("No math news fetched; keeping existing store")
        return current

    if llm is None:
        llm = _make_llm(config)

    translated = await _translate_items(selected, llm)
    # Merge with current so a single weak source cycle does not shrink the buffer.
    by_id = {item.id: item for item in current}
    for item in translated:
        by_id[item.id] = item
    merged = sorted(by_id.values(), key=lambda i: i.published_at, reverse=True)[:10]
    store.save(merged)
    return merged


class MathNewsRefresher:
    def __init__(self, config: MathNewsConfig, store: MathNewsStore) -> None:
        self.config = config
        self.store = store
        self._task: asyncio.Task[Any] | None = None

    async def _loop(self) -> None:
        while True:
            try:
                await refresh_math_news(self.store, self.config)
            except Exception as exc:
                log.warning("Math news refresh loop error: %s", exc)
            await asyncio.sleep(self.config.refresh_seconds)

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
