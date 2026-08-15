"""Tool registry: builtin/MCP/plugin registration, dispatch, stats, logging.

Imports from ``math_agent.agent.*`` are deferred (TYPE_CHECKING or call time)
because ``math_agent.agent.__init__`` eagerly imports the ReAct agent, which
imports this package back — a runtime top-level import would be circular.
"""
from __future__ import annotations

import json
import logging
import re
import time
from typing import TYPE_CHECKING, Any

from math_agent.config import AgentConfig, SearchConfig
from math_agent.tools.builtin import (
    _add_material_tool,
    _compute_tool,
    _fetch_url_tool,
    _find_related_tool,
    _formalize_tool,
    _formalize_unavailable_tool,
    _lean_check_tool,
    _lean_check_unavailable_tool,
    _plot_figure_tool,
    _prove_by_lemmas_tool,
    _prove_by_lemmas_unavailable_tool,
    _read_sources_tool,
    _relate_knowledge_tool,
    _search_arxiv_tool,
    _search_knowledge_adapter,
    _search_materials_tool,
    _search_mathlib_tool,
    _search_scholar_tool,
    _search_tool,
    _searching_tool,
    _tactic_search_tool,
    _tactic_search_unavailable_tool,
)
from math_agent.tools.context import ToolArgMap, ToolContext, ToolFn
from math_agent.tools.results import ToolDescription, ToolResult

if TYPE_CHECKING:
    from math_agent.agent.mcp_client import McpClient
    from math_agent.agent.react_state import Action, ToolObservation
    from math_agent.lean.codegen import LeanCodegen
    from math_agent.lean.premise_retriever import PremiseRetriever
    from math_agent.lean.runner import LeanRunner

log = logging.getLogger("math_agent.tools")


_BUILTIN_ARG_MAPS: dict[str, ToolArgMap] = {
    "compute": "code",
    "search": "query",
    "search_arxiv": "query",
    "search_scholar": "query",
    "fetch_url": "url",
    "searching": "query",
    "read_sources": "prompt",
    "add_material": "text",
    "search_materials": "query",
    "search_knowledge": "query",
    "relate_knowledge": "spec",
    "find_related": "item_id",
    "formalize": "statement",
    "lean_check": ("code", "draft"),
    "search_mathlib": "query",
    "plot_figure": ("code", "caption"),
    "tactic_search": ("theorem_statement", "max_attempts"),
    "prove_by_lemmas": ("statement", "lemmas"),
}

_SPECIAL_ACTIONS = frozenset({"think", "set_goal", "conclude", "update_plan"})


def _params(
    properties: dict[str, Any], required: list[str]
) -> dict[str, Any]:
    return {"type": "object", "properties": properties, "required": required}


def _str_prop(description: str = "") -> dict[str, Any]:
    prop: dict[str, Any] = {"type": "string"}
    if description:
        prop["description"] = description
    return prop


