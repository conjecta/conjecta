from __future__ import annotations

import hashlib
import json
import logging
import os
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

log = logging.getLogger("math_agent.math_news.store")


@dataclass(frozen=True)
class MathNewsItem:
    id: str
    source: str
    title_zh: str
    summary_zh: str
    url: str
    published_at: datetime
    fetched_at: datetime

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["published_at"] = self.published_at.isoformat()
        data["fetched_at"] = self.fetched_at.isoformat()
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MathNewsItem":
        return cls(
            id=data["id"],
            source=data["source"],
            title_zh=data["title_zh"],
            summary_zh=data["summary_zh"],
            url=data["url"],
            published_at=datetime.fromisoformat(data["published_at"]),
            fetched_at=datetime.fromisoformat(data["fetched_at"]),
        )


def _normalize_url(url: str) -> str:
    from urllib.parse import urlparse, urlunparse
    parsed = urlparse(url.strip())
    netloc = parsed.netloc.lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]
    path = parsed.path.rstrip("/") or "/"
    return urlunparse((parsed.scheme.lower(), netloc, path, "", parsed.query, ""))


def stable_id(url: str) -> str:
    return hashlib.sha256(_normalize_url(url).encode("utf-8")).hexdigest()[:16]


class MathNewsStore:
    def __init__(self, path: Path, seed_path: Path | None = None) -> None:
        self.path = Path(path)
        self.seed_path = Path(seed_path) if seed_path else None

    def load(self) -> list[MathNewsItem]:
        for source in [self.path, self.seed_path]:
            if source is None or not source.exists():
                continue
            try:
                items: list[MathNewsItem] = []
                for line in source.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    items.append(MathNewsItem.from_dict(json.loads(line)))
                return items
            except Exception as exc:
                log.warning("Failed to load math news store from %s: %s", source, exc)
        return []

    def save(self, items: list[MathNewsItem]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8") as f:
            for item in items:
                f.write(json.dumps(item.to_dict(), ensure_ascii=False) + "\n")
        os.replace(tmp, self.path)

    def updated_at(self) -> datetime | None:
        items = self.load()
        if not items:
            return None
        return max(item.fetched_at for item in items)
