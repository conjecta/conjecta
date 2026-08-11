from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from math_agent.agent.mcp_client import McpClient, McpToolResult
from math_agent.mcp_config import McpServerConfig, parse_mcp_servers
from math_agent.agent.tools import ToolRegistry


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
    """A minimal fake MCP ClientSession that supports async context management."""

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


@asynccontextmanager
async def fake_sse_client(url: str, headers: dict[str, str] | None = None):
    yield (MagicMock(), MagicMock())


def test_parse_mcp_servers():
    raw = [
        {
            "name": "python-repl",
            "transport": "stdio",
            "command": "python",
            "args": ["-m", "repl"],
            "env": {"FOO": "bar"},
        },
        {
            "name": "remote",
            "transport": "sse",
            "url": "http://localhost:3000/sse",
        },
    ]
    servers = parse_mcp_servers(raw)
    assert len(servers) == 2
    assert servers[0].name == "python-repl"
    assert servers[0].transport == "stdio"
    assert servers[0].command == "python"
    assert servers[0].args == ["-m", "repl"]
    assert servers[0].env == {"FOO": "bar"}
    assert servers[1].transport == "sse"
    assert servers[1].url == "http://localhost:3000/sse"


def test_parse_mcp_servers_ignores_invalid():
    servers = parse_mcp_servers([{"transport": "stdio"}, {"name": "ok"}])
    assert len(servers) == 1
    assert servers[0].name == "ok"


def test_parse_mcp_servers_allows_portable_command_override(monkeypatch):
    monkeypatch.setenv("CONJECTA_MCP_SAGEMATH_COMMAND", "/custom/bin/sagemath-mcp")

    servers = parse_mcp_servers(
        [{"name": "sagemath", "transport": "stdio", "command": "sagemath-mcp"}]
    )

    assert servers[0].command == "/custom/bin/sagemath-mcp"


@pytest.mark.asyncio
async def test_mcp_client_discovers_and_calls_tools():
    server = McpServerConfig(name="python-repl", transport="stdio", command="python", args=["-m", "repl"])
    client = McpClient([server])

    fake_session = FakeSession(
        list_tools_result=FakeListToolsResult(
            tools=[FakeTool("execute", "Run Python code", {"type": "object", "properties": {"code": {"type": "string"}}})]
        ),
        call_tool_result=FakeCallResult([FakeTextContent("42")]),
    )

    with patch("mcp.ClientSession", return_value=fake_session):
        with patch("mcp.client.stdio.stdio_client", fake_stdio_client):
            with patch("mcp.StdioServerParameters"):
                await client.initialize()

    tools = client.tools
    assert "mcp_python_repl_execute" in tools
    assert tools["mcp_python_repl_execute"]["definition"]["name"] == "execute"
    assert client.health["python-repl"] == {
        "status": "connected",
        "error": "",
        "tool_count": 1,
    }

    result = await client.call_tool_from_json("mcp_python_repl_execute", json.dumps({"code": "print(6*7)"}))
    assert result.output == "42"
    assert result.success is True


@pytest.mark.asyncio
async def test_tool_registry_registers_mcp_tools():
    server = McpServerConfig(name="math-bridge", transport="sse", url="http://localhost:3000/sse")
    client = McpClient([server])

    fake_session = FakeSession(
        list_tools_result=FakeListToolsResult(
            tools=[FakeTool("lookup", "Look up a theorem")]
        ),
        call_tool_result=FakeCallResult([FakeTextContent("Theorem found")]),
    )

    with patch("mcp.ClientSession", return_value=fake_session):
        with patch("mcp.client.sse.sse_client", fake_sse_client):
            await client.initialize()

    registry = ToolRegistry(enabled_tools=["compute"], mcp_client=client)
    assert "mcp_math_bridge_lookup" in registry.available

    from math_agent.agent.react_state import Action

    observation = await registry.execute_action(
        Action(name="mcp_math_bridge_lookup", args={"query": "Nat.gcd_comm"})
    )
    assert observation.success
    assert observation.output == "Theorem found"