# Static JSON-schema parameter tables for the built-in ReAct actions, used
# when the backend speaks native function calling. Keyed by the action name
# the model sees (the search tool is disclosed as "search_web").
_NATIVE_PARAMETER_SCHEMAS: dict[str, dict[str, Any]] = {
    "think": _params({"text": _str_prop("internal reasoning, no side effects")}, ["text"]),
    "set_goal": _params(
        {
            "goal": _str_prop("the sub-goal to create or activate"),
            "goal_id": _str_prop("optional stable id for the goal"),
            "depends_on": {
                "type": "array",
                "items": {"type": "string"},
                "description": "optional goal ids this goal depends on",
            },
        },
        ["goal"],
    ),
    "conclude": _params(
        {
            "answer": _str_prop("the complete final answer"),
            "evidence_id": _str_prop(
                "optional Formal evidence ID supporting the answer (required for formal runs)"
            ),
        },
        ["answer"],
    ),
    "update_plan": _params(
        {
            "items": {
                "type": "array",
                "description": "the full todo checklist (max 20 items, replaces the current one)",
                "items": {
                    "type": "object",
                    "properties": {
                        "content": _str_prop("task description"),
                        "status": {
                            "type": "string",
                            "enum": ["pending", "in_progress", "done"],
                        },
                    },
                    "required": ["content", "status"],
                },
            }
        },
        ["items"],
    ),
    "compute": _params(
        {"code": _str_prop("Python code; use print; math/sympy/numpy allowed")},
        ["code"],
    ),
    "search_web": _params({"query": _str_prop()}, ["query"]),
    "search_arxiv": _params({"query": _str_prop()}, ["query"]),
    "search_scholar": _params({"query": _str_prop()}, ["query"]),
    "fetch_url": _params({"url": _str_prop("public URL to fetch")}, ["url"]),
    "read_sources": _params(
        {"prompt": _str_prop("prompt containing URLs/arXiv ids to fetch and store")},
        ["prompt"],
    ),
    "add_material": _params({"text": _str_prop()}, ["text"]),
    "search_materials": _params({"query": _str_prop()}, ["query"]),
    "search_knowledge": _params({"query": _str_prop()}, ["query"]),
    "relate_knowledge": _params(
        {"spec": _str_prop("from_id,to_id,relation")}, ["spec"]
    ),
    "find_related": _params({"item_id": _str_prop()}, ["item_id"]),
    "searching": _params({"query": _str_prop()}, ["query"]),
    "plot_figure": _params(
        {
            "code": _str_prop("matplotlib code; do NOT call savefig/show"),
            "caption": _str_prop("short figure caption"),
        },
        ["code"],
    ),
    "formalize": _params(
        {"statement": _str_prop("mathematical statement and proof sketch")},
        ["statement"],
    ),
    "lean_check": _params(
        {
            "code": _str_prop("Lean 4 code to type-check"),
            "draft": {
                "type": "boolean",
                "description": "true for cheap partial checks that allow `sorry` holes",
            },
            "declaration": _str_prop("optional theorem name to check"),
        },
        ["code"],
    ),
    "tactic_search": _params(
        {
            "theorem_statement": _str_prop(),
            "max_attempts": {
                "type": "integer",
                "description": "bound on tactic search attempts",
            },
        },
        ["theorem_statement"],
    ),
    "prove_by_lemmas": _params(
        {
            "statement": _str_prop("mathematical statement and proof sketch"),
            "lemmas": _str_prop(
                "optional JSON array of your own lemma decomposition: "
                '[{"name": "...", "statement": "...", "proof_hint": "...", "depends_on": []}]'
                ". Each `statement` must be a Lean 4 proposition only -- the part "
                "that would follow the colon in `lemma name : <statement>`. Write "
                "Lean notation, not English: `∀ B : ℕ, B < 10 → ...`, never "  # noqa: RUF001 -- intentional Lean notation
                "`For B less than 10, ...`. Omit the `lemma`/`theorem` keyword, "
                "the name, and any `:= by` proof."
            ),
        },
        ["statement"],
    ),
    "search_mathlib": _params(
        {"query": _str_prop("exact mathlib4 declaration name or type snippet")},
        ["query"],
    ),
}


