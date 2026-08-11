import pytest

from math_agent.agent.knowledge.graph import KnowledgeGraph
from math_agent.agent.react_state import Action, ProjectContext
from math_agent.agent.tools import ToolContext, ToolRegistry


def test_graph_adds_and_retrieves_relations(tmp_path):
    graph = KnowledgeGraph(root=str(tmp_path))
    graph.add_relation("fact-a", "fact-b", "implies", "proj-1")
    related = graph.get_related("fact-a", "proj-1")
    assert len(related) == 1
    assert related[0]["to_id"] == "fact-b"
    assert related[0]["relation"] == "implies"


@pytest.mark.asyncio
async def test_relate_and_find_tools(tmp_path):
    graph = KnowledgeGraph(root=str(tmp_path))
    registry = ToolRegistry(enabled_tools=["relate_knowledge", "find_related"])
    ctx = ToolContext(project_context=ProjectContext(project_id="proj-g"), knowledge_graph=graph)

    add_action = Action(name="relate_knowledge", args={"spec": "fact-1,fact-2,implies"})
    obs = await registry.execute_action(add_action, ctx)
    assert obs.success is True

    find_action = Action(name="find_related", args={"item_id": "fact-1"})
    obs2 = await registry.execute_action(find_action, ctx)
    assert "fact-1 --implies--> fact-2" in obs2.output
