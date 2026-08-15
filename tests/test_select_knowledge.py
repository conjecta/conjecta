"""Tests for knowledge-selection helpers (no live LLM calls)."""

from __future__ import annotations

import pytest

from math_agent.billing.models import LLMResponse
from math_agent.web.knowledge_selection import (
    SATISFACTION_ACTIONS_MARKER,
    build_knowledge_catalogs,
    catalog_item,
    extract_rephrased_request,
    format_augmented_prompt,
    normalize_conversation_history,
    parse_json_blob,
    resolve_selected_ids,
    split_satisfaction_response,
)
from math_agent.web import knowledge_routes as web_app


def test_catalog_item_fact():
    item = {
        "id": "f1",
        "statement": "Every prime greater than 2 is odd.",
        "note": "Basic fact",
    }
    out = catalog_item(item, title_keys=("statement", "title"), body_keys=("note", "why", "body"))
    assert out == {
        "id": "f1",
        "title": "Every prime greater than 2 is odd.",
        "body": "Basic fact",
    }


def test_catalog_item_requires_id_and_title():
    assert catalog_item({"id": "x"}, title_keys=("title",), body_keys=("body",)) is None
    assert catalog_item({"title": "x"}, title_keys=("title",), body_keys=("body",)) is None


def test_resolve_selected_ids_preserves_order_and_limits():
    catalog = [
        {"id": "a", "title": "A", "body": ""},
        {"id": "b", "title": "B", "body": ""},
        {"id": "c", "title": "C", "body": ""},
    ]
    out = resolve_selected_ids(["c", "missing", "a", "a"], catalog, limit=2)
    assert [item["id"] for item in out] == ["c", "a"]


def test_parse_json_blob_from_mixed_text():
    raw = 'Reasoning first.\n{"selected_fact_ids": ["f1"], "selected_intuition_ids": [], "selected_trick_ids": []}'
    data = parse_json_blob(raw)
    assert data is not None
    assert data["selected_fact_ids"] == ["f1"]


def test_format_augmented_prompt():
    prompt = format_augmented_prompt(
        "Prove the claim.",
        [{"id": "f1", "title": "Fact A", "body": ""}],
        [{"id": "i1", "title": "Try induction", "body": "on n"}],
        [],
    )
    assert "=== Relevant Project Knowledge ===" in prompt
    assert "[Facts]" in prompt
    assert "Fact A" in prompt
    assert "[Intuitions]" in prompt
    assert "=== Current Request ===" in prompt
    assert "Prove the claim." in prompt


def test_extract_rephrased_request():
    augmented = "=== Relevant Project Knowledge ===\n\n=== Current Request ===\nProve that sqrt(2) is irrational."
    assert extract_rephrased_request(augmented, "original") == "Prove that sqrt(2) is irrational."
    assert extract_rephrased_request("no marker", "original") == "original"


def test_split_satisfaction_response():
    raw = (
        "Glad that helped — I'll keep using that induction sketch.\n"
        f"{SATISFACTION_ACTIONS_MARKER}\n"
        '{"user_satisfied": true, "useful_items": [], "material_outcomes": [], "nail_down": []}'
    )
    prose, actions = split_satisfaction_response(raw)
    assert "Glad that helped" in prose
    assert actions.get("user_satisfied") is True


def test_solve_catalogs_only_admit_explicitly_trusted_items():
    facts, intuitions, tricks = build_knowledge_catalogs(
        [
            {"id": "candidate", "statement": "candidate leak", "status": "candidate"},
            {"id": "rejected", "statement": "rejected leak", "status": "rejected"},
            {"id": "approved", "statement": "approved fact", "status": "approved"},
            {"id": "reviewed", "statement": "reviewed fact", "status": "reviewed"},
            {"id": "verified", "statement": "verified fact", "status": "verified"},
            {"id": "legacy", "statement": "statusless leak"},
        ],
        [{"id": "i1", "title": "candidate idea", "status": "candidate"}],
        [{"id": "t1", "title": "approved trick", "status": "approved"}],
    )

    assert [item["id"] for item in facts] == ["approved", "reviewed", "verified"]
    assert intuitions == []
    assert [item["id"] for item in tricks] == ["t1"]


