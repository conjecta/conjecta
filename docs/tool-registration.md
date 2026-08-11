# Extending tools

Conjecta has three tool extension paths. Choose the narrowest one that matches
the ownership boundary.

## Built-in tool

Use a built-in for a capability maintained in this repository and required by
the default product. Add its implementation, description, argument mapping,
configuration name, and tests in `math_agent/agent/tools.py`.

## MCP tool

Use MCP for an external service, executable, or independently deployed tool.
MCP discovery supplies the description and JSON schema, and Conjecta reserves
the `mcp_` action namespace for these tools. This is the preferred third-party
extension boundary.

## In-process registered tool

Use `ToolRegistry.register()` for an application-owned capability assembled at
runtime. Registration is immediately reflected in the ReAct prompt, action
validation, and dispatch path:

```python
from math_agent.agent.tools import ToolContext, ToolRegistry, ToolResult

async def double(value: str, _ctx: ToolContext) -> ToolResult:
    return ToolResult(name="double", output=str(int(value) * 2), success=True)

registry = ToolRegistry(enabled_tools=[])
registry.register(
    "double",
    double,
    description="double an integer",
    args_example='{"value": "21"}',
    arg_map="value",
)
```

Use a string `arg_map` when the function consumes one raw string. Use a tuple,
such as `("query", "limit")`, when it consumes a JSON object; the first field
is required and later fields are optional. Names and descriptions are
validated, duplicates are rejected, and `mcp_` names are forbidden. Registered
functions share the same exception handling and `ToolContext` defaults as
built-ins.