def test_tool_registry_describes_builtins_without_mcp():
    registry = ToolRegistry(enabled_tools=["compute", "conclude"])
    descriptions = registry.describe_visible_tools()
    names = {d.name for d in descriptions}
    assert "compute" in names
    assert "conclude" in names
    assert all(d.category == "builtin" for d in descriptions)


@pytest.mark.asyncio
async def test_progressive_disclosure_shows_relevant_mcp_first():
    server = McpServerConfig(name="math-suite", transport="stdio", command="python", args=["-m", "suite"])
    client = McpClient([server])

    fake_session = FakeSession(
        list_tools_result=FakeListToolsResult(
            tools=[
                FakeTool("integrate", "Symbolic integration with SageMath", {"type": "object", "properties": {"expr": {"type": "string"}}}),
                FakeTool("fetch_paper", "Fetch an arXiv paper", {"type": "object", "properties": {"id": {"type": "string"}}}),
            ]
        ),
        call_tool_result=FakeCallResult([FakeTextContent("ok")]),
    )

    with patch("mcp.ClientSession", return_value=fake_session):
        with patch("mcp.client.stdio.stdio_client", fake_stdio_client):
            with patch("mcp.StdioServerParameters"):
                await client.initialize()

    registry = ToolRegistry(enabled_tools=["compute"], mcp_client=client)

    # Step 0 with a calculus problem: only integration MCP should be surfaced.
    visible = registry.describe_visible_tools(context="integrate x^2", step=0, mcp_top_k=1)
    visible_names = {d.name for d in visible}
    assert "mcp_math_suite_integrate" in visible_names
    assert "mcp_math_suite_fetch_paper" not in visible_names

    # Step 1: all MCP tools become visible.
    visible = registry.describe_visible_tools(context="integrate x^2", step=1)
    visible_names = {d.name for d in visible}
    assert "mcp_math_suite_integrate" in visible_names
    assert "mcp_math_suite_fetch_paper" in visible_names

    # Non-progressive mode shows everything on step 0.
    visible = registry.describe_visible_tools(context="integrate x^2", step=0, progressive=False)
    visible_names = {d.name for d in visible}
    assert "mcp_math_suite_fetch_paper" in visible_names


def test_format_tool_list_includes_args_and_description():
    registry = ToolRegistry(enabled_tools=["compute"])
    descriptions = registry.describe_visible_tools()
    text = registry.format_tool_list(descriptions)
    assert "compute({\"code\": \"...\"})" in text
    assert "restricted Python sandbox" in text


def test_parse_mcp_servers_rejects_duplicate_names():
    raw = [
        {"name": "sagemath", "transport": "stdio", "command": "sagemath-mcp"},
        {"name": "sagemath", "transport": "stdio", "command": "other"},
    ]
    with pytest.raises(ValueError, match="Duplicate MCP server name"):
        parse_mcp_servers(raw)


def test_parse_mcp_servers_rejects_unsupported_transport():
    raw = [{"name": "bad", "transport": "ws", "command": "x"}]
    with pytest.raises(ValueError, match="Unsupported MCP transport"):
        parse_mcp_servers(raw)


def test_parse_mcp_servers_requires_url_for_http_transports():
    raw = [{"name": "remote", "transport": "sse", "command": "x"}]
    with pytest.raises(ValueError, match="url"):
        parse_mcp_servers(raw)


def test_parse_mcp_servers_expands_env_values(monkeypatch):
    monkeypatch.setenv("MCP_SECRET", "42")
    raw = [{"name": "s", "transport": "stdio", "command": "cmd", "env": {"KEY": "$MCP_SECRET"}}]
    servers = parse_mcp_servers(raw)
    assert servers[0].env == {"KEY": "42"}


