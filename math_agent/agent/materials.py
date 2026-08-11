from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Material:
    id: str
    project_id: str
    kind: str
    label: str
    text: str
    source: str
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "project_id": self.project_id,
            "kind": self.kind,
            "label": self.label,
            "text": self.text,
            "source": self.source,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Material:
        return cls(
            id=data["id"],
            project_id=data["project_id"],
            kind=data["kind"],
            label=data["label"],
            text=data["text"],
            source=data["source"],
            created_at=data["created_at"],
        )


class MaterialStore:
    """Append-only JSONL store of raw source materials per project."""

    def __init__(self, root: str | Path = "data/materials") -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, project_id: str) -> Path:
        safe = "".join(c for c in project_id if c.isalnum() or c in "-_")
        return self.root / f"{safe}.jsonl"

    def _read_all(self, project_id: str) -> list[Material]:
        path = self._path(project_id)
        if not path.exists():
            return []
        items: list[Material] = []
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    items.append(Material.from_dict(json.loads(line)))
                except (json.JSONDecodeError, KeyError):
                    continue
        return items

    def add(
        self,
        project_id: str,
        kind: str,
        label: str,
        text: str,
        source: str,
    ) -> Material:
        material = Material(
            id=uuid.uuid4().hex[:12],
            project_id=project_id,
            kind=kind,
            label=label,
            text=text,
            source=source,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        path = self._path(project_id)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(material.to_dict(), ensure_ascii=False) + "\n")
        return material

    def list(self, project_id: str, *, limit: int = 100) -> list[Material]:
        return self._read_all(project_id)[:limit]

    def search(self, project_id: str, query: str, *, limit: int = 20) -> list[Material]:
        q = query.lower()
        results = [
            m for m in self._read_all(project_id)
            if q in m.text.lower() or q in m.label.lower()
        ]
        return results[:limit]
