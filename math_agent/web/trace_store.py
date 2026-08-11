"""Per-session solve trace persistence.

Streams the NDJSON solve events to ``{user_root}/traces/{session_id}.jsonl``
so a client that reconnects (or returns after a transport detach) can replay
the intermediate reasoning steps, not just the final answer. Token-level and
heartbeat events are excluded; oversized string fields and total event count
are capped to bound disk usage.
"""
from __future__ import annotations

import json
import logging
import re
import threading
from copy import deepcopy
from pathlib import Path
from typing import Any

from math_agent.web.project_store import project_store_root_for_user

log = logging.getLogger("math_agent.web.trace_store")

TRACE_DIR_NAME = "traces"
TRACE_EXCLUDED_TYPES = {"token", "ping"}
MAX_EVENTS_PER_SESSION = 5000
_MAX_EVENT_BYTES = 8192
_MAX_STRING_FIELD_CHARS = 4000
SESSION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


def trace_path_for(user_id: str, session_id: str) -> Path:
    # Defensive: session ids from ``new_session_logger`` already match, but
    # reads can arrive as raw URL input — never let them escape the dir.
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", session_id)[:128] or "unknown"
    return project_store_root_for_user(user_id) / TRACE_DIR_NAME / f"{safe}.jsonl"


def _truncate_event(event: dict[str, Any]) -> dict[str, Any]:
    """Cap a single event's serialized size by truncating long string fields."""
    record = deepcopy(event)
    text = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
    if len(text.encode("utf-8")) <= _MAX_EVENT_BYTES:
        return record
    for key, value in record.items():
        if isinstance(value, str) and len(value) > _MAX_STRING_FIELD_CHARS:
            record[key] = value[:_MAX_STRING_FIELD_CHARS] + "…"
    return record


class TraceRecorder:
    """Append solve events to one session's JSONL trace file."""

    def __init__(self, user_id: str, session_id: str) -> None:
        self._path = trace_path_for(user_id, session_id)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._count = 0
        self._truncated = False
        self._closed = False
        try:
            self._handle = self._path.open("a", encoding="utf-8")
        except OSError as exc:
            log.warning("Trace recorder disabled for %s: %s", session_id, exc)
            self._handle = None
            self._closed = True

    def record(self, event: dict[str, Any]) -> None:
        if not isinstance(event, dict):
            return
        event_type = event.get("type")
        if event_type in TRACE_EXCLUDED_TYPES:
            return
        with self._lock:
            if self._closed or self._handle is None:
                return
            if self._count >= MAX_EVENTS_PER_SESSION:
                if not self._truncated:
                    self._truncated = True
                    self._write_line({"type": "trace_truncated"})
                return
            self._write_line(_truncate_event(event))
            self._count += 1

    def _write_line(self, record: dict[str, Any]) -> None:
        assert self._handle is not None
        try:
            self._handle.write(
                json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
            )
            self._handle.flush()
        except OSError as exc:
            log.warning("Trace write failed for %s: %s", self._path.name, exc)
            self.close()

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            if self._handle is not None:
                try:
                    self._handle.close()
                except OSError:
                    pass
                self._handle = None


def read_trace(user_id: str, session_id: str) -> list[dict[str, Any]]:
    """Return the persisted trace events for one session, oldest first."""
    path = trace_path_for(user_id, session_id)
    events: list[dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8") as handle:
            for raw in handle:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    event = json.loads(raw)
                except Exception:
                    continue
                if isinstance(event, dict):
                    events.append(event)
    except FileNotFoundError:
        return []
    except OSError as exc:
        log.warning("Trace read failed for %s: %s", session_id, exc)
    return events


def trace_exists(user_id: str, session_id: str) -> bool:
    return trace_path_for(user_id, session_id).is_file()
