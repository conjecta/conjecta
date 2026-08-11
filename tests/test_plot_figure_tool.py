import pytest

from math_agent.agent.react_state import Action
from math_agent.agent.tools import ToolContext, ToolRegistry


@pytest.mark.asyncio
async def test_plot_figure_tool_saves_png_and_returns_markdown_link(tmp_path):
    registry = ToolRegistry(enabled_tools=["plot_figure"])
    assert "plot_figure" in registry.available

    ctx = ToolContext(
        figure_dir=tmp_path, figure_url_prefix="/api/solve/figures/sess-1"
    )
    action = Action(
        name="plot_figure",
        args={
            "code": "import matplotlib.pyplot as plt\nplt.plot([0, 1], [0, 1])",
            "caption": "直线 y=x",
        },
    )
    observation = await registry.execute_action(action, ctx)
    assert observation.success
    assert "![直线 y=x](/api/solve/figures/sess-1/fig-1.png)" in observation.output
    assert (tmp_path / "fig-1.png").is_file()


@pytest.mark.asyncio
async def test_plot_figure_tool_falls_back_to_local_path(tmp_path):
    registry = ToolRegistry(enabled_tools=["plot_figure"])
    ctx = ToolContext(figure_dir=tmp_path)
    action = Action(
        name="plot_figure",
        args={"code": "import matplotlib.pyplot as plt\nplt.plot([1])"},
    )
    observation = await registry.execute_action(action, ctx)
    assert observation.success
    assert str(tmp_path / "fig-1.png") in observation.output


@pytest.mark.asyncio
async def test_plot_figure_tool_unavailable_without_figure_dir():
    registry = ToolRegistry(enabled_tools=["plot_figure"])
    action = Action(
        name="plot_figure",
        args={"code": "import matplotlib.pyplot as plt\nplt.plot([1])"},
    )
    observation = await registry.execute_action(action, ToolContext())
    assert not observation.success
    assert "unavailable" in observation.output


@pytest.mark.asyncio
async def test_plot_figure_tool_reports_plot_errors(tmp_path):
    registry = ToolRegistry(enabled_tools=["plot_figure"])
    ctx = ToolContext(figure_dir=tmp_path)
    action = Action(name="plot_figure", args={"code": "import os"})
    observation = await registry.execute_action(action, ctx)
    assert not observation.success
    assert "Import not allowed" in observation.output
