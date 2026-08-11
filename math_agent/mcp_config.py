from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Any


@dataclass
class McpServerConfig:
    """Configuration for a single MCP server connection.

    Supports three transports:
    - ``stdio``: spawn a local process and communicate over stdin/stdout.
    - ``sse``: connect to a remote server via Server-Sent Events over HTTP.
    - ``streamable-http``: connect to a remote server over HTTP POST streams.
    """

    name: str
    transport: str = "stdio"
    command: str | None = None
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    url: str | None = None
    headers: dict[str, str] = field(default_factory=dict)
    tool_timeout_seconds: float = 60.0
    read_timeout_seconds: float | None = 30.0
    serialize_calls: bool = True


def _expand(value: str) -> str:
    """Expand environment variables and ``~`` in a string value."""
    return os.path.expanduser(os.path.expandvars(str(value)))


def parse_mcp_servers(raw: Any) -> list[McpServerConfig]:
    """Parse the ``mcp_servers`` section from the raw TOML config.

    Accepts either a list of tables (``[[mcp_servers]]``) or a single table.
    Unknown fields are ignored so the config stays forward-compatible.

    Raises:
        ValueError: if server names are duplicated, the transport is unsupported,
            or an HTTP transport is missing a valid ``url``.
    """
    if raw is None:
        return []

    if isinstance(raw, dict):
        raw = [raw]

    valid_transports = {"stdio", "sse", "streamable-http"}
    seen_names: set[str] = set()
    servers: list[McpServerConfig] = []

    for entry in raw:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name")
        if not name:
            continue

        name = str(name)
        if name in seen_names:
            raise ValueError(f"Duplicate MCP server name: {name}")
        seen_names.add(name)

        transport = str(entry.get("transport", "stdio")).lower()
        if transport not in valid_transports:
            raise ValueError(
                f"Unsupported MCP transport '{transport}' for server '{name}'"
            )

        url = entry.get("url")
        if transport in {"sse", "streamable-http"}:
            if not url or not str(url).startswith(("http://", "https://")):
                raise ValueError(
                    f"MCP server '{name}' with transport '{transport}' requires a valid http:// or https:// url"
                )

        env = entry.get("env") or {}
        if not isinstance(env, dict):
            env = {}

        env_key = "CONJECTA_MCP_" + re.sub(
            r"[^A-Za-z0-9]", "_", name
        ).upper() + "_COMMAND"
        raw_command = os.environ.get(env_key) or entry.get("command")
        command = _expand(raw_command) if raw_command else None
        args = [_expand(value) for value in entry.get("args", [])]

        headers = entry.get("headers") or {}
        if not isinstance(headers, dict):
            headers = {}

        tool_timeout_seconds = float(
            entry.get("tool_timeout_seconds", 60.0)
        )
        raw_read_timeout = entry.get("read_timeout_seconds", 30.0)
        read_timeout_seconds = float(raw_read_timeout) if raw_read_timeout is not None else None
        serialize_calls = bool(entry.get("serialize_calls", True))

        servers.append(
            McpServerConfig(
                name=name,
                transport=transport,
                command=command,
                args=args,
                env={str(k): _expand(v) for k, v in env.items()},
                url=url,
                headers={str(k): str(v) for k, v in headers.items()},
                tool_timeout_seconds=tool_timeout_seconds,
                read_timeout_seconds=read_timeout_seconds,
                serialize_calls=serialize_calls,
            )
        )

    return servers