_BUILTIN_DESCRIPTIONS: dict[str, ToolDescription] = {
    "think": ToolDescription(
        name="think",
        args='{"text": "..."}',
        description="internal reasoning with no tool call",
    ),
    "search_web": ToolDescription(
        name="search_web",
        args='{"query": "..."}',
        description="real web search via Tavily (requires TAVILY_API_KEY; falls back to DuckDuckGo)",
    ),
    "search_arxiv": ToolDescription(
        name="search_arxiv",
        args='{"query": "..."}',
        description=(
            "search arXiv for math/CS papers by keywords; returns titles, "
            "authors, abstracts and arXiv IDs (pass an arXiv ID or URL to "
            "`read_sources` to fetch the full text). Prefer this over "
            "search_web for literature questions."
        ),
    ),
    "search_scholar": ToolDescription(
        name="search_scholar",
        args='{"query": "..."}',
        description=(
            "search Semantic Scholar for academic papers by keywords; "
            "returns titles, authors, abstracts, citation counts and links. "
            "Prefer this over search_web for literature questions."
        ),
    ),
    "fetch_url": ToolDescription(
        name="fetch_url",
        args='{"url": "..."}',
        description="fetch the actual content of a public URL and return a cleaned text snippet",
    ),
    "read_sources": ToolDescription(
        name="read_sources",
        args='{"prompt": "..."}',
        description="fetch URLs/arXiv from a prompt and store the extracted text as project materials",
    ),
    "add_material": ToolDescription(
        name="add_material",
        args='{"text": "..."}',
        description="add a raw text snippet to the project materials",
    ),
    "search_materials": ToolDescription(
        name="search_materials",
        args='{"query": "..."}',
        description="search project materials by keyword",
    ),
    "search_knowledge": ToolDescription(
        name="search_knowledge",
        args='{"query": "..."}',
        description="search the project's JSONL-backed facts, intuitions, and techniques",
    ),
    "relate_knowledge": ToolDescription(
        name="relate_knowledge",
        args='{"spec": "from_id,to_id,relation"}',
        description="record a relationship between two knowledge items (relation: implies/generalizes/specializes/uses/related/contradicts)",
    ),
    "find_related": ToolDescription(
        name="find_related",
        args='{"item_id": "..."}',
        description="find items related to a knowledge entry",
    ),
    "searching": ToolDescription(
        name="searching",
        args='{"query": "..."}',
        description="fallback scholarly lookup when Tavily search is unavailable or empty",
    ),
    "compute": ToolDescription(
        name="compute",
        args='{"code": "..."}',
        description=(
            "restricted Python sandbox for calculation, verification, and search "
            "(use print; math/sympy/numpy/re/json allowed; urllib may fetch "
            "public http(s) URLs — private/loopback addresses are blocked)"
        ),
    ),
    "plot_figure": ToolDescription(
        name="plot_figure",
        args='{"code": "...", "caption": "..."}',
        description=(
            "create a figure with matplotlib to illustrate the answer "
            "(geometry, function plots, diagrams, data charts) when a picture "
            "would help the user understand — and ALWAYS when the user "
            "explicitly asks for a picture/diagram (画图/图解/示意图). Build "
            "the plot with matplotlib.pyplot; do NOT call savefig/show — "
            "figures are saved automatically. The result contains a markdown "
            "image link; embed that link verbatim on its own line in the "
            "final answer."
        ),
    ),
    "formalize": ToolDescription(
        name="formalize",
        args='{"statement": "..."}',
        description="generate Lean 4 code from a mathematical STATEMENT and its proof sketch (short English or formula). This is the right action when you need to PROVE a new lemma or theorem.",
    ),
    "lean_check": ToolDescription(
        name="lean_check",
        args='{"code": "...", "draft": "optional true", "declaration": "optional theorem name"}',
        description=(
            "type-check EXISTING Lean 4 code. Set draft=true for cheap, FREQUENT partial checks while developing a proof: "
            "skeletons with `sorry` holes are type-checked and reported as DRAFT OK (not a proof). "
            "Omit draft for the final strict check (no `sorry` allowed) — only a strict PASSED result counts as proof evidence."
        ),
    ),
    "tactic_search": ToolDescription(
        name="tactic_search",
        args='{"theorem_statement": "...", "imports": "import Mathlib.X (optional, precise imports only)", "max_attempts": 32}',
        description="deep proof search: explores tactic sequences against the live Lean goal state. Use this when one-shot `formalize`/`lean_check` repair has failed on a goal. Pass the precise `import ...` lines from the formalization in `imports` (never the umbrella `import Mathlib`).",
    ),
    "prove_by_lemmas": ToolDescription(
        name="prove_by_lemmas",
        args='{"statement": "...", "lemmas": "optional JSON array of {name, statement, proof_hint, depends_on}"}',
        description=(
            "the primary route for hard theorems: decompose the statement into lemmas (blueprint-style), "
            "prove and verify each lemma in Lean 4 one at a time, then assemble the final theorem over the "
            "verified lemmas. Prefer this whenever one-shot `formalize` keeps failing or the proof needs "
            "multiple lemmas. You may pass your own lemma decomposition via `lemmas`; otherwise one is "
            "planned automatically. Each lemma `statement` must be a bare Lean 4 proposition in Lean "
            "notation (what follows the colon in `lemma name : ...`), not an English sentence."
        ),
    ),
    "search_mathlib": ToolDescription(
        name="search_mathlib",
        args='{"query": "..."}',
        description="search mathlib4 for an EXACT declaration name or type snippet; best for known identifiers like 'Nat.dvd_gcd' or 'IsSelfAdjoint'. If it returns nothing, the result likely does not exist in mathlib4.",
    ),
    "set_goal": ToolDescription(
        name="set_goal",
        args='{"goal": "...", "goal_id": "optional", "depends_on": ["goal-id"]}',
        description="create or activate a proof-DAG sub-goal with optional dependencies",
    ),
    "conclude": ToolDescription(
        name="conclude",
        args='{"answer": "...", "evidence_id": "optional formal-..."}',
        description="final answer; formal runs must bind the supporting Formal evidence ID",
    ),
    "update_plan": ToolDescription(
        name="update_plan",
        args='{"items": [{"content": "...", "status": "pending|in_progress|done"}]}',
        description="replace the todo checklist for multi-step tasks (max 20 items); the current list is shown in the context every step",
    ),
}


