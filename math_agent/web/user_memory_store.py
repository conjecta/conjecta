from __future__ import annotations

import enum
import json
import logging
import re
import threading
import uuid
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from math_agent.search.text_retrieval import rank_rows


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_unexpired(expires_at: str | None, now: datetime) -> bool:
    if expires_at is None:
        return True
    try:
        parsed = datetime.fromisoformat(str(expires_at).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc) > now
    except (TypeError, ValueError):
        return False


class UserMemoryEntryKind(str, enum.Enum):
    PREFERENCE = "preference"
    TECHNIQUE = "technique"
    CORRECTION = "correction"
    CONTEXT = "context"


class MemoryStatus(str, enum.Enum):
    CANDIDATE = "candidate"
    ACTIVE = "active"
    SNOOZED = "snoozed"
    REJECTED = "rejected"


class MemoryScope(str):
    """String-like scope values; subclass of str so dynamic project scopes work."""

    @classmethod
    def project(cls, project_id: str) -> MemoryScope:
        return cls(f"project:{project_id}")


MemoryScope.GLOBAL = MemoryScope("global")


@dataclass
class UserMemory:
    content: str
    id: str = ""
    user_id: str = ""
    kind: UserMemoryEntryKind = UserMemoryEntryKind.PREFERENCE
    why: str = ""
    weight: float = 0.5
    status: MemoryStatus = MemoryStatus.CANDIDATE
    scope: MemoryScope = MemoryScope.GLOBAL
    source_session_id: str = ""
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)
    expires_at: str | None = None
    tombstone: bool = False

    def __post_init__(self) -> None:
        if not self.id:
            self.id = f"um-{uuid.uuid4().hex}"
        self.content = (self.content or "").strip()[:300]
        self.why = (self.why or "").strip()[:200]
        self.weight = max(0.0, min(1.0, float(self.weight)))
        self.tombstone = bool(self.tombstone)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "user_id": self.user_id,
            "kind": self.kind.value,
            "content": self.content,
            "why": self.why,
            "weight": self.weight,
            "status": self.status.value,
            "scope": self.scope,
            "source_session_id": self.source_session_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "expires_at": self.expires_at,
            "tombstone": self.tombstone,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> UserMemory:
        return cls(
            id=str(data.get("id") or ""),
            user_id=str(data.get("user_id") or ""),
            kind=UserMemoryEntryKind(str(data.get("kind") or "preference")),
            content=str(data.get("content") or ""),
            why=str(data.get("why") or ""),
            weight=float(data.get("weight", 0.5)),
            status=MemoryStatus(str(data.get("status") or "candidate")),
            scope=MemoryScope(str(data.get("scope") or "global")),
            source_session_id=str(data.get("source_session_id") or ""),
            created_at=str(data.get("created_at") or _now()),
            updated_at=str(data.get("updated_at") or _now()),
            expires_at=data.get("expires_at"),
            tombstone=bool(data.get("tombstone", False)),
        )