def test_catalog_and_history_normalize_non_string_values_with_caps():
    entry = catalog_item(
        {"id": 17, "title": 42, "body": {"detail": "x" * 500}},
        title_keys=("title",),
        body_keys=("body",),
    )
    assert entry is not None
    assert entry["id"] == "17"
    assert entry["title"] == "42"
    assert isinstance(entry["body"], str)
    assert len(entry["body"]) <= 400

    history = normalize_conversation_history(
        [
            {"role": "user", "text": {"prompt": "x" * 700}},
            {"role": "assistant", "content": ["answer", 2]},
            {"role": {"invalid": True}, "text": "skip me"},
        ]
    )
    assert [turn["role"] for turn in history] == ["user", "assistant"]
    assert all(isinstance(turn["text"], str) for turn in history)
    assert all(len(turn["text"]) <= 500 for turn in history)


def test_knowledge_snapshot_normalizes_non_string_values_with_caps():
    snapshot = web_app._knowledge_snapshot(
        [{"id": 1, "statement": ["fact"], "note": {"detail": "x" * 400}}],
        [{"id": 2, "title": 99, "body": {"idea": True}, "successCount": "bad"}],
        [{"id": 3, "title": {"trick": "T"}, "body": [1, 2], "successCount": []}],
        [{
            "id": 4,
            "status": "candidate",
            "kind": ["fact"],
            "title": {"material": "M"},
            "body": {"proof": "x" * 500},
            "positiveOutcomes": {},
            "negativeOutcomes": "bad",
            "usageCount": [],
        }],
    )

    for kind, body_limit in (("facts", 280), ("intuitions", 280), ("tricks", 280), ("materials", 400)):
        assert len(snapshot[kind]) == 1
        row = snapshot[kind][0]
        assert isinstance(row["id"], str)
        assert isinstance(row["title"], str)
        assert isinstance(row["body"], str)
        assert len(row["title"]) <= 240
        assert len(row["body"]) <= body_limit
    assert snapshot["intuitions"][0]["successCount"] == 0
    assert snapshot["tricks"][0]["successCount"] == 0
    assert snapshot["materials"][0]["usageCount"] == 0


class _ChunkedLLM:
    def __init__(self, chunks):
        self.chunks = chunks
        self.consumed = []
        self.closed = False
        self.messages = None

    def stream(self, messages, system="", temperature=None, response_format=None):
        self.messages = messages
        return _ClosableChunkStream(self)


class _ClosableChunkStream:
    def __init__(self, owner):
        self.owner = owner
        self.index = 0

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self.index >= len(self.owner.chunks):
            raise StopAsyncIteration
        chunk = self.owner.chunks[self.index]
        self.index += 1
        response = LLMResponse(
            text=chunk,
            prompt_tokens=0,
            completion_tokens=0,
            total_tokens=0,
        )
        self.owner.consumed.append(response)
        return response

    async def aclose(self):
        self.owner.closed = True


async def _collect_selection_events(payload):
    return [event async for event in web_app._knowledge_selection_events(payload)]


@pytest.mark.asyncio
async def test_selection_stream_drains_split_marker_and_json_without_emitting_json(monkeypatch):
    chunks = [
        "Reasoning about parity.\n---RES",
        "ULT---\n{\"selected_fact_",
        "ids\":[\"f1\"],\"selected_intuition_ids\":[],",
        "\"selected_trick_ids\":[],\"augmented_prompt\":\"=== Current Request ===\\nProve parity.\",",
        "\"rephrased_prompt\":\"Prove parity.\"}",
        "must not be consumed after complete JSON",
    ]
    llm = _ChunkedLLM(chunks)
    monkeypatch.setattr(web_app, "create_backend_from_model_string", lambda *args, **kwargs: llm)
    payload = {
        "problem": "Original parity request",
        "model": "fake/model",
        "facts": [{"id": "f1", "statement": "Parity fact", "status": "approved"}],
        "intuitions": [],
        "tricks": [],
    }

    events = await _collect_selection_events(payload)

    tokens = "".join(event["text"] for event in events if event["type"] == "token")
    assert tokens == "Reasoning about parity.\n"
    assert "selected_fact_ids" not in tokens
    assert len(llm.consumed) == 5
    assert llm.closed is True
    done = next(event for event in events if event["type"] == "done")
    assert [fact["id"] for fact in done["facts"]] == ["f1"]
    assert done["rephrased_prompt"] == "Prove parity."


@pytest.mark.asyncio
async def test_selection_stream_caps_accumulation_at_128000_and_fails_safely(monkeypatch):
    llm = _ChunkedLLM(["x" * 128_000, "y"])
    monkeypatch.setattr(web_app, "create_backend_from_model_string", lambda *args, **kwargs: llm)
    payload = {
        "problem": "Bound the response",
        "model": "fake/model",
        "facts": [{"id": "f1", "statement": "A fact", "status": "approved"}],
        "intuitions": [],
        "tricks": [],
    }

    events = await _collect_selection_events(payload)

    assert events[-1]["type"] == "error"
    assert not any(event["type"] == "done" for event in events)
    assert sum(len(event["text"]) for event in events if event["type"] == "token") == 128_000
    assert llm.closed is True


