from __future__ import annotations

from types import SimpleNamespace

import pytest

from math_agent.billing.models import LLMResponse
from math_agent.web import knowledge_routes as web_app
from math_agent.agent.materials import MaterialStore
from math_agent.web.knowledge_graph import build_knowledge_graph
from math_agent.web.project_store import ProjectStore


def test_build_knowledge_graph_includes_knowledge_materials_sources_and_edges(tmp_path):
    project_store = ProjectStore(tmp_path / "projects")
    material_store = MaterialStore(root=tmp_path / "materials")
    project_store.add_many(
        "proj-1",
        [{"id": "fact-1", "statement": "Gaussian concentration", "why": "Core tail bound", "source": "Talagrand"}],
        [{"id": "intuition-1", "title": "Center first", "body": "Remove the mean before applying concentration.", "kind": "heuristic", "source": "Talagrand"}],
        [{"id": "tech-1", "title": "Whitening", "body": "Normalize covariance before comparison.", "category": "linear algebra", "source": "Lecture notes"}],
    )
    material_store.add("proj-1", "url", "Talagrand paper", "Long source text", "https://example.com/talagrand")
    project_store.add_knowledge_graph_edges(
        "proj-1",
        [
            {
                "source": "intuition-1",
                "target": "fact-1",
                "kind": "supports",
                "label": "motivates the estimate",
                "evidence": "The centering step makes the concentration bound usable.",
                "weight": 0.8,
            }
        ],
    )

    graph = build_knowledge_graph(project_store, material_store, "proj-1")

    assert graph["ok"] is True
    assert {node["id"] for node in graph["nodes"]} >= {"fact-1", "intuition-1", "tech-1"}
    assert any(node["kind"] == "material" and node["label"] == "Talagrand paper" for node in graph["nodes"])
    assert any(node["kind"] == "source" and node["label"] == "Talagrand" for node in graph["nodes"])
    assert graph["edges"] == [
        {
            "id": "intuition-1:supports:fact-1",
            "source": "intuition-1",
            "target": "fact-1",
            "kind": "supports",
            "label": "motivates the estimate",
            "evidence": "The centering step makes the concentration bound usable.",
            "weight": 0.8,
            "created_at": graph["edges"][0]["created_at"],
            "metadata": {},
        }
    ]


def test_build_knowledge_graph_ignores_edges_with_missing_nodes(tmp_path):
    project_store = ProjectStore(tmp_path / "projects")
    material_store = MaterialStore(root=tmp_path / "materials")
    project_store.add_many(
        "proj-1",
        [{"id": "fact-1", "statement": "Gaussian concentration", "why": "", "source": ""}],
        [],
        [],
    )
    project_store.add_knowledge_graph_edges(
        "proj-1",
        [{"source": "fact-1", "target": "missing", "kind": "related_to", "label": "bad"}],
    )

    graph = build_knowledge_graph(project_store, material_store, "proj-1")

    assert graph["edges"] == []


def test_build_knowledge_graph_skips_internal_pipeline_sources(tmp_path):
    project_store = ProjectStore(tmp_path / "projects")
    material_store = MaterialStore(root=tmp_path / "materials")
    project_store.add_many(
        "proj-1",
        [
            {
                "id": "fact-1",
                "statement": "Maximum principle implies nonnegativity",
                "why": "",
                "source": "memory_consolidation",
            },
            {
                "id": "fact-2",
                "statement": "Strong maximum principle gives strict positivity",
                "why": "",
                "source": "knowledge_evaluator",
            },
            {
                "id": "fact-3",
                "statement": "Poisson equation with nonnegative right-hand side",
                "why": "",
                "source": "https://www.math.uci.edu/~rvershyn/papers/concentration.pdf",
            },
        ],
        [],
        [],
    )

    graph = build_knowledge_graph(project_store, material_store, "proj-1")
    source_labels = {
        node["label"] for node in graph["nodes"] if node["kind"] == "source"
    }

    assert "memory_consolidation" not in source_labels
    assert "knowledge_evaluator" not in source_labels
    assert "https://www.math.uci.edu/~rvershyn/papers/concentration.pdf" in source_labels


@pytest.mark.asyncio
async def test_graph_endpoint_uses_authoritative_nodes_and_local_edges(monkeypatch, tmp_path):
    cloud_store = ProjectStore(tmp_path / "cloud")
    local_store = ProjectStore(tmp_path / "local")
    cloud_store.add_many(
        "proj-1",
        [{"id": "cloud-fact", "statement": "Cloud fact", "status": "approved"}],
        [{"id": "cloud-idea", "title": "Cloud idea", "body": "Body", "status": "approved"}],
        [],
    )
    local_store.add_fact("proj-1", "Local-only fact")
    local_store.add_knowledge_graph_edges(
        "proj-1",
        [{"source": "cloud-idea", "target": "cloud-fact", "kind": "supports"}],
    )
    monkeypatch.setattr(
        web_app, "require_auth_user", lambda _request: SimpleNamespace(user_id="u-cloud")
    )
    monkeypatch.setattr(web_app, "_maybe_knowledge_store", lambda _user_id=None: cloud_store)
    monkeypatch.setattr(web_app, "_project_store", lambda _user_id=None: local_store)
    monkeypatch.setattr(web_app, "_material_store", lambda _user_id=None: None)

    graph = await web_app.list_knowledge_graph(object(), project_id="proj-1")

    node_ids = {node["id"] for node in graph["nodes"]}
    assert {"cloud-fact", "cloud-idea"} <= node_ids
    assert all(node["label"] != "Local-only fact" for node in graph["nodes"])
    assert graph["edges"][0]["source"] == "cloud-idea"
    assert graph["edges"][0]["target"] == "cloud-fact"


@pytest.mark.asyncio
async def test_graph_explore_uses_authoritative_nodes_and_persists_local_edges(
    monkeypatch, tmp_path
):
    cloud_store = ProjectStore(tmp_path / "cloud")
    local_store = ProjectStore(tmp_path / "local")
    cloud_store.add_many(
        "proj-1",
        [{"id": "cloud-fact", "statement": "Cloud fact", "status": "approved"}],
        [{"id": "cloud-idea", "title": "Cloud idea", "body": "Body", "status": "approved"}],
        [],
    )

    class EdgeLLM:
        async def complete(self, *_args, **_kwargs):
            return LLMResponse(
                text=(
                    '{"edges":[{"source":"cloud-idea","target":"cloud-fact",'
                    '"kind":"supports","label":"supports","weight":0.9}]}'
                ),
                prompt_tokens=0,
                completion_tokens=0,
                total_tokens=0,
            )

    monkeypatch.setattr(
        web_app, "require_auth_user", lambda _request: SimpleNamespace(user_id="u-cloud")
    )
    monkeypatch.setattr(web_app, "_maybe_knowledge_store", lambda _user_id=None: cloud_store)
    monkeypatch.setattr(web_app, "_project_store", lambda _user_id=None: local_store)
    monkeypatch.setattr(web_app, "_material_store", lambda _user_id=None: None)
    monkeypatch.setattr(
        web_app, "create_backend_from_model_string", lambda *_args, **_kwargs: EdgeLLM()
    )

    graph = await web_app.explore_knowledge_graph(
        {"project_id": "proj-1", "model": "openai/gpt-5.6-sol"}, object()
    )

    assert {node["id"] for node in graph["nodes"]} >= {"cloud-fact", "cloud-idea"}
    assert graph["edges"][0]["source"] == "cloud-idea"
    assert local_store.list_knowledge_graph_edges("proj-1")[0]["target"] == "cloud-fact"
