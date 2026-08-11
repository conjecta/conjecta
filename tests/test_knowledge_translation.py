from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from starlette.requests import Request

from math_agent.billing.models import LLMResponse
from math_agent.web import agent_factory
from math_agent.web import knowledge_routes as web_app
from math_agent.web.knowledge_translation import is_primarily_english
from math_agent.web.project_store import ProjectStore


class _TranslationLLM:
    def __init__(self) -> None:
        self.calls = 0

    async def complete(self, messages, system=None, temperature=None):
        self.calls += 1
        assert "Preserve all LaTeX" in (system or "")
        return LLMResponse(
            text=json.dumps({
                "title_zh": "前缀乘积将子集乘积问题转化为碰撞问题",
                "body_zh": r"定义 $P_k=a_1\cdots a_k \pmod p$。",
            }, ensure_ascii=False),
            prompt_tokens=0,
            completion_tokens=0,
            total_tokens=0,
        )


def _request() -> Request:
    return Request({"type": "http", "method": "POST", "path": "/api/knowledge/translate", "headers": [], "query_string": b""})


def test_language_detection_ignores_chinese_and_symbol_only_text():
    assert is_primarily_english("Prefix products turn subset-product problems into collision problems")
    assert not is_primarily_english("前缀乘积把子集乘积问题变成碰撞问题")
    assert not is_primarily_english("P_k = a_1 ... a_k (mod p)")


@pytest.mark.asyncio
async def test_translation_endpoint_persists_and_reuses_chinese_fields(tmp_path, monkeypatch):
    store = ProjectStore(root=tmp_path)
    item = store.add_intuition(
        "default",
        "Prefix products turn subset-product problems into collision problems",
        "Define P_k as the product of the first k terms modulo p.",
    )
    llm = _TranslationLLM()
    monkeypatch.setattr(agent_factory, "require_auth_user", lambda _request: SimpleNamespace(user_id="u_test"))
    monkeypatch.setattr(web_app, "_maybe_knowledge_store", lambda _user_id: store)
    monkeypatch.setattr(web_app, "create_backend_from_model_string", lambda *_a, **_k: llm)

    payload = {"project_id": "default", "item_id": item["id"], "kind": "intuition"}
    first = await web_app.translate_knowledge(payload, _request())
    second = await web_app.translate_knowledge(payload, _request())

    assert first["cached"] is False
    assert second["cached"] is True
    assert llm.calls == 1
    saved = store.list_intuitions("default")[0]
    assert saved["title_zh"].startswith("前缀乘积")
    assert "P_k" in saved["body_zh"]
