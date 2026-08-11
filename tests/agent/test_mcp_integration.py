"""Integration tests for recommended external MCP servers.

These tests use mocked MCP servers so they do not require SageMath or arXiv
to be installed or reachable. They verify that Conjecta's generic MCP client
can discover and call tools whose schemas match the real servers.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from math_agent.agent.mcp_client import McpClient
from math_agent.mcp_config import McpServerConfig
from math_agent.agent.tools import ToolRegistry
from math_agent.agent.react_state import Action


class FakeTool:
    def __init__(self, name: str, description: str = "", input_schema: dict | None = None):
        self.name = name
        self.description = description
        self.inputSchema = input_schema or {"type": "object"}


class FakeTextContent:
    def __init__(self, text: str):
        self.text = text


class FakeCallResult:
    def __init__(self, content: Any):
        self.content = content


class FakeListToolsResult:
    def __init__(self, tools: list[Any]):
        self.tools = tools


class FakeSession:
    def __init__(self, list_tools_result: FakeListToolsResult, call_tool_result: FakeCallResult):
        self._list_tools_result = list_tools_result
        self._call_tool_result = call_tool_result

    async def initialize(self) -> None:
        return None

    async def list_tools(self) -> FakeListToolsResult:
        return self._list_tools_result

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> FakeCallResult:
        return self._call_tool_result

    async def __aenter__(self) -> "FakeSession":
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        return None


@asynccontextmanager
async def fake_stdio_client(params: Any):
    yield (MagicMock(), MagicMock())


@pytest.mark.asyncio
async def test_sagemath_mcp_evaluate():
    """SageMath MCP exposes evaluate/version tools; Conjecta can call them."""
    server = McpServerConfig(
        name="sagemath",
        transport="stdio",
        command="sagemath-mcp",
    )
    client = McpClient([server])

    fake_session = FakeSession(
        list_tools_result=FakeListToolsResult(
            tools=[
                FakeTool(
                    "sagemath.evaluate",
                    "Evaluate a SageMath script and return stdout/stderr.",
                    {
                        "type": "object",
                        "properties": {
                            "script": {"type": "string"},
                            "timeout": {"type": "number"},
                        },
                        "required": ["script"],
                    },
                ),
                FakeTool("sagemath.version", "Return the installed SageMath version."),
            ]
        ),
        call_tool_result=FakeCallResult([FakeTextContent("x^3/3")]),
    )

    with patch("mcp.ClientSession", return_value=fake_session):
        with patch("mcp.client.stdio.stdio_client", fake_stdio_client):
            with patch("mcp.StdioServerParameters"):
                await client.initialize()

    registry = ToolRegistry(enabled_tools=["compute"], mcp_client=client)
    assert "mcp_sagemath_sagemath_evaluate" in registry.available

    observation = await registry.execute_action(
        Action(
            name="mcp_sagemath_sagemath_evaluate",
            args={"script": "integrate(x^2, x)"},
        )
    )
    assert observation.success
    assert observation.output == "x^3/3"


@pytest.mark.asyncio
async def test_arxiv_mcp_search_and_fetch():
    """arXiv MCP server exposes search/fetch tools; Conjecta can call them."""
    server = McpServerConfig(
        name="arxiv",
        transport="stdio",
        command="arxiv-mcp-server",
    )
    client = McpClient([server])

    fake_session = FakeSession(
        list_tools_result=FakeListToolsResult(
            tools=[
                FakeTool(
                    "search_papers",
                    "Search arXiv for papers matching a query.",
                    {
                        "type": "object",
                        "properties": {
                            "query": {"type": "string"},
                            "max_results": {"type": "number"},
                        },
                        "required": ["query"],
                    },
                ),
                FakeTool(
                    "get_paper",
                    "Retrieve the abstract and metadata of an arXiv paper by ID.",
                    {
                        "type": "object",
                        "properties": {"paper_id": {"type": "string"}},
                        "required": ["paper_id"],
                    },
                ),
            ]
        ),
        call_tool_result=FakeCallResult(
            [FakeTextContent("1. arXiv:1234.56789 - A paper about primes")]
        ),
    )

    with patch("mcp.ClientSession", return_value=fake_session):
        with patch("mcp.client.stdio.stdio_client", fake_stdio_client):
            with patch("mcp.StdioServerParameters"):
                await client.initialize()

    registry = ToolRegistry(enabled_tools=["compute"], mcp_client=client)
    assert "mcp_arxiv_search_papers" in registry.available
    assert "mcp_arxiv_get_paper" in registry.available

    observation = await registry.execute_action(
        Action(
            name="mcp_arxiv_search_papers",
            args={"query": "prime number theorem", "max_results": 3},
        )
    )
    assert observation.success
    assert "arXiv:1234.56789" in observation.output


def test_mcp_server_config_examples_parse():
    """The recommended server configs from config.example.toml round-trip correctly."""
    raw = [
        {
            "name": "sagemath",
            "transport": "stdio",
            "command": "sagemath-mcp",
        },
        {
            "name": "arxiv",
            "transport": "stdio",
            "command": "arxiv-mcp-server",
        },
    ]
    from math_agent.mcp_config import parse_mcp_servers

    servers = parse_mcp_servers(raw)
    assert len(servers) == 2
    assert servers[0].name == "sagemath"
    assert servers[1].name == "arxiv"