def test_parse_mcp_servers_parses_timeout_and_headers():
    raw = [
        {
            "name": "s",
            "transport": "stdio",
            "command": "cmd",
            "tool_timeout_seconds": 120.0,
            "read_timeout_seconds": 15.0,
            "headers": {"Authorization": "Bearer x"},
        }
    ]
    servers = parse_mcp_servers(raw)
    assert servers[0].tool_timeout_seconds == 120.0
    assert servers[0].read_timeout_seconds == 15.0
    assert servers[0].headers == {"Authorization": "Bearer x"}


@pytest.mark.asyncio
async def test_call_tool_times_out_and_returns_error():
    server = McpServerConfig(
        name="slow", transport="stdio", command="cmd", tool_timeout_seconds=0.05
    )
    client = McpClient([server])

    class SlowSession(FakeSession):
        async def call_tool(self, name: str, arguments: dict[str, Any]) -> FakeCallResult:
            await asyncio.sleep(10.0)
            return FakeCallResult([FakeTextContent("too late")])

    fake_session = SlowSession(
        list_tools_result=FakeListToolsResult(tools=[FakeTool("execute")]),
        call_tool_result=FakeCallResult([]),
    )

    with patch("mcp.ClientSession", return_value=fake_session):
        with patch("mcp.client.stdio.stdio_client", fake_stdio_client):
            with patch("mcp.StdioServerParameters"):
                await client.initialize()

    result = await client.call_tool("mcp_slow_execute", {})
    assert "timed out" in result.error.lower()
    assert result.success is False


def test_call_result_struct_has_success_flag():
    from math_agent.agent.mcp_client import McpToolResult
    result = McpToolResult(output="ok", success=True)
    assert result.success is True


@pytest.mark.asyncio
async def test_call_tool_returns_structured_error_on_failure():
    server = McpServerConfig(name="bad", transport="stdio", command="cmd")
    client = McpClient([server])
    fake_session = FakeSession(
        list_tools_result=FakeListToolsResult(tools=[FakeTool("execute")]),
        call_tool_result=FakeCallResult([FakeTextContent("boom")]),
    )
    with patch("mcp.ClientSession", return_value=fake_session):
        with patch("mcp.client.stdio.stdio_client", fake_stdio_client):
            with patch("mcp.StdioServerParameters"):
                await client.initialize()

    # Server crash / disconnected
    client._sessions["bad"] = None
    result = await client.call_tool("mcp_bad_execute", {})
    assert isinstance(result, McpToolResult)
    assert result.success is False
    assert "not connected" in result.error.lower()


@pytest.mark.asyncio
async def test_call_tool_marks_is_error_result_as_failure():
    server = McpServerConfig(name="srv", transport="stdio", command="cmd")
    client = McpClient([server])

    class ErrorResult:
        isError = True
        content = [FakeTextContent("division by zero")]

    fake_session = FakeSession(
        list_tools_result=FakeListToolsResult(tools=[FakeTool("execute")]),
        call_tool_result=ErrorResult(),
    )
    with patch("mcp.ClientSession", return_value=fake_session):
        with patch("mcp.client.stdio.stdio_client", fake_stdio_client):
            with patch("mcp.StdioServerParameters"):
                await client.initialize()

    result = await client.call_tool("mcp_srv_execute", {})
    assert isinstance(result, McpToolResult)
    assert result.success is False
    assert result.is_error is True
    assert "division by zero" in result.output


@pytest.mark.asyncio
async def test_call_tool_reconnects_after_connection_error():
    server = McpServerConfig(name="repl", transport="stdio", command="cmd")
    client = McpClient([server])

    first = True
    class ReconnectSession(FakeSession):
        async def call_tool(self, name: str, arguments: dict[str, Any]) -> FakeCallResult:
            nonlocal first
            if first:
                first = False
                raise ConnectionResetError("closed")
            return FakeCallResult([FakeTextContent("recovered")])

    fake_session = ReconnectSession(
        list_tools_result=FakeListToolsResult(tools=[FakeTool("execute")]),
        call_tool_result=FakeCallResult([]),
    )
    with patch("mcp.ClientSession", return_value=fake_session):
        with patch("mcp.client.stdio.stdio_client", fake_stdio_client):
            with patch("mcp.StdioServerParameters"):
                await client.initialize()

    result = await client.call_tool("mcp_repl_execute", {})
    assert result.success is True
    assert result.output == "recovered"
    assert client.health["repl"]["status"] == "connected"


