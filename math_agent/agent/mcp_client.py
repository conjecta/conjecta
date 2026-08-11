from __future__ import annotations

import asyncio
import json
import logging
from contextlib import AsyncExitStack
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from math_agent.mcp_config import McpServerConfig

log = logging.getLogger("math_agent.mcp_client")


@dataclass
class McpToolResult:
    output: str = ""
    success: bool = True
    is_error: bool = False
    error: str | None = None


def _mcp_tool_name(server_name: str, tool_name: str) -> str:
    """Return the Conjecta-visible name for an MCP tool.

    The prefix prevents collisions with built-in tools and makes the origin
    explicit to the agent and to human operators. Dots and dashes are
    normalized to underscores so the resulting name is a valid action token.
    """
    sanitized_server = server_name.replace(" ", "_").replace("-", "_").replace(".", "_")
    sanitized_tool = tool_name.replace(" ", "_").replace("-", "_").replace(".", "_")
    return f"mcp_{sanitized_server}_{sanitized_tool}"


def _tool_definition(mcp_tool: Any) -> dict[str, Any]:
    """Convert an MCP tool definition into a Conjecta tool definition."""
    return {
        "name": mcp_tool.name,
        "description": getattr(mcp_tool, "description", "") or "",
        "input_schema": getattr(mcp_tool, "inputSchema", {}) or {},
    }


