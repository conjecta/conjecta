from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _safe_component(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]", "_", value).strip("._")
    return cleaned[:120] or "artifact"


# Statuses marking a research artifact as accepted evidence for the proof.
ACCEPTED_ARTIFACT_STATUSES = frozenset({"reviewed", "verified", "proved"})


@dataclass(frozen=True)
class ResearchArtifact:
    id: str
    goal_id: str
    goal_statement: str
    attempt_index: int
    strategy: str
    status: str
    answer: str = ""
    summary: str = ""
    verification_status: str = "best_effort"
    verification_issues: list[str] = field(default_factory=list)
    evidence_ids: list[str] = field(default_factory=list)
    trace_checkpoint: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    path: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ResearchArtifactStore:
    """Persist complete per-goal attempts outside the prompt context."""

    def __init__(self, root: str | Path, session_id: str) -> None:
        self.session_id = _safe_component(session_id)
        self.root = Path(root).resolve() / self.session_id
        self.root.mkdir(parents=True, exist_ok=True)

    def create(
        self,
        *,
        goal_id: str,
        goal_statement: str,
        attempt_index: int,
        strategy: str,
        status: str,
        answer: str,
        summary: str,
        verification_status: str,
        verification_issues: list[str],
        evidence_ids: list[str] | None = None,
        trace_checkpoint: dict[str, Any] | None = None,
    ) -> ResearchArtifact:
        digest = hashlib.sha256(
            f"{self.session_id}\0{goal_id}\0{attempt_index}\0{answer}".encode("utf-8")
        ).hexdigest()[:16]
        artifact_id = f"research-{digest}"
        path = self.root / f"{_safe_component(goal_id)}-{attempt_index}-{digest}.json"
        artifact = ResearchArtifact(
            id=artifact_id,
            goal_id=goal_id,
            goal_statement=goal_statement,
            attempt_index=attempt_index,
            strategy=strategy,
            status=status,
            answer=answer,
            summary=summary,
            verification_status=verification_status,
            verification_issues=list(verification_issues),
            evidence_ids=list(evidence_ids or []),
            trace_checkpoint=dict(trace_checkpoint or {}),
            path=str(path),
        )
        payload = json.dumps(artifact.to_dict(), ensure_ascii=False, indent=2)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(payload, encoding="utf-8")
        temporary.replace(path)
        return artifact

    @staticmethod
    def read(path: str | Path) -> dict[str, Any] | None:
        """Load a persisted artifact payload, or None when unreadable."""
        try:
            return json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    @staticmethod
    def set_status(path: str | Path, status: str) -> bool:
        """Update the status of a persisted artifact so disk matches memory."""
        target = Path(path)
        try:
            payload = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return False
        payload["status"] = status
        serialized = json.dumps(payload, ensure_ascii=False, indent=2)
        temporary = target.with_suffix(".tmp")
        temporary.write_text(serialized, encoding="utf-8")
        temporary.replace(target)
        return True