@pytest.mark.asyncio
async def test_call_tool_marks_unavailable_after_reconnect_failure():
    server = McpServerConfig(name="repl", transport="stdio", command="cmd")
    client = McpClient([server])

    class FailingSession(FakeSession):
        async def call_tool(self, name: str, arguments: dict[str, Any]) -> FakeCallResult:
            raise ConnectionResetError("closed")

    fake_session = FailingSession(
        list_tools_result=FakeListToolsResult(tools=[FakeTool("execute")]),
        call_tool_result=FakeCallResult([]),
    )
    with patch("mcp.ClientSession", return_value=fake_session):
        with patch("mcp.client.stdio.stdio_client", fake_stdio_client):
            with patch("mcp.StdioServerParameters"):
                await client.initialize()

    result = await client.call_tool("mcp_repl_execute", {})
    assert result.success is False
    assert client.health["repl"]["status"] == "unavailable"


@pytest.mark.asyncio
async def test_sse_client_receives_headers():
    server = McpServerConfig(
        name="remote",
        transport="sse",
        url="http://localhost:3000/sse",
        headers={"X-Api-Key": "secret"},
    )
    client = McpClient([server])

    captured: dict[str, Any] = {}
    @asynccontextmanager
    async def capturing_sse_client(url: str, headers: dict[str, str] | None = None):
        captured["url"] = url
        captured["headers"] = headers
        yield (MagicMock(), MagicMock())

    fake_session = FakeSession(
        list_tools_result=FakeListToolsResult(tools=[FakeTool("lookup")]),
        call_tool_result=FakeCallResult([FakeTextContent("ok")]),
    )
    with patch("mcp.ClientSession", return_value=fake_session):
        with patch("mcp.client.sse.sse_client", capturing_sse_client):
            await client.initialize()

    assert captured.get("headers") == {"X-Api-Key": "secret"}
    assert "mcp_remote_lookup" in client.tools


@pytest.mark.asyncio
async def test_streamable_http_transport_is_rejected_without_newer_sdk():
    server = McpServerConfig(
        name="remote",
        transport="streamable-http",
        url="http://localhost:3000/mcp",
    )
    client = McpClient([server])

    # If SDK does not provide streamablehttp_client, initialize should mark unavailable.
    await client.initialize()
    assert client.health["remote"]["status"] == "unavailable"


@pytest.mark.asyncio
async def test_concurrent_calls_to_stateful_server_are_serialized():
    server = McpServerConfig(name="repl", transport="stdio", command="cmd")
    client = McpClient([server])

    active = 0
    max_active = 0

    class CountingSession(FakeSession):
        async def call_tool(self, name: str, arguments: dict[str, Any]) -> FakeCallResult:
            nonlocal active, max_active
            active += 1
            max_active = max(max_active, active)
            await asyncio.sleep(0.05)
            active -= 1
            return FakeCallResult([FakeTextContent("ok")])

    fake_session = CountingSession(
        list_tools_result=FakeListToolsResult(tools=[FakeTool("execute")]),
        call_tool_result=FakeCallResult([]),
    )
    with patch("mcp.ClientSession", return_value=fake_session):
        with patch("mcp.client.stdio.stdio_client", fake_stdio_client):
            with patch("mcp.StdioServerParameters"):
                await client.initialize()

    results = await asyncio.gather(
        client.call_tool("mcp_repl_execute", {}),
        client.call_tool("mcp_repl_execute", {}),
    )
    assert all(r.success for r in results)
    assert max_active == 1