@pytest.mark.asyncio
async def test_selection_overflow_closes_upstream_before_terminal_error_is_observed(monkeypatch):
    llm = _ChunkedLLM(["x" * 128_000, "y"])
    monkeypatch.setattr(web_app, "create_backend_from_model_string", lambda *args, **kwargs: llm)
    payload = {
        "problem": "Bound the response",
        "model": "fake/model",
        "facts": [{"id": "f1", "statement": "A fact", "status": "approved"}],
        "intuitions": [],
        "tricks": [],
    }
    events = web_app._knowledge_selection_events(payload)

    try:
        while True:
            event = await events.__anext__()
            if event["type"] == "error":
                break
        assert llm.closed is True
    finally:
        await events.aclose()


class _ClosableSelectionEvents:
    def __init__(self, events):
        self.events = iter(events)
        self.closed = False

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return next(self.events)
        except StopIteration as exc:
            raise StopAsyncIteration from exc

    async def aclose(self):
        self.closed = True


@pytest.mark.asyncio
async def test_nonstream_selection_closes_inner_generator_on_terminal_return(monkeypatch):
    stream = _ClosableSelectionEvents([{
        "type": "done",
        "facts": [],
        "intuitions": [],
        "tricks": [],
        "augmented_prompt": "P",
    }])
    monkeypatch.setattr(web_app, "_knowledge_selection_events", lambda *args, **kwargs: stream)
    monkeypatch.setattr(
        web_app,
        "require_auth_user",
        lambda request: type("User", (), {"user_id": "user-test"})(),
    )

    result = await web_app.select_knowledge({"problem": "P"}, object())

    assert result["ok"] is True
    assert stream.closed is True


@pytest.mark.asyncio
async def test_sse_selection_closes_inner_generator_when_client_stops_early(monkeypatch):
    stream = _ClosableSelectionEvents([
        {"type": "phase_start", "phase": "prepare", "label": "Prepare"},
        {"type": "done", "facts": [], "intuitions": [], "tricks": []},
    ])
    monkeypatch.setattr(web_app, "_knowledge_selection_events", lambda *args, **kwargs: stream)
    monkeypatch.setattr(
        web_app,
        "require_auth_user",
        lambda request: type("User", (), {"user_id": "user-test"})(),
    )

    response = await web_app.select_knowledge_stream({"problem": "P"}, object())
    body = response.body_iterator
    first = await body.__anext__()
    assert "phase_start" in first
    await body.aclose()

    assert stream.closed is True


@pytest.mark.asyncio
async def test_selection_treats_truthy_non_list_catalogs_as_empty(monkeypatch):
    monkeypatch.setattr(
        web_app,
        "create_backend_from_model_string",
        lambda *args, **kwargs: pytest.fail("empty malformed catalogs must not call the LLM"),
    )
    payload = {
        "problem": "Prove the claim",
        "model": "fake/model",
        "facts": 1,
        "intuitions": {"unexpected": "mapping"},
        "tricks": True,
    }

    events = await _collect_selection_events(payload)

    assert events == [{
        "type": "done",
        "ok": True,
        "analysis": "",
        "selection_reasoning": "",
        "facts": [],
        "intuitions": [],
        "tricks": [],
        "augmented_prompt": "Prove the claim",
        "reason": "No usable project knowledge.",
    }]


@pytest.mark.asyncio
async def test_satisfaction_treats_truthy_non_list_snapshot_fields_as_empty(monkeypatch):
    llm = _ChunkedLLM([
        "Thanks.\n---ACTIONS---\n"
        '{"user_satisfied":true,"useful_items":[],"material_outcomes":[],"nail_down":[]}'
    ])
    monkeypatch.setattr(web_app, "create_backend_from_model_string", lambda *args, **kwargs: llm)
    payload = {
        "problem": "Continue",
        "conversation_history": [{"role": "user", "text": "Thanks"}],
        "model": "fake/model",
        "facts": 1,
        "intuitions": {"unexpected": "mapping"},
        "tricks": True,
        "materials": "not a list",
    }

    events = [event async for event in web_app._evaluate_satisfaction_events(payload)]

    request_data = __import__("json").loads(llm.messages[0].content)
    assert request_data["project_knowledge"] == {
        "facts": [],
        "intuitions": [],
        "tricks": [],
        "materials": [],
    }
    assert events[-1]["type"] == "done"
