import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import math_agent.web.user_memory_store as user_memory_store

from math_agent.web.user_memory_store import (
    MemoryScope,
    MemoryStatus,
    UserMemory,
    UserMemoryEntryKind,
    UserMemoryStore,
    UserProfileSummary,
)


def test_user_memory_defaults():
    mem = UserMemory(content="prefer Chinese")
    assert mem.kind == UserMemoryEntryKind.PREFERENCE
    assert mem.status == MemoryStatus.CANDIDATE
    assert mem.scope == MemoryScope.GLOBAL
    assert mem.weight == 0.5
    assert mem.content == "prefer Chinese"


def test_profile_summary_defaults():
    profile = UserProfileSummary(user_id="u-1", summary="likes short answers")
    assert profile.version == 1
    assert profile.user_id == "u-1"


def test_user_memory_round_trip():
    mem = UserMemory(
        content="prefer Chinese",
        kind=UserMemoryEntryKind.PREFERENCE,
        scope=MemoryScope.project("p1"),
        weight=0.9,
    )
    restored = UserMemory.from_dict(mem.to_dict())
    assert restored.content == mem.content
    assert restored.kind == mem.kind
    assert restored.scope == mem.scope
    assert restored.weight == mem.weight
    assert isinstance(restored.scope, MemoryScope)


def test_user_memory_normalization():
    mem = UserMemory(content="  spaced  ", why="  why  ", weight=1.5)
    assert mem.content == "spaced"
    assert mem.why == "why"
    assert mem.weight == 1.0
    assert mem.id.startswith("um-")


def test_profile_summary_round_trip():
    profile = UserProfileSummary(user_id="u-1", summary="short", source_memory_ids=["a", "b"])
    restored = UserProfileSummary.from_dict(profile.to_dict())
    assert restored.summary == profile.summary
    assert restored.source_memory_ids == profile.source_memory_ids
    assert restored.version == profile.version


def test_store_crud_and_search():
    with tempfile.TemporaryDirectory() as tmp:
        store = UserMemoryStore(user_id="u-1", root=Path(tmp))
        mem = store.add(
            content="用中文回答",
            kind=UserMemoryEntryKind.PREFERENCE,
            why="user asked in Chinese",
            weight=0.9,
        )
        assert mem.status == MemoryStatus.ACTIVE
        found = store.search("中文")
        assert len(found) == 1
        assert found[0].content == "用中文回答"

        store.update(mem.id, {"status": MemoryStatus.SNOOZED.value})
        listed = store.list(status=MemoryStatus.ACTIVE)
        assert len(listed) == 0

        store.delete(mem.id)
        assert len(store.list()) == 0
        rejected = store.list_rejected()
        assert len(rejected) == 1
        assert rejected[0].tombstone is True


def test_profile_summary_versioning():
    with tempfile.TemporaryDirectory() as tmp:
        store = UserMemoryStore(user_id="u-1", root=Path(tmp))
        store.save_profile("summary v1")
        store.save_profile("summary v2")
        current = store.get_profile()
        assert current.summary == "summary v2"
        assert current.version == 2

        store.rollback_profile(1)
        rolled = store.get_profile()
        assert rolled.summary == "summary v1"
        assert rolled.version == 3

        cleared = store.clear_profile()
        assert cleared.summary == ""
        assert cleared.version == 4
        assert store.get_profile().summary == ""


def test_search_preserves_relevance_ranking(tmp_path, monkeypatch):
    store = UserMemoryStore(user_id="u-1", root=tmp_path)
    first = store.add(content="first result")
    second = store.add(content="second result")

    monkeypatch.setattr(
        user_memory_store,
        "rank_rows",
        lambda *_args, **_kwargs: [second.to_dict(), first.to_dict()],
    )

    assert [memory.id for memory in store.search("result")] == [second.id, first.id]


def test_list_supports_zero_and_unbounded_limits(tmp_path):
    store = UserMemoryStore(user_id="u-1", root=tmp_path)
    store.add(content="one")
    store.add(content="two")

    assert store.list(limit=0) == []
    assert len(store.list(limit=None)) == 2


def test_update_cannot_change_memory_ownership_or_provenance(tmp_path):
    store = UserMemoryStore(user_id="u-1", root=tmp_path)
    memory = store.add(content="original", source_session_id="session-1")

    updated = store.update(
        memory.id,
        {"content": "updated", "user_id": "u-2", "source_session_id": "session-2"},
    )

    assert updated is not None
    assert updated.content == "updated"
    assert updated.user_id == "u-1"
    assert updated.source_session_id == "session-1"


def test_expiration_uses_timezone_aware_comparison(tmp_path):
    store = UserMemoryStore(user_id="u-1", root=tmp_path)
    past_z = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat().replace(
        "+00:00", "Z"
    )
    future_offset = (
        datetime.now(timezone.utc) + timedelta(minutes=1)
    ).astimezone(timezone(timedelta(hours=8))).isoformat()
    store.add(content="expired", expires_at=past_z)
    store.add(content="future", expires_at=future_offset)
    store.add(content="invalid", expires_at="not-a-date")

    assert [memory.content for memory in store.list()] == ["future"]
