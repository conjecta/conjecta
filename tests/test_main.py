from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from math_agent import main


@pytest.mark.asyncio
async def test_cli_closes_mcp_client_on_exception():
    cfg = MagicMock()
    cfg.logging.enabled = False
    cfg.agent.tools = []
    cfg.agent.memory_consolidation_enabled = False
    cfg.lean.enabled = False
    cfg.mcp_servers = []
    cfg.knowledge = MagicMock()
    cfg.verifier = MagicMock()

    mcp_client = MagicMock()
    mcp_client.initialize = AsyncMock()
    mcp_client.close = AsyncMock()

    agent = MagicMock()
    agent.solve = AsyncMock(side_effect=RuntimeError("boom"))

    with (
        patch("math_agent.main.load_config", return_value=cfg),
        patch("math_agent.main.create_backend", return_value=MagicMock()),
        patch("math_agent.agent.mcp_client.McpClient", return_value=mcp_client),
        patch("math_agent.agent.tools.ToolRegistry"),
        patch("math_agent.agent.supervisor.SupervisorAgent", return_value=agent),
    ):
        with pytest.raises(RuntimeError):
            await main.run("test")

    mcp_client.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_cli_handles_human_input_required_gracefully(capsys):
    from math_agent.agent.react_state import HumanInputRequired

    cfg = MagicMock()
    cfg.logging.enabled = False
    cfg.agent.tools = []
    cfg.agent.memory_consolidation_enabled = False
    cfg.lean.enabled = False
    cfg.mcp_servers = []
    cfg.knowledge = MagicMock()
    cfg.verifier = MagicMock()

    mcp_client = MagicMock()
    mcp_client.initialize = AsyncMock()
    mcp_client.close = AsyncMock()

    agent = MagicMock()
    agent.solve = AsyncMock(
        side_effect=HumanInputRequired(
            {
                "request_id": "r1",
                "kind": "plan_review",
                "question": "审查研究计划？",
            }
        )
    )

    with (
        patch("math_agent.main.load_config", return_value=cfg),
        patch("math_agent.main.create_backend", return_value=MagicMock()),
        patch("math_agent.agent.mcp_client.McpClient", return_value=mcp_client),
        patch("math_agent.agent.tools.ToolRegistry"),
        patch("math_agent.agent.supervisor.SupervisorAgent", return_value=agent),
    ):
        await main.run("test")

    mcp_client.close.assert_awaited_once()
    out = capsys.readouterr().out
    assert "审查研究计划？" in out
    assert "r1" in out
