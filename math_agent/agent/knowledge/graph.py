from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Relation:
    id: str
    project_id: str
    from_id: str
    to_id: str
    relation: str
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "project_id": self.project_id,
            "from_id": self.from_id,
            "to_id": self.to_id,
            "relation": self.relation,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Relation:
        return cls(
            id=data["id"],
            project_id=data["project_id"],
            from_id=data["from_id"],
            to_id=data["to_id"],
            relation=data["relation"],
            created_at=data["created_at"],
        )


class KnowledgeGraph:
    """Local JSONL graph of relations between knowledge items."""

    VALID_RELATIONS = {"implies", "generalizes", "specializes", "uses", "related", "contradicts"}

    def __init__(self, root: str | Path = "data/knowledge_graphs") -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, project_id: str) -> Path:
        safe = "".join(c for c in project_id if c.isalnum() or c in "-_")
        return self.root / f"{safe}.jsonl"

    def _read_all(self, project_id: str) -> list[Relation]:
        path = self._path(project_id)
        if not path.exists():
            return []
        items: list[Relation] = []
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    items.append(Relation.from_dict(json.loads(line)))
                except (json.JSONDecodeError, KeyError):
                    continue
        return items

    def add_relation(
        self,
        from_id: str,
        to_id: str,
        relation: str,
        project_id: str,
    ) -> Relation:
        relation = relation.lower().strip()
        if relation not in self.VALID_RELATIONS:
            relation = "related"
        rel = Relation(
            id=uuid.uuid4().hex[:12],
            project_id=project_id,
            from_id=from_id,
            to_id=to_id,
            relation=relation,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        path = self._path(project_id)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rel.to_dict(), ensure_ascii=False) + "\n")
        return rel

    def get_related(self, item_id: str, project_id: str) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for rel in self._read_all(project_id):
            if rel.from_id == item_id or rel.to_id == item_id:
                results.append({
                    "relation_id": rel.id,
                    "from_id": rel.from_id,
                    "to_id": rel.to_id,
                    "relation": rel.relation,
                })
        return results
