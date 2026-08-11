import pytest
from math_agent.web import agent_factory as web_app
from math_agent.agent.materials import MaterialStore
from math_agent.agent.react_state import Action, ProjectContext
from math_agent.agent.tools import ToolContext, ToolRegistry


@pytest.mark.asyncio
async def test_material_store_persists_and_searches(tmp_path):
    store = MaterialStore(root=str(tmp_path))
    material = store.add(
        "proj-1",
        "url",
        "Example",
        "The Riemann hypothesis is about zeta zeros.",
        "https://example.com",
    )
    assert material.id
    listed = store.list("proj-1")
    assert len(listed) == 1
    results = store.search("proj-1", "Riemann")
    assert len(results) == 1
    assert "Riemann" in results[0].text


@pytest.mark.asyncio
async def test_read_sources_tool_stores_material(monkeypatch, tmp_path):
    from math_agent.source_fetch import FetchedSource

    store = MaterialStore(root=str(tmp_path))

    async def fake_fetch(prompt, *, max_chars):
        return [FetchedSource(url="https://example.com", label="Ex", text="Riemann zeta zeros.")]

    monkeypatch.setattr("math_agent.source_fetch.fetch_sources_from_prompt", fake_fetch)
    registry = ToolRegistry(enabled_tools=["read_sources"])
    ctx = ToolContext(project_context=ProjectContext(project_id="proj-x"), material_store=store)
    action = Action(name="read_sources", args={"prompt": "read https://example.com"})
    obs = await registry.execute_action(action, ctx)
    assert obs.success is True
    assert store.search("proj-x", "Riemann")


def test_web_material_store_sanitizes_tenant_path_without_collisions(monkeypatch, tmp_path):
    monkeypatch.setenv("CONJECTA_MATERIAL_STORE_DIR", str(tmp_path))

    traversal_store = web_app._material_store("../../shared")
    plain_store = web_app._material_store("shared")

    assert traversal_store.root.resolve().parent == tmp_path.resolve()
    assert plain_store.root.resolve().parent == tmp_path.resolve()
    assert traversal_store.root != plain_store.root