def _rank_by_relevance(
    descriptions: list[ToolDescription], context: str
) -> list[ToolDescription]:
    """Rank MCP tool descriptions by simple keyword overlap with ``context``."""
    if not context:
        return descriptions

    def _tokens(text: str) -> set[str]:
        return set(re.findall(r"[a-zA-Z][a-zA-Z0-9_]*", text.lower()))

    context_tokens = _tokens(context)
    if not context_tokens:
        return descriptions

    def score(desc: ToolDescription) -> int:
        text = f"{desc.name} {desc.description}"
        return len(_tokens(text) & context_tokens)

    return sorted(descriptions, key=score, reverse=True)


class ToolRegistry:
    def __init__(
        self,
        *,
        enabled_tools: list[str] | None = None,
        lean_runner: LeanRunner | None = None,
        lean_codegen: LeanCodegen | None = None,
        premise_retriever: PremiseRetriever | None = None,
        llm: object | None = None,
        material_store: object | None = None,
        knowledge_store: object | None = None,
        knowledge_graph: object | None = None,
        knowledge_config: object | None = None,
        mcp_client: McpClient | None = None,
        agent_config: AgentConfig | None = None,
        search_config: SearchConfig | None = None,
        prover_llm: object | None = None,
        critic_llm: object | None = None,
    ) -> None:
        self._lean_runner = lean_runner
        self._lean_codegen = lean_codegen
        self._premise_retriever = premise_retriever
        self._repl_pool = None
        if lean_runner is not None:
            from math_agent.config import LeanConfig
            from math_agent.lean.repl_session import LeanReplPool

            config = getattr(lean_runner, "config", None)
            if isinstance(config, LeanConfig) and LeanReplPool.available(config):
                self._repl_pool = LeanReplPool(config)
        self._llm = llm
        self._material_store = material_store
        self._knowledge_store = knowledge_store
        self._knowledge_graph = knowledge_graph
        self._knowledge_config = knowledge_config
        self._mcp_client = mcp_client
        self._agent_config = agent_config
        self._search_config = search_config
        self._prover_llm = prover_llm
        self._critic_llm = critic_llm
        from math_agent.lean.proof_trace_memory import ProofTraceMemory

        self._trace_memory = ProofTraceMemory()
        self._tool_stats: dict[str, dict[str, Any]] = {}
        self._tools: dict[str, ToolFn] = {}
        self._descriptions: dict[str, ToolDescription] = {}
        self._arg_maps: dict[str, ToolArgMap] = {}
        self._mcp_tools: set[str] = set()
        self._mcp_schemas: dict[str, dict[str, Any]] = {}
        self._register_builtins(["compute"] if enabled_tools is None else enabled_tools)
        self._register_mcp_tools()

    def _register_builtins(self, enabled_tools: list[str]) -> None:
        builtins: dict[str, ToolFn] = {
            "compute": _compute_tool,
            "search": _search_tool,
            "search_arxiv": _search_arxiv_tool,
            "search_scholar": _search_scholar_tool,
            "fetch_url": _fetch_url_tool,
            "searching": _searching_tool,
            "read_sources": _read_sources_tool,
            "add_material": _add_material_tool,
            "search_materials": _search_materials_tool,
            "search_knowledge": _search_knowledge_adapter,
            "relate_knowledge": _relate_knowledge_tool,
            "find_related": _find_related_tool,
            "search_mathlib": _search_mathlib_tool,
            "plot_figure": _plot_figure_tool,
        }
        if self._lean_codegen is not None:
            builtins["formalize"] = _formalize_tool
        elif "formalize" in enabled_tools:
            builtins["formalize"] = _formalize_unavailable_tool
        if self._lean_runner is not None:
            builtins["lean_check"] = _lean_check_tool
        elif "lean_check" in enabled_tools:
            builtins["lean_check"] = _lean_check_unavailable_tool
        if self._lean_runner is not None and self._llm is not None:
            builtins["tactic_search"] = _tactic_search_tool
        elif "tactic_search" in enabled_tools:
            builtins["tactic_search"] = _tactic_search_unavailable_tool
        if self._lean_runner is not None and self._llm is not None:
            builtins["prove_by_lemmas"] = _prove_by_lemmas_tool
        elif "prove_by_lemmas" in enabled_tools:
            builtins["prove_by_lemmas"] = _prove_by_lemmas_unavailable_tool

        for name in enabled_tools:
            fn = builtins.get(name)
            if fn is not None:
                self._tools[name] = fn
                self._arg_maps[name] = _BUILTIN_ARG_MAPS[name]
                description_key = "search_web" if name == "search" else name
                description = _BUILTIN_DESCRIPTIONS.get(description_key)
                if description is not None:
                    self._descriptions[name] = description
            else:
                log.warning("Unknown tool in config, skipping: %s", name)

    def _register_mcp_tools(self) -> None:
        if self._mcp_client is None:
            return
        for conjecta_name, info in self._mcp_client.tools.items():
            definition = info["definition"]
            mcp_name = definition.get("name", conjecta_name)
            description = definition.get("description", "")
            input_schema = definition.get("input_schema", {}) or {}
            args_example = self._schema_to_args_example(input_schema)
            log.info("Registering MCP tool: %s (%s)", conjecta_name, description[:60])
            self._tools[conjecta_name] = self._make_mcp_tool_fn(conjecta_name)
            self._mcp_tools.add(conjecta_name)
            if isinstance(input_schema, dict):
                self._mcp_schemas[conjecta_name] = input_schema
            properties = input_schema.get("properties", {})
            self._arg_maps[conjecta_name] = (
                tuple(str(key) for key in properties)
                if isinstance(properties, dict)
                else ()
            )
            self._descriptions[conjecta_name] = ToolDescription(
                name=conjecta_name,
                args=args_example,
                description=description
                or f"MCP tool '{mcp_name}' from server '{info.get('server_name', 'unknown')}'",
                category="mcp",
            )

    @staticmethod
    def _schema_to_args_example(schema: dict[str, Any]) -> str:
        """Convert a JSON schema into a compact args example for the prompt."""
        properties = schema.get("properties", {})
        if not properties:
            return "{}"
        parts = []
        for key, prop in properties.items():
            prop_type = (
                prop.get("type", "string") if isinstance(prop, dict) else "string"
            )
            example = "..."
            if prop_type == "number":
                example = "0"
            elif prop_type == "boolean":
                example = "true"
            elif prop_type == "array":
                example = "[...]"
            elif prop_type == "object":
                example = "{}"
            parts.append(f'"{key}": {example}')
        return "{" + ", ".join(parts) + "}"

    def _make_mcp_tool_fn(self, conjecta_name: str) -> ToolFn:
        async def _mcp_tool_wrapper(args_json: str, _ctx: ToolContext) -> ToolResult:
            if self._mcp_client is None:
                return ToolResult(
                    name=conjecta_name,
                    output="MCP client is not available",
                    success=False,
                )
            result = await self._mcp_client.call_tool_from_json(
                conjecta_name, args_json
            )
            output = (
                result.output if result.success else (result.error or result.output)
            )
            return ToolResult(
                name=conjecta_name,
                output=output,
                success=result.success,
            )

        return _mcp_tool_wrapper

    def register(
        self,
        name: str,
        fn: ToolFn,
        *,
        description: str,
        args_example: str,
        arg_map: ToolArgMap,
    ) -> None:
        """Register an in-process tool as a first-class ReAct action.

        External integrations should use MCP. The ``mcp_`` namespace is
        reserved so a local plugin cannot impersonate an MCP capability.
        """

        normalized = str(name or "").strip()
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", normalized):
            raise ValueError("Tool name must be a valid identifier.")
        if normalized.startswith("mcp_"):
            raise ValueError("The mcp_ tool namespace is reserved for MCP tools.")
        if normalized in self._tools or normalized in _SPECIAL_ACTIONS:
            raise ValueError(f"Tool {normalized!r} is already registered.")
        if not callable(fn):
            raise TypeError("Tool function must be callable.")
        rendered_description = str(description or "").strip()
        if not rendered_description:
            raise ValueError("Tool description must be non-empty.")
        rendered_args = str(args_example or "").strip()
        if not rendered_args:
            raise ValueError("Tool args_example must be non-empty.")
        normalized_arg_map = self._validate_arg_map(arg_map)

        self._tools[normalized] = fn
        self._arg_maps[normalized] = normalized_arg_map
        self._descriptions[normalized] = ToolDescription(
            name=normalized,
            args=rendered_args,
            description=rendered_description,
            category="plugin",
        )

    @staticmethod
    def _validate_arg_map(arg_map: ToolArgMap) -> ToolArgMap:
        if isinstance(arg_map, str):
            value = arg_map.strip()
            if not value:
                raise ValueError("Tool arg_map string must be non-empty.")
            return value
        if isinstance(arg_map, tuple) and arg_map:
            values = tuple(str(item).strip() for item in arg_map)
            if any(not item for item in values) or len(values) != len(set(values)):
                raise ValueError("Tool arg_map fields must be non-empty and unique.")
            return values
        raise ValueError("Tool arg_map must be a string or a non-empty tuple.")

    def argument_map(self, name: str) -> ToolArgMap | None:
        canonical = {"search_web": "search", "web_fetch": "fetch_url"}.get(name, name)
        return self._arg_maps.get(canonical)

    async def call(
        self,
        name: str,
        args: str,
        ctx: ToolContext | None = None,
    ) -> ToolResult:
        fn = self._tools.get(name)
        if fn is None:
            log.warning("Unknown tool requested: %s", name)
            return ToolResult(name=name, output=f"Unknown tool: {name}", success=False)
        # Structured metadata only: tool payloads may contain user problems
        # and private data, so nothing derived from args/output is ever
        # logged (CodeQL py/clear-text-logging).
        log.info("Tool call start: name=%s", name)
        tool_ctx = self._context_with_defaults(ctx)
        started = time.monotonic()
        failed = False
        try:
            result = await fn(args, tool_ctx)
            failed = not result.success
            elapsed_ms = round((time.monotonic() - started) * 1000)
            log.info(
                "Tool call done: name=%s status=%s duration_ms=%d output_chars=%d",
                name,
                "ok" if result.success else "error",
                elapsed_ms,
                len(result.output),
            )
            return result
        except Exception:
            failed = True
            log.exception(
                "Tool call failed: name=%s error_code=TOOL_EXECUTION_FAILED", name
            )
            return ToolResult(
                name=name,
                output=f"Error: tool {name} failed to execute.",
                success=False,
            )
        finally:
            # Stats always update (calls/wall time), even when the tool raises
            # or the call is cancelled (CancelledError propagates through here).
            elapsed = time.monotonic() - started
            stats = self._tool_stats.setdefault(
                name, {"calls": 0, "failures": 0, "wall_seconds": 0.0}
            )
            stats["calls"] += 1
            stats["failures"] += 1 if failed else 0
            stats["wall_seconds"] = round(stats["wall_seconds"] + elapsed, 6)

    @property
    def tool_stats(self) -> dict[str, dict[str, Any]]:
        """Live per-tool usage: calls, failures, cumulative wall seconds."""
        return {name: dict(stats) for name, stats in self._tool_stats.items()}

    @property
    def lean_runner(self) -> LeanRunner | None:
        return self._lean_runner

    @property
    def lean_codegen(self) -> LeanCodegen | None:
        return self._lean_codegen

    @property
    def llm(self) -> object | None:
        return self._llm

    @property
    def material_store(self) -> object | None:
        return self._material_store

    @property
    def knowledge_store(self) -> object | None:
        return self._knowledge_store

    @property
    def knowledge_graph(self) -> object | None:
        return self._knowledge_graph

    async def execute_action(
        self,
        action: Action,
        ctx: ToolContext | None = None,
    ) -> ToolObservation:
        from math_agent.agent.react_state import ToolObservation

        name = action.name
        args = action.args
        tool_ctx = self._context_with_defaults(ctx)

        # Legacy / alternative action names.
        name_aliases = {
            "search_web": "search",
            "web_fetch": "fetch_url",
        }
        name = name_aliases.get(name, name)

        if name in self._mcp_tools:
            tool_result = await self.call(name, json.dumps(args), tool_ctx)
            return ToolObservation(
                success=tool_result.success,
                output=tool_result.output,
                lean_code=tool_result.lean_code,
            )

        arg_map = self._arg_maps.get(name)
        if arg_map is None:
            return ToolObservation(
                success=False,
                output=f"Unknown action: {name}",
                error="unknown_action",
            )

        if isinstance(arg_map, tuple):
            tool_result = await self.call(
                name,
                json.dumps({k: args.get(k, "") for k in arg_map}),
                tool_ctx,
            )
        else:
            tool_result = await self.call(name, args.get(arg_map, ""), tool_ctx)
        return ToolObservation(
            success=tool_result.success,
            output=tool_result.output,
            lean_code=tool_result.lean_code,
        )

    def _context_with_defaults(self, ctx: ToolContext | None) -> ToolContext:
        if ctx is None:
            return ToolContext(
                lean_runner=self._lean_runner,
                lean_codegen=self._lean_codegen,
                premise_retriever=self._premise_retriever,
                repl_pool=self._repl_pool,
                material_store=self._material_store,
                knowledge_store=self._knowledge_store,
                knowledge_graph=self._knowledge_graph,
                knowledge_config=self._knowledge_config,
                agent_config=self._agent_config,
                search_config=self._search_config,
                prover_llm=self._prover_llm,
                critic_llm=self._critic_llm,
                trace_memory=self._trace_memory,
            )
        return ToolContext(
            state=ctx.state,
            lean_runner=ctx.lean_runner or self._lean_runner,
            lean_codegen=ctx.lean_codegen or self._lean_codegen,
            premise_retriever=ctx.premise_retriever or self._premise_retriever,
            repl_pool=ctx.repl_pool or self._repl_pool,
            prover_llm=ctx.prover_llm or self._prover_llm,
            critic_llm=ctx.critic_llm or self._critic_llm,
            trace_memory=ctx.trace_memory or self._trace_memory,
            project_context=ctx.project_context,
            llm=ctx.llm or self._llm,
            material_store=ctx.material_store or self._material_store,
            knowledge_store=ctx.knowledge_store or self._knowledge_store,
            knowledge_graph=ctx.knowledge_graph or self._knowledge_graph,
            knowledge_config=ctx.knowledge_config or self._knowledge_config,
            agent_config=ctx.agent_config or self._agent_config,
            search_config=ctx.search_config or self._search_config,
            figure_dir=ctx.figure_dir,
            figure_url_prefix=ctx.figure_url_prefix,
            formalization_plan=ctx.formalization_plan,
            event_callback=ctx.event_callback,
            session_log=ctx.session_log,
        )

    @property
    def available(self) -> list[str]:
        return list(self._tools.keys())

    @property
    def capability_health(self) -> dict[str, dict[str, Any]]:
        if self._mcp_client is None:
            return {}
        return self._mcp_client.health

    def describe_visible_tools(
        self,
        context: str = "",
        step: int = 0,
        *,
        progressive: bool = True,
        mcp_top_k: int = 3,
        allowed_names: frozenset[str] | None = None,
    ) -> list[ToolDescription]:
        """Return tool descriptions that should be visible to the LLM at this step.

        Built-in tools are always visible. MCP tools are disclosed progressively:
        - On step 0, only the top-k MCP tools most relevant to ``context`` are shown.
        - On later steps, all MCP tools are shown so the model can adapt.

        This keeps the first prompt focused while still letting the model decide
        whether to call an MCP tool once it becomes visible.
        """
        visible = [
            _BUILTIN_DESCRIPTIONS[name]
            for name in ("think", "set_goal", "conclude", "update_plan")
        ]
        visible.extend(
            description
            for name, description in self._descriptions.items()
            if name not in self._mcp_tools
            and (allowed_names is None or name in allowed_names)
        )
        mcp_descriptions = [
            desc for desc in self._descriptions.values() if desc.category == "mcp"
        ]
        if allowed_names is not None:
            mcp_descriptions = [
                description
                for description in mcp_descriptions
                if description.name in allowed_names
            ]

        if not mcp_descriptions:
            return visible

        if not progressive or step > 0:
            return visible + mcp_descriptions

        # Step 0 progressive disclosure: surface only the most relevant MCP tools.
        ranked = _rank_by_relevance(mcp_descriptions, context)
        return visible + ranked[:mcp_top_k]

    def format_tool_list(self, descriptions: list[ToolDescription]) -> str:
        """Format a list of tool descriptions for inclusion in a system prompt."""
        lines = [
            f"  - {desc.name}({desc.args}): {desc.description}" for desc in descriptions
        ]
        return "\n".join(lines)

    def native_tool_schemas(
        self, descriptions: list[ToolDescription]
    ) -> list[dict[str, Any]]:
        """Build OpenAI-style tool schemas for native function calling.

        Built-in actions use the static parameter table; MCP tools pass their
        own JSON schema through unchanged. Plugin tools without a table entry
        fall back to a schema derived from their argument map.
        """
        schemas: list[dict[str, Any]] = []
        for desc in descriptions:
            parameters: dict[str, Any]
            if desc.category == "mcp":
                parameters = self._mcp_parameters(desc.name)
            else:
                schema = _NATIVE_PARAMETER_SCHEMAS.get(desc.name)
                parameters = (
                    schema
                    if schema is not None
                    else self._parameters_from_arg_map(desc.name)
                )
            schemas.append(
                {
                    "type": "function",
                    "function": {
                        "name": desc.name,
                        "description": desc.description,
                        "parameters": parameters,
                    },
                }
            )
        return schemas

    def _mcp_parameters(self, name: str) -> dict[str, Any]:
        schema = self._mcp_schemas.get(name)
        if not isinstance(schema, dict) or not schema:
            return {"type": "object", "properties": {}}
        parameters = dict(schema)
        parameters.setdefault("type", "object")
        parameters.setdefault("properties", {})
        return parameters

    def _parameters_from_arg_map(self, name: str) -> dict[str, Any]:
        arg_map = self.argument_map(name)
        if isinstance(arg_map, tuple):
            keys = list(arg_map)
        elif isinstance(arg_map, str):
            keys = [arg_map]
        else:
            keys = []
        properties = {key: _str_prop() for key in keys}
        # Mirror _validate_action: the first listed argument is required.
        required = keys[:1]
        return {"type": "object", "properties": properties, "required": required}