@dataclass
class UserProfileSummary:
    user_id: str
    summary: str
    version: int = 1
    generated_at: str = field(default_factory=_now)
    source_memory_ids: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.summary = (self.summary or "").strip()[:500]
        self.version = max(1, int(self.version))
        self.source_memory_ids = list(self.source_memory_ids or [])

    def to_dict(self) -> dict[str, Any]:
        return {
            "user_id": self.user_id,
            "summary": self.summary,
            "version": self.version,
            "generated_at": self.generated_at,
            "source_memory_ids": self.source_memory_ids,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> UserProfileSummary:
        return cls(
            user_id=str(data.get("user_id") or ""),
            summary=str(data.get("summary") or ""),
            version=int(data.get("version", 1)),
            generated_at=str(data.get("generated_at") or _now()),
            source_memory_ids=list(data.get("source_memory_ids") or []),
        )


log = logging.getLogger("math_agent.web.user_memory_store")

_USER_ID_RE = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")
_WRITE_LOCK = threading.RLock()
_MUTABLE_MEMORY_FIELDS = frozenset(
    {"content", "kind", "why", "weight", "status", "scope", "expires_at"}
)


def _default_root() -> Path:
    return Path("logs/users").resolve()


def _memory_root(root: Path | None, user_id: str) -> Path:
    from math_agent.web.project_store import sanitize_user_id

    base = (root or _default_root()).resolve()
    return base / sanitize_user_id(user_id)


class UserMemoryStore:
    """Append-only JSONL store for per-user memories and profile summaries."""

    def __init__(self, user_id: str, root: Path | None = None) -> None:
        if not _USER_ID_RE.match(user_id or ""):
            raise ValueError("Invalid user_id for memory store")
        self.user_id = user_id
        self.root = _memory_root(root, user_id)
        self.root.mkdir(parents=True, exist_ok=True)
        self._memory_log = self.root / "user_memory.jsonl"
        self._profile_log = self.root / "user_profile.jsonl"
        self._lock = _WRITE_LOCK
        self._memories_loaded = False
        self._profiles_loaded = False
        self._memories: list[UserMemory] = []
        self._profiles: list[UserProfileSummary] = []
        self._memory_signature: tuple[int, int, int, int] | None = None
        self._profile_signature: tuple[int, int, int, int] | None = None
        self._ensure_indexes()

    @staticmethod
    def _file_signature(path: Path) -> tuple[int, int, int, int] | None:
        try:
            stat = path.stat()
        except OSError:
            return None
        return (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns)

    def _ensure_indexes(self) -> None:
        self._ensure_memory_index()
        self._ensure_profile_index()

    def _ensure_memory_index(self) -> None:
        with self._lock:
            mem_sig = self._file_signature(self._memory_log)
            if self._memories_loaded and mem_sig == self._memory_signature:
                return
            memories: dict[str, UserMemory] = {}
            if mem_sig is not None:
                with self._memory_log.open("r", encoding="utf-8") as fh:
                    for line in fh:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            data = json.loads(line)
                            event_type = data.get("type")
                            if event_type == "memory":
                                item = data.get("item")
                                if isinstance(item, dict):
                                    mem = UserMemory.from_dict(item)
                                    memories[mem.id] = mem
                            elif event_type == "delete":
                                item = data.get("item") or {}
                                deleted_id = str(item.get("id") or "")
                                if deleted_id:
                                    memories.pop(deleted_id, None)
                        except Exception:
                            continue
            self._memories = list(memories.values())
            self._memory_signature = mem_sig
            self._memories_loaded = True

    def _ensure_profile_index(self) -> None:
        with self._lock:
            prof_sig = self._file_signature(self._profile_log)
            if self._profiles_loaded and prof_sig == self._profile_signature:
                return
            profiles: list[UserProfileSummary] = []
            if prof_sig is not None:
                with self._profile_log.open("r", encoding="utf-8") as fh:
                    for line in fh:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            data = json.loads(line)
                            if data.get("type") == "profile":
                                item = data.get("item")
                                if isinstance(item, dict):
                                    profiles.append(UserProfileSummary.from_dict(item))
                        except Exception:
                            continue
            self._profiles = profiles
            self._profile_signature = prof_sig
            self._profiles_loaded = True

    def _append(self, path: Path, record_type: str, item: Any) -> None:
        record = {"type": record_type, "at": _now(), "item": item.to_dict()}
        text = json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
        with self._lock:
            self._ensure_indexes()
            with path.open("a", encoding="utf-8") as fh:
                fh.write(text)
            if record_type == "memory":
                self._memory_signature = self._file_signature(path)
            else:
                self._profile_signature = self._file_signature(path)

    def add(
        self,
        content: str,
        kind: UserMemoryEntryKind = UserMemoryEntryKind.PREFERENCE,
        why: str = "",
        weight: float = 0.5,
        status: MemoryStatus = MemoryStatus.ACTIVE,
        scope: MemoryScope = MemoryScope.GLOBAL,
        source_session_id: str = "",
        expires_at: str | None = None,
    ) -> UserMemory:
        if not (content or "").strip():
            raise ValueError("content is required")
        mem = UserMemory(
            content=content,
            user_id=self.user_id,
            kind=kind,
            why=why,
            weight=weight,
            status=status,
            scope=scope,
            source_session_id=source_session_id,
            expires_at=expires_at,
        )
        with self._lock:
            self._ensure_indexes()
            self._memories.append(mem)
            self._append(self._memory_log, "memory", mem)
        return mem

    def list(
        self,
        *,
        kind: UserMemoryEntryKind | None = None,
        status: MemoryStatus | None = None,
        scope: MemoryScope | None = None,
        limit: int | None = 200,
        offset: int = 0,
    ) -> list[UserMemory]:
        with self._lock:
            self._ensure_indexes()
            rows = list(self._memories)
        if kind is not None:
            rows = [m for m in rows if m.kind == kind]
        if status is not None:
            rows = [m for m in rows if m.status == status]
        if scope is not None:
            rows = [m for m in rows if m.scope == scope]
        now = datetime.now(timezone.utc)
        rows = [m for m in rows if _is_unexpired(m.expires_at, now)]
        if status is None:
            rows = [m for m in rows if not m.tombstone]
        rows.reverse()
        start = max(0, int(offset))
        if limit is None:
            return rows[start:]
        bounded_limit = max(0, min(int(limit), 1000))
        end = start + bounded_limit
        return rows[start:end]

    def search(self, query: str, *, limit: int = 20) -> list[UserMemory]:
        query = (query or "").strip()
        if not query:
            return []
        active = self.list(status=MemoryStatus.ACTIVE, limit=None)
        rows = [m.to_dict() for m in active]
        ranked = rank_rows(rows, query, ["content", "why"], limit=max(limit, 20))
        by_id = {m.id: m for m in active}
        ranked_ids = [str(row.get("id") or "") for row in ranked]
        return [by_id[memory_id] for memory_id in ranked_ids if memory_id in by_id][
            : max(0, int(limit))
        ]

    def update(self, memory_id: str, fields: dict[str, Any]) -> UserMemory | None:
        with self._lock:
            self._ensure_indexes()
            for idx, mem in enumerate(self._memories):
                if mem.id == memory_id:
                    data = mem.to_dict()
                    data.update(
                        {
                            key: value
                            for key, value in fields.items()
                            if key in _MUTABLE_MEMORY_FIELDS
                        }
                    )
                    data["updated_at"] = _now()
                    updated = UserMemory.from_dict(data)
                    self._memories[idx] = updated
                    self._append(self._memory_log, "memory", updated)
                    return updated
        return None

    def delete(self, memory_id: str) -> bool:
        with self._lock:
            self._ensure_indexes()
            target = next((m for m in self._memories if m.id == memory_id), None)
            if target is None:
                return False
            tombstone = UserMemory.from_dict({
                **target.to_dict(),
                "status": MemoryStatus.REJECTED.value,
                "tombstone": True,
                "updated_at": _now(),
            })
            self._memories = [m for m in self._memories if m.id != memory_id]
            self._memories.append(tombstone)
            self._append(self._memory_log, "memory", tombstone)
        return True

    def list_rejected(self, *, limit: int | None = 200) -> list[UserMemory]:
        with self._lock:
            self._ensure_indexes()
            rows = [m for m in self._memories if m.tombstone or m.status == MemoryStatus.REJECTED]
            rows.reverse()
            if limit is None:
                return rows
            return rows[: max(0, int(limit))]

    def get_profile(self) -> UserProfileSummary | None:
        with self._lock:
            self._ensure_indexes()
            return deepcopy(self._profiles[-1]) if self._profiles else None

    def save_profile(self, summary: str, source_memory_ids: list[str] | None = None) -> UserProfileSummary:
        with self._lock:
            self._ensure_profile_index()
            version = (self._profiles[-1].version if self._profiles else 0) + 1
            profile = UserProfileSummary(
                user_id=self.user_id,
                summary=summary,
                version=version,
                source_memory_ids=source_memory_ids or [],
            )
            self._profiles.append(profile)
            self._append(self._profile_log, "profile", profile)
        return profile

    def clear_profile(self) -> UserProfileSummary:
        """Disable the current profile while preserving append-only history."""
        return self.save_profile("", source_memory_ids=[])

    def list_profile_versions(self) -> list[UserProfileSummary]:
        with self._lock:
            self._ensure_indexes()
            return deepcopy(self._profiles)

    def rollback_profile(self, target_version: int) -> UserProfileSummary | None:
        versions = self.list_profile_versions()
        target = next((p for p in versions if p.version == target_version), None)
        if target is None:
            return None
        return self.save_profile(target.summary, source_memory_ids=target.source_memory_ids)
