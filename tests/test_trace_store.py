"""Tests for per-session solve trace persistence (math_agent.web.trace_store)."""
from __future__ import annotations

import json

import pytest

import math_agent.web.trace_store as trace_store
from math_agent.web.trace_store import (
    TraceRecorder,
    read_trace,
    trace_exists,
    trace_path_for,
)


@pytest.fixture
def store_root(monkeypatch: pytest.MonkeyPatch, tmp_path):
    """Isolate the project-store root (traces live under it) into tmp_path."""
    monkeypatch.setenv("CONJECTA_PROJECT_STORE_DIR", str(tmp_path))
    return tmp_path


def test_record_read_roundtrip_preserves_order(store_root):
    recorder = TraceRecorder("user-1", "sess-1")
    events = [
        {"type": "session", "session_id": "sess-1"},
        {"type": "step", "step_num": 1, "content": "think"},
        {"type": "done", "final_answer": "42"},
    ]
    for event in events:
        recorder.record(event)
    recorder.close()

    assert trace_exists("user-1", "sess-1") is True
    assert read_trace("user-1", "sess-1") == events


def test_token_and_ping_events_are_dropped(store_root):
    recorder = TraceRecorder("user-1", "sess-2")
    recorder.record({"type": "session", "session_id": "sess-2"})
    recorder.record({"type": "token", "text": "a"})
    recorder.record({"type": "ping"})
    recorder.record({"type": "step", "step_num": 1})
    recorder.close()

    recorded = read_trace("user-1", "sess-2")
    assert [event["type"] for event in recorded] == ["session", "step"]


def test_oversized_event_has_long_string_fields_truncated(store_root):
    recorder = TraceRecorder("user-1", "sess-3")
    recorder.record({"type": "step", "content": "x" * 9000, "note": "short"})
    recorder.close()

    (event,) = read_trace("user-1", "sess-3")
    assert event["content"] == "x" * 4000 + "…"
    assert event["note"] == "short"


def test_event_count_cap_writes_trace_truncated_once(store_root, monkeypatch):
    monkeypatch.setattr(trace_store, "MAX_EVENTS_PER_SESSION", 3)
    recorder = TraceRecorder("user-1", "sess-4")
    for index in range(6):
        recorder.record({"type": "step", "step_num": index})
    recorder.close()

    recorded = read_trace("user-1", "sess-4")
    assert [event.get("step_num") for event in recorded[:3]] == [0, 1, 2]
    assert recorded[3] == {"type": "trace_truncated"}
    assert len(recorded) == 4


def test_close_is_idempotent_and_record_after_close_is_noop(store_root):
    recorder = TraceRecorder("user-1", "sess-5")
    recorder.record({"type": "step", "step_num": 1})
    recorder.close()
    recorder.close()
    recorder.record({"type": "step", "step_num": 2})

    recorded = read_trace("user-1", "sess-5")
    assert [event["step_num"] for event in recorded] == [1]


def test_read_trace_skips_corrupt_lines(store_root):
    path = trace_path_for("user-1", "sess-6")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        handle.write(json.dumps({"type": "step", "step_num": 1}) + "\n")
        handle.write("{not valid json\n")
        handle.write("\n")
        handle.write(json.dumps(["not", "a", "dict"]) + "\n")
        handle.write(json.dumps({"type": "done"}) + "\n")

    recorded = read_trace("user-1", "sess-6")
    assert recorded == [{"type": "step", "step_num": 1}, {"type": "done"}]


def test_read_trace_missing_file_returns_empty(store_root):
    assert read_trace("user-1", "sess-missing") == []
    assert trace_exists("user-1", "sess-missing") is False