class McpClient:
    """Client that discovers and calls tools from configured MCP servers.

    The client is designed to be created once at application startup and shared
    by the ``ToolRegistry``. It keeps sessions alive for the lifetime of the
    process so that stateful MCP servers (e.g. those keeping an in-memory REPL)
    remain usable across tool calls.
    """

    def __init__(self, servers: list[McpServerConfig]) -> None:
        self._servers = servers
        self._server_stacks: dict[str, AsyncExitStack] = {}
        self._sessions: dict[str, Any] = {}
        self._tool_index: dict[str, tuple[str, dict[str, Any]]] = {}
        self._mcp_imports: dict[str, Any] = {}
        self._server_configs: dict[str, McpServerConfig] = {
            server.name: server for server in servers
        }
        self._server_locks: dict[str, asyncio.Lock] = {
            server.name: asyncio.Lock() for server in servers
        }
        self._health: dict[str, dict[str, Any]] = {
            server.name: {"status": "not_initialized", "error": "", "tool_count": 0}
            for server in servers
        }

    async def initialize(self) -> None:
        """Connect to all configured servers and discover their tools."""
        try:
            from mcp import ClientSession, StdioServerParameters
            from mcp.client.sse import sse_client
            from mcp.client.stdio import stdio_client
            try:
                from mcp.client.streamablehttp import (  # type: ignore[import-not-found]
                    streamablehttp_client,
                )
            except ImportError:
                streamablehttp_client = None  # type: ignore[assignment]
        except ImportError as exc:  # pragma: no cover - dependency guard
            log.warning("MCP Python SDK not installed: %s", exc)
            for server in self._servers:
                self._health[server.name] = {
                    "status": "unavailable",
                    "error": f"ImportError: {exc}",
                    "tool_count": 0,
                }
            return

        self._mcp_imports = {
            "ClientSession": ClientSession,
            "StdioServerParameters": StdioServerParameters,
            "stdio_client": stdio_client,
            "sse_client": sse_client,
            "streamablehttp_client": streamablehttp_client,
        }

        for server in self._servers:
            try:
                session = await self._connect_server(
                    server,
                    ClientSession,
                    StdioServerParameters,
                    stdio_client,
                    sse_client,
                    streamablehttp_client,
                )
                if session is None:
                    self._health[server.name] = {
                        "status": "unavailable",
                        "error": "Connection returned no session",
                        "tool_count": 0,
                    }
                    continue

                result = await session.list_tools()
                tools = getattr(result, "tools", []) or []
                for tool in tools:
                    conjecta_name = _mcp_tool_name(server.name, tool.name)
                    self._tool_index[conjecta_name] = (server.name, _tool_definition(tool))
                    log.info(
                        "Discovered MCP tool: %s (from server %s)",
                        conjecta_name,
                        server.name,
                    )
                self._sessions[server.name] = session
                self._health[server.name] = {
                    "status": "connected",
                    "error": "",
                    "tool_count": len(tools),
                }
            except Exception as exc:
                log.warning("Failed to initialize MCP server %s: %s", server.name, exc)
                self._health[server.name] = {
                    "status": "unavailable",
                    "error": f"{type(exc).__name__}: {exc}",
                    "tool_count": 0,
                }

    async def _connect_server(
        self,
        server: McpServerConfig,
        ClientSession: Any,
        StdioServerParameters: Any,
        stdio_client: Any,
        sse_client: Any,
        streamablehttp_client: Any | None,
    ) -> Any | None:
        if server.transport not in ("stdio", "sse", "streamable-http"):
            log.warning("Unsupported MCP transport for server %s: %s", server.name, server.transport)
            return None

        stack = AsyncExitStack()
        try:
            if server.transport == "stdio":
                if not server.command:
                    log.warning("MCP server %s uses stdio but has no command", server.name)
                    await stack.aclose()
                    return None
                params = StdioServerParameters(
                    command=server.command,
                    args=server.args,
                    env=server.env or None,
                )
                transport = await stack.enter_async_context(stdio_client(params))
                read, write = transport[0], transport[1]
            elif server.transport == "sse":
                if not server.url:
                    log.warning("MCP server %s uses sse but has no url", server.name)
                    await stack.aclose()
                    return None
                transport = await stack.enter_async_context(
                    sse_client(server.url, headers=server.headers)
                )
                read, write = transport[0], transport[1]
            elif server.transport == "streamable-http":
                if not server.url:
                    log.warning("MCP server %s uses streamable-http but has no url", server.name)
                    await stack.aclose()
                    return None
                if streamablehttp_client is None:
                    log.warning(
                        "MCP server %s uses streamable-http but the installed MCP SDK does not support it",
                        server.name,
                    )
                    await stack.aclose()
                    return None
                transport = await stack.enter_async_context(
                    streamablehttp_client(server.url, headers=server.headers)
                )
                read, write = transport[0], transport[1]

            read_timeout = (
                timedelta(seconds=server.read_timeout_seconds)
                if server.read_timeout_seconds is not None
                else None
            )
            session = await stack.enter_async_context(
                ClientSession(read, write, read_timeout_seconds=read_timeout)
            )
            await session.initialize()
            self._server_stacks[server.name] = stack
            return session
        except Exception:
            await stack.aclose()
            raise

    async def _reconnect_server(self, server_name: str) -> bool:
        """Reconnect a single server after a connection error.

        Closes the old per-server stack, establishes a new session, and
        rebuilds the tool index entries for this server.
        """
        server = self._server_configs.get(server_name)
        if server is None:
            return False

        self._health[server_name] = {
            "status": "reconnecting",
            "error": "",
            "tool_count": 0,
        }

        old_stack = self._server_stacks.pop(server_name, None)
        if old_stack is not None:
            try:
                await old_stack.aclose()
            except Exception as exc:
                log.warning(
                    "Error closing old MCP server stack for %s: %s", server_name, exc
                )

        imports = self._mcp_imports
        ClientSession = imports.get("ClientSession")
        StdioServerParameters = imports.get("StdioServerParameters")
        stdio_client = imports.get("stdio_client")
        sse_client = imports.get("sse_client")
        streamablehttp_client = imports.get("streamablehttp_client")
        if not all((ClientSession, StdioServerParameters, stdio_client, sse_client)):
            try:
                from mcp import ClientSession as _ClientSession
                from mcp import StdioServerParameters as _StdioServerParameters
                from mcp.client.sse import sse_client as _sse_client
                from mcp.client.stdio import stdio_client as _stdio_client
                try:
                    from mcp.client.streamablehttp import (  # type: ignore[import-not-found]
                        streamablehttp_client as _streamablehttp_client,
                    )
                except ImportError:
                    _streamablehttp_client = None  # type: ignore[assignment]
                ClientSession = _ClientSession
                StdioServerParameters = _StdioServerParameters
                stdio_client = _stdio_client
                sse_client = _sse_client
                streamablehttp_client = _streamablehttp_client
                imports = {
                    "ClientSession": ClientSession,
                    "StdioServerParameters": StdioServerParameters,
                    "stdio_client": stdio_client,
                    "sse_client": sse_client,
                    "streamablehttp_client": streamablehttp_client,
                }
            except ImportError as exc:  # pragma: no cover - dependency guard
                log.warning("MCP Python SDK not installed: %s", exc)
                self._health[server_name] = {
                    "status": "unavailable",
                    "error": f"ImportError: {exc}",
                    "tool_count": 0,
                }
                return False

        try:
            session = await self._connect_server(
                server,
                ClientSession,
                StdioServerParameters,
                stdio_client,
                sse_client,
                streamablehttp_client,
            )
            if session is None:
                self._health[server_name] = {
                    "status": "unavailable",
                    "error": "Connection returned no session",
                    "tool_count": 0,
                }
                self._sessions.pop(server_name, None)
                return False

            result = await session.list_tools()
            tools = getattr(result, "tools", []) or []
            for conjecta_name in list(self._tool_index):
                if self._tool_index[conjecta_name][0] == server_name:
                    del self._tool_index[conjecta_name]
            for tool in tools:
                conjecta_name = _mcp_tool_name(server_name, tool.name)
                self._tool_index[conjecta_name] = (
                    server_name,
                    _tool_definition(tool),
                )

            self._sessions[server_name] = session
            self._health[server_name] = {
                "status": "connected",
                "error": "",
                "tool_count": len(tools),
            }
            return True
        except Exception as exc:
            log.warning("Failed to reconnect MCP server %s: %s", server_name, exc)
            self._health[server_name] = {
                "status": "unavailable",
                "error": f"{type(exc).__name__}: {exc}",
                "tool_count": 0,
            }
            self._sessions.pop(server_name, None)
            return False

    @property
    def tools(self) -> dict[str, dict[str, Any]]:
        """Return discovered tools keyed by Conjecta-visible name.

        Each value contains ``server_name`` and ``definition`` keys.
        """
        return {
            name: {"server_name": server_name, "definition": definition}
            for name, (server_name, definition) in self._tool_index.items()
        }

    @property
    def health(self) -> dict[str, dict[str, Any]]:
        """Return a copy of per-server connection health for UI and routing."""
        return {name: dict(value) for name, value in self._health.items()}

    async def call_tool(self, conjecta_name: str, arguments: dict[str, Any]) -> McpToolResult:
        """Call an MCP tool and return a structured result.

        Calls to stateful servers are serialized with per-server asyncio locks
        when ``serialize_calls`` is enabled (the default), so that overlapping
        requests cannot corrupt an in-memory REPL or other server-side state.
        """
        if conjecta_name not in self._tool_index:
            return McpToolResult(success=False, error=f"Unknown MCP tool: {conjecta_name}")

        server_name, _ = self._tool_index[conjecta_name]
        server_config = self._server_configs.get(server_name)
        lock = self._server_locks.get(server_name)
        if lock is not None and (server_config is None or server_config.serialize_calls):
            async with lock:
                return await self._call_tool_unlocked(conjecta_name, arguments)
        return await self._call_tool_unlocked(conjecta_name, arguments)

    async def _call_tool_unlocked(
        self, conjecta_name: str, arguments: dict[str, Any]
    ) -> McpToolResult:
        """Internal call implementation; callers must hold the server lock when serializing."""
        server_name, definition = self._tool_index[conjecta_name]
        session = self._sessions.get(server_name)
        if session is None:
            return McpToolResult(
                success=False, error=f"MCP server {server_name} is not connected"
            )

        server_config = self._server_configs.get(server_name)
        timeout = server_config.tool_timeout_seconds if server_config else 60.0

        async def _call_with_timeout(sess: Any) -> Any:
            return await asyncio.wait_for(
                sess.call_tool(definition["name"], arguments=arguments),
                timeout=timeout,
            )

        try:
            result = await _call_with_timeout(session)
        except asyncio.TimeoutError:
            # Must precede the connection-error clause: since Python 3.11,
            # asyncio.TimeoutError aliases builtin TimeoutError, an OSError.
            log.warning("MCP tool call timed out: %s", conjecta_name)
            return McpToolResult(
                success=False, error=f"MCP tool call timed out after {timeout}s"
            )
        except (ConnectionResetError, BrokenPipeError, ConnectionError, OSError) as exc:
            log.warning(
                "MCP server %s connection error during %s: %s",
                server_name,
                conjecta_name,
                exc,
            )
            self._health[server_name] = {
                "status": "disconnected",
                "error": f"{type(exc).__name__}: {exc}",
                "tool_count": 0,
            }
            reconnected = await self._reconnect_server(server_name)
            if not reconnected:
                return McpToolResult(
                    success=False,
                    error=f"MCP server {server_name} disconnected and reconnect failed",
                )
            session = self._sessions.get(server_name)
            if session is None:
                return McpToolResult(
                    success=False,
                    error=f"MCP server {server_name} reconnected but session missing",
                )
            try:
                result = await _call_with_timeout(session)
            except asyncio.TimeoutError:
                log.warning("MCP tool call timed out: %s", conjecta_name)
                return McpToolResult(
                    success=False, error=f"MCP tool call timed out after {timeout}s"
                )
            except (ConnectionResetError, BrokenPipeError, ConnectionError, OSError) as exc2:
                log.warning(
                    "MCP server %s still disconnected during retry of %s: %s",
                    server_name,
                    conjecta_name,
                    exc2,
                )
                self._health[server_name] = {
                    "status": "unavailable",
                    "error": f"{type(exc2).__name__}: {exc2}",
                    "tool_count": 0,
                }
                return McpToolResult(
                    success=False,
                    error=f"MCP server {server_name} disconnected and reconnect failed",
                )
            except Exception as exc2:
                log.exception("MCP tool call failed: %s", conjecta_name)
                return McpToolResult(
                    success=False, error=f"MCP tool call failed: {exc2}"
                )
        except Exception as exc:
            log.exception("MCP tool call failed: %s", conjecta_name)
            return McpToolResult(success=False, error=f"MCP tool call failed: {exc}")

        if getattr(result, "isError", False):
            return McpToolResult(
                output=self._format_result(result),
                success=False,
                is_error=True,
                error="MCP tool returned isError=True",
            )

        return McpToolResult(output=self._format_result(result))

    def _format_result(self, result: Any) -> str:
        """Flatten an MCP CallToolResult into a string observation."""
        content = getattr(result, "content", None)
        if content is None:
            return ""

        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, str):
                    parts.append(item)
                else:
                    text = getattr(item, "text", None)
                    if text is not None:
                        parts.append(str(text))
                    else:
                        parts.append(str(item))
            return "\n".join(parts)

        if isinstance(content, str):
            return content

        return str(content)

    async def call_tool_from_json(self, conjecta_name: str, args_json: str) -> McpToolResult:
        """Parse a JSON string of arguments and call the tool."""
        try:
            arguments = json.loads(args_json) if args_json else {}
        except json.JSONDecodeError as exc:
            return McpToolResult(
                success=False,
                error=f"Invalid JSON arguments for {conjecta_name}: {exc}",
            )
        if not isinstance(arguments, dict):
            return McpToolResult(
                success=False,
                error=f"MCP tool arguments must be a JSON object, got {type(arguments).__name__}",
            )
        return await self.call_tool(conjecta_name, arguments)

    async def close(self) -> None:
        """Close all MCP sessions.

        Acquires each server lock before closing its stack so that in-flight
        tool calls or reconnections finish before their transport is torn down.
        Re-reads and pops the stack from ``_server_stacks`` inside the critical
        section to avoid leaking a stack created concurrently by a reconnect.
        """
        for server_name in list(self._server_stacks):
            lock = self._server_locks.get(server_name)
            try:
                if lock is not None:
                    async with lock:
                        stack = self._server_stacks.pop(server_name, None)
                        if stack is not None:
                            await stack.aclose()
                else:
                    stack = self._server_stacks.pop(server_name, None)
                    if stack is not None:
                        await stack.aclose()
            except Exception as exc:
                log.warning("Error closing MCP server stack: %s", exc)

        self._sessions.clear()
        self._tool_index.clear()
