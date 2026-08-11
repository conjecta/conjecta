from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Awaitable

from math_agent.agent.mcp_client import McpClient
from math_agent.agent.react_state import Action, ProjectContext, ToolObservation
from math_agent.agent.state import ReasoningState
from math_agent.config import AgentConfig, SearchConfig
from math_agent.lean.mathlib_search import default_search
from math_agent.net_safety import UnsafeFetchURL, fetch_public_url
from math_agent.tools.lean import check_lean_code, formalize_statement
from math_agent.tools.plot_sandbox import run_plot
from math_agent.tools.python_sandbox import run_python

if TYPE_CHECKING:
    from math_agent.lean.codegen import LeanCodegen
    from math_agent.lean.premise_retriever import PremiseRetriever
    from math_agent.lean.runner import LeanRunner

log = logging.getLogger("math_agent.tools")

_LOG_PREVIEW = 500


@dataclass
class ToolResult:
    name: str
    output: str
    success: bool
    lean_code: str | None = None


@dataclass
class ToolDescription:
    """Data-driven description of a tool exposed to the LLM.

    Keeping descriptions in the registry removes the need to hardcode tool
    lists in prompts and lets us disclose MCP tools progressively.
    """

    name: str
    args: str
    description: str
    category: str = "builtin"  # "builtin", "plugin", or "mcp"


@dataclass
class ToolContext:
    state: ReasoningState | None = None
    lean_runner: LeanRunner | None = None
    lean_codegen: LeanCodegen | None = None
    premise_retriever: PremiseRetriever | None = None
    repl_pool: object | None = None  # math_agent.lean.repl_session.LeanReplPool
    # Optional formal-prover role backend (tactic generation, proof writing);
    # falls back to `llm` when unset.
    prover_llm: object | None = None
    # Optional critic backend for tactic candidate re-ranking.
    critic_llm: object | None = None
    # Verified-proof trace memory (proof flywheel).
    trace_memory: object | None = None
    project_context: ProjectContext | None = None
    llm: object | None = None
    material_store: object | None = None
    knowledge_store: object | None = None
    knowledge_graph: object | None = None
    knowledge_config: object | None = None
    agent_config: AgentConfig | None = None
    search_config: SearchConfig | None = None
    # Per-solve figure output directory for the plot_figure tool, plus the URL
    # prefix used to reference saved figures from the final answer (web mode).
    figure_dir: Path | None = None
    figure_url_prefix: str | None = None
    # Unified-planner Lean sketch (FormalizationPlan as a plain dict) drafted
    # up front; the formalize tool feeds it to the coder as guidance.
    formalization_plan: dict[str, Any] | None = None
    # Optional async callback for long-running tools to report user-facing
    # progress lines (surfaced as tool_progress events on the running card).
    event_callback: Callable[[str], Awaitable[None]] | None = None
    # Per-solve logger so long-running tools (e.g. prove_by_lemmas) can write
    # diagnostics into the session log instead of dropping them.
    session_log: logging.Logger | None = None


# Tool type: async function taking a string arg, returning string
ToolFn = Callable[[str, ToolContext], Awaitable[ToolResult]]
ToolArgMap = str | tuple[str, ...]


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
                "Lean notation, not English: `∀ B : ℕ, B < 10 → ...`, never "
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
        log.info("Tool call start: name=%s args=%s", name, args[:_LOG_PREVIEW])
        tool_ctx = self._context_with_defaults(ctx)
        started = time.monotonic()
        try:
            result = await fn(args, tool_ctx)
            elapsed = time.monotonic() - started
            stats = self._tool_stats.setdefault(
                name, {"calls": 0, "failures": 0, "wall_seconds": 0.0}
            )
            stats["calls"] += 1
            stats["failures"] += 0 if result.success else 1
            stats["wall_seconds"] = round(stats["wall_seconds"] + elapsed, 6)
            log.info(
                "Tool call done: name=%s success=%s output_chars=%d preview=%s",
                name,
                result.success,
                len(result.output),
                result.output[:_LOG_PREVIEW],
            )
            return result
        except Exception as e:
            log.exception(
                "Tool call failed: name=%s args=%s", name, args[:_LOG_PREVIEW]
            )
            return ToolResult(name=name, output=f"Error: {e}", success=False)

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
            if desc.category == "mcp":
                parameters = self._mcp_parameters(desc.name)
            else:
                parameters = _NATIVE_PARAMETER_SCHEMAS.get(desc.name)
                if parameters is None:
                    parameters = self._parameters_from_arg_map(desc.name)
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


async def _compute_tool(code: str, _ctx: ToolContext) -> ToolResult:
    log.debug("Compute code received chars=%d", len(code))
    result = await run_python(code)
    return ToolResult(name="compute", output=result.output, success=result.success)


def _safe_caption(text: str) -> str:
    """Keep a caption from breaking the markdown image syntax."""
    cleaned = text.replace("[", "(").replace("]", ")").strip()
    return cleaned[:120] or "figure"


async def _plot_figure_tool(args_json: str, ctx: ToolContext) -> ToolResult:
    try:
        payload = json.loads(args_json) if args_json.strip() else {}
    except json.JSONDecodeError:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    code = str(payload.get("code") or "")
    caption = _safe_caption(str(payload.get("caption") or ""))
    log.debug("Plot figure code received chars=%d", len(code))

    if ctx.figure_dir is None:
        return ToolResult(
            name="plot_figure",
            output="plot_figure is unavailable: no figure directory is configured for this session.",
            success=False,
        )

    result = await run_plot(code, out_dir=ctx.figure_dir)
    if not result.success:
        return ToolResult(name="plot_figure", output=result.output, success=False)

    embeds: list[str] = []
    for name in result.figures:
        if ctx.figure_url_prefix:
            embeds.append(f"![{caption}]({ctx.figure_url_prefix}/{name})")
        else:
            embeds.append(f"{ctx.figure_dir / name}")
    embed_block = "\n".join(embeds)
    output = (
        f"Figure saved ({len(result.figures)} image(s)). "
        "Embed it in the final answer verbatim on its own line:\n"
        f"{embed_block}"
    )
    if result.output:
        output += f"\n[plot output]\n{result.output}"
    return ToolResult(name="plot_figure", output=output, success=True)


def _search_max_results(ctx: ToolContext | None) -> int | None:
    if ctx is not None and ctx.search_config is not None:
        return ctx.search_config.max_results
    return None


def _search_fallback_enabled(ctx: ToolContext | None) -> bool:
    if ctx is not None and ctx.search_config is not None:
        return ctx.search_config.fallback_provider != "none"
    return True


async def _search(
    query: str,
    *,
    max_results: int | None = None,
    use_fallback: bool = True,
) -> str:
    """Web search: Tavily first, DuckDuckGo as fallback."""
    from math_agent.search.duckduckgo import (
        duckduckgo_search,
        is_duckduckgo_failure_message,
    )
    from math_agent.search.tavily import is_tavily_failure_message, tavily_search

    kwargs = {"max_results": max_results} if max_results else {}
    output = await tavily_search(query, **kwargs)
    if not use_fallback or not is_tavily_failure_message(output):
        return output
    ddg_output = await duckduckgo_search(query, **kwargs)
    if is_duckduckgo_failure_message(ddg_output):
        # Both providers failed; report the primary provider's error.
        return output
    return f"[web search via DuckDuckGo]\n{ddg_output}"


async def _search_tool(query: str, ctx: ToolContext) -> ToolResult:
    from math_agent.search.tavily import is_tavily_failure_message

    output = await _search(
        query,
        max_results=_search_max_results(ctx),
        use_fallback=_search_fallback_enabled(ctx),
    )
    return ToolResult(
        name="search",
        output=output,
        success=not is_tavily_failure_message(output),
    )


async def _search_arxiv_tool(query: str, ctx: ToolContext) -> ToolResult:
    from math_agent.search.arxiv import arxiv_search, is_arxiv_failure_message

    max_results = _search_max_results(ctx)
    kwargs = {"max_results": max_results} if max_results else {}
    output = await arxiv_search(query, **kwargs)
    return ToolResult(
        name="search_arxiv",
        output=output,
        success=not is_arxiv_failure_message(output),
    )


async def _search_scholar_tool(query: str, ctx: ToolContext) -> ToolResult:
    from math_agent.search.semantic_scholar import (
        is_scholar_failure_message,
        scholar_search,
    )

    max_results = _search_max_results(ctx)
    kwargs = {"max_results": max_results} if max_results else {}
    output = await scholar_search(query, **kwargs)
    return ToolResult(
        name="search_scholar",
        output=output,
        success=not is_scholar_failure_message(output),
    )


async def _fetch_url_tool(url: str, _ctx: ToolContext) -> ToolResult:
    """Fetch URL content and return a readable text snippet."""
    from math_agent.source_fetch import extract_html_text

    raw_url = url.strip()
    headers = {"User-Agent": "ConjectaMathAgent/0.1 (+tool fetch_url)"}
    try:
        resp = await fetch_public_url(
            raw_url,
            timeout_seconds=12.0,
            headers=headers,
            max_bytes=2 * 1024 * 1024,
        )
    except UnsafeFetchURL as exc:
        return ToolResult(name="fetch_url", output=str(exc), success=False)
    except Exception as exc:
        return ToolResult(
            name="fetch_url", output=f"Fetch failed: {exc}", success=False
        )

    content_type = (resp.headers.get("content-type") or "").lower()
    if "text/html" in content_type:
        text = extract_html_text(resp.text)
    else:
        text = resp.text.strip()

    if not text:
        return ToolResult(
            name="fetch_url",
            output=f"Fetched {resp.url}, but content was empty.",
            success=False,
        )

    max_chars = 3500
    snippet = text[:max_chars]
    if len(text) > max_chars:
        snippet += " ... [truncated]"
    return ToolResult(
        name="fetch_url", output=f"Source: {resp.url}\n{snippet}", success=True
    )


async def _read_sources_tool(prompt: str, ctx: ToolContext) -> ToolResult:
    from math_agent.source_fetch import fetch_sources_from_prompt

    project_id = (
        ctx.project_context.project_id if ctx.project_context else None
    ) or "default"
    store = ctx.material_store
    if store is None:
        return ToolResult(
            name="read_sources", output="Material store not available.", success=False
        )

    sources = await fetch_sources_from_prompt(prompt, max_chars=60_000)
    if not sources:
        return ToolResult(
            name="read_sources",
            output="No sources found or fetched from the prompt.",
            success=True,
        )

    added: list[str] = []
    for src in sources:
        kind = "arxiv" if "arxiv" in src.url.lower() else "url"
        m = store.add(project_id, kind, src.label, src.text, src.url)
        added.append(m.id)

    summary = f"Fetched {len(sources)} source(s). Material IDs: {', '.join(added)}."
    return ToolResult(name="read_sources", output=summary, success=True)


async def _add_material_tool(text: str, ctx: ToolContext) -> ToolResult:
    project_id = (
        ctx.project_context.project_id if ctx.project_context else None
    ) or "default"
    store = ctx.material_store
    if store is None:
        return ToolResult(
            name="add_material", output="Material store not available.", success=False
        )
    m = store.add(project_id, "text", "User-provided material", text, "user")
    return ToolResult(
        name="add_material", output=f"Added material {m.id}.", success=True
    )


async def _search_materials_tool(query: str, ctx: ToolContext) -> ToolResult:
    project_id = (
        ctx.project_context.project_id if ctx.project_context else None
    ) or "default"
    store = ctx.material_store
    if store is None:
        return ToolResult(
            name="search_materials",
            output="Material store not available.",
            success=False,
        )
    results = store.search(project_id, query, limit=10)
    if not results:
        return ToolResult(
            name="search_materials", output="No matching materials.", success=True
        )
    lines = [f"- [{m.kind}] {m.label}\n{m.text[:600]}" for m in results]
    return ToolResult(name="search_materials", output="\n\n".join(lines), success=True)


async def _relate_knowledge_tool(spec: str, ctx: ToolContext) -> ToolResult:
    project_id = (
        ctx.project_context.project_id if ctx.project_context else None
    ) or "default"
    graph = ctx.knowledge_graph
    if graph is None:
        return ToolResult(
            name="relate_knowledge",
            output="Knowledge graph not available.",
            success=False,
        )

    parts = [p.strip() for p in spec.split(",")]
    if len(parts) != 3:
        return ToolResult(
            name="relate_knowledge",
            output="Expected format: from_id,to_id,relation (e.g. fact-1,fact-2,implies)",
            success=False,
        )
    from_id, to_id, relation = parts
    rel = graph.add_relation(from_id, to_id, relation, project_id)
    return ToolResult(
        name="relate_knowledge",
        output=f"Added relation {rel.id}: {from_id} {rel.relation} {to_id}",
        success=True,
    )


async def _find_related_tool(item_id: str, ctx: ToolContext) -> ToolResult:
    project_id = (
        ctx.project_context.project_id if ctx.project_context else None
    ) or "default"
    graph = ctx.knowledge_graph
    if graph is None:
        return ToolResult(
            name="find_related", output="Knowledge graph not available.", success=False
        )
    related = graph.get_related(item_id, project_id)
    if not related:
        return ToolResult(name="find_related", output="No related items.", success=True)
    lines = [f"- {r['from_id']} --{r['relation']}--> {r['to_id']}" for r in related]
    return ToolResult(name="find_related", output="\n".join(lines), success=True)


async def _searching_tool(query: str, ctx: ToolContext) -> ToolResult:
    from math_agent.search.duckduckgo import (
        duckduckgo_search,
        is_duckduckgo_failure_message,
    )
    from math_agent.search.tavily import is_tavily_failure_message, tavily_search

    max_results = _search_max_results(ctx)
    kwargs = {"max_results": max_results} if max_results else {}
    tavily_output = await tavily_search(query, **kwargs)
    if not is_tavily_failure_message(tavily_output):
        return ToolResult(name="searching", output=tavily_output, success=True)
    if _search_fallback_enabled(ctx):
        ddg_output = await duckduckgo_search(query, **kwargs)
        if not is_duckduckgo_failure_message(ddg_output):
            return ToolResult(
                name="searching",
                output=f"[web search via DuckDuckGo]\n{ddg_output}",
                success=True,
            )
    if ctx.llm is None:
        return ToolResult(name="searching", output=tavily_output, success=False)
    output = await _llm_search_content(query, ctx.llm)
    return ToolResult(
        name="searching",
        output=f"[model knowledge, not from live search]\n{output}",
        success=True,
    )


async def _llm_search_content(query: str, llm: object) -> str:
    from math_agent.agent.prompts import LLM_SEARCH_SYSTEM, with_time_context
    from math_agent.llm.base import Message

    q = query.strip()
    if not q:
        return "Search query cannot be empty."
    try:
        response = await llm.complete(  # type: ignore[union-attr]
            [Message(role="user", content=q)],
            system=with_time_context(LLM_SEARCH_SYSTEM),
            temperature=0.0,
        )
        return response.text
    except Exception as exc:
        log.warning("LLM search fallback failed: %s", exc)
        return f"LLM search failed: {exc}"


async def _formalize_unavailable_tool(_statement: str, _ctx: ToolContext) -> ToolResult:
    return ToolResult(
        name="formalize",
        output="Lean formalization unavailable (Lean codegen not configured).",
        success=False,
    )


async def _lean_check_unavailable_tool(
    _lean_code: str, _ctx: ToolContext
) -> ToolResult:
    return ToolResult(
        name="lean_check",
        output="Lean check unavailable (Lean toolchain not configured).",
        success=False,
    )


async def _tactic_search_unavailable_tool(
    _args_str: str, _ctx: ToolContext
) -> ToolResult:
    return ToolResult(
        name="tactic_search",
        output="tactic_search unavailable (Lean runner or LLM not configured).",
        success=False,
    )


async def _formalize_tool(statement: str, ctx: ToolContext) -> ToolResult:
    lean_codegen = ctx.lean_codegen
    if lean_codegen is not None and ctx.premise_retriever is not None:
        from math_agent.lean.codegen import LeanCodegen

        lean_codegen = LeanCodegen(
            llm=lean_codegen.llm,
            runner=lean_codegen.runner,
            config=lean_codegen.config,
            premise_retriever=ctx.premise_retriever,
        )
    output, lean_code = await formalize_statement(
        _statement_with_formalization_plan(statement, ctx.formalization_plan),
        lean_codegen=lean_codegen,
        state=ctx.state,
    )
    success = lean_code is not None and "PASSED" in output
    return ToolResult(
        name="formalize", output=output, success=success, lean_code=lean_code
    )


def _statement_with_formalization_plan(
    statement: str, plan_data: dict[str, Any] | None
) -> str:
    """Append the unified planner's Lean sketch as guidance for the coder."""
    if not plan_data:
        return statement
    from math_agent.agent.planner import FormalizationPlan

    try:
        known = {
            key: value
            for key, value in plan_data.items()
            if key in FormalizationPlan.__dataclass_fields__
        }
        plan = FormalizationPlan(**known)
    except Exception:
        return statement
    if not (
        plan.restatement or plan.goal_type or plan.lemmas or plan.recommended_imports
    ):
        return statement
    return f"{statement}\n\n{plan.to_prompt_block(include_verified_code=False)}"


def _parse_lean_check_args(args_str: str) -> tuple[str, bool]:
    """Parse lean_check args: JSON {"code": ..., "draft": ...} or raw code string."""
    text = str(args_str or "")
    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return text, False
    if not isinstance(parsed, dict):
        return text, False
    code = str(parsed.get("code") or "")
    draft_value = parsed.get("draft", False)
    draft = draft_value is True or (
        isinstance(draft_value, str)
        and draft_value.strip().lower() in {"true", "1", "yes"}
    )
    return code, draft


async def _lean_check_tool(args_str: str, ctx: ToolContext) -> ToolResult:
    lean_code, draft = _parse_lean_check_args(args_str)
    if re.search(r"(?m)^\s*import\s+Mathlib\s*$", lean_code or ""):
        suggestion = ""
        if ctx.premise_retriever is not None:
            try:
                entries = ctx.premise_retriever.retrieve(lean_code, top_k=3)
                modules = sorted(
                    {e.module for e in entries if e.module.startswith("Mathlib.")}
                )
                if modules:
                    suggestion = " Try precise imports such as: " + ", ".join(
                        f"import {m}" for m in modules
                    )
            except Exception:
                pass
        return ToolResult(
            name="lean_check",
            output=(
                "Rejected: the umbrella `import Mathlib` is too heavy for this "
                "host (it does not finish within the solve budget). Use precise "
                "imports instead (e.g. `import Mathlib.Tactic.Common` plus the "
                "specific module)."
                + suggestion
            ),
            success=False,
        )
    output, code = await check_lean_code(
        lean_code, lean_runner=ctx.lean_runner, draft=draft
    )
    success = code is not None and "PASSED" in output
    draft_ok = not success and code is not None and "DRAFT OK" in output
    if not success and not draft_ok:
        output = (
            "The provided Lean 4 code did not pass verification. "
            "If this was brand-new code, consider using the `formalize` action to regenerate it "
            "from a clear statement and proof sketch, then verify the result with `lean_check`. "
            "If repeated formalize/lean_check repair rounds on this item keep failing, switch to "
            "`tactic_search` (single goal) or `prove_by_lemmas` (lemma decomposition) instead of "
            "repairing the same draft again.\n\n"
            + output
        )
    return ToolResult(name="lean_check", output=output, success=success, lean_code=code)


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


def _validate_mathlib_query(query: str) -> str | None:
    """Return an error message if the query is not a valid mathlib search string."""
    q = query.strip()
    if not q:
        return "Empty query. Provide an exact declaration name (e.g. 'Nat.gcd_comm') or a Lean type snippet in ASCII (e.g. 'a + b = b + a')."
    if "`" in q:
        return "Do not use backticks (`) in search_mathlib queries. Use plain identifiers or ASCII type snippets."
    # Only allow printable ASCII plus a small set of Lean-accepted Unicode symbols.
    allowed_unicode = set("→←↔∀∃λΠ∧∨≠≤≥⊔⊓⟨⟩⟦⟧")
    for ch in q:
        if ord(ch) < 128:
            continue
        if ch not in allowed_unicode:
            return (
                f"Invalid character '{ch}' in search_mathlib query. "
                "Use exact declaration names or ASCII type snippets; do not paste full mathematical expressions."
            )
    return None


async def _search_mathlib_tool(query: str, _ctx: ToolContext) -> ToolResult:
    validation_error = _validate_mathlib_query(query)
    if validation_error:
        return ToolResult(name="search_mathlib", output=validation_error, success=False)
    try:
        search = default_search()
        entries = await asyncio.to_thread(search.search_by_name, query, max_results=5)
        if not entries:
            entries = await asyncio.to_thread(
                search.search_by_type_snippet, query, max_results=5
            )
    except Exception as exc:
        return ToolResult(
            name="search_mathlib", output=f"Search failed: {exc}", success=False
        )

    if not entries:
        return ToolResult(
            name="search_mathlib",
            output=(
                f"No mathlib declarations found for: {query}\n\n"
                "This often means the exact result is not in mathlib4. "
                "Proceed by constructing the proof from first principles using `formalize` or `lean_check`."
            ),
            success=True,
        )

    lines = [f"Found {len(entries)} declaration(s):"]
    for e in entries:
        name = e.get("name", "")
        module = e.get("module", "")
        signature = e.get("signature", "") or e.get("type", "")
        lines.append(f"- {name} in {module}")
        if signature:
            lines.append(f"  signature: {signature}")
    return ToolResult(name="search_mathlib", output="\n".join(lines), success=True)


async def _search_knowledge_adapter(query: str, ctx: ToolContext) -> ToolResult:
    """Adapter exposing search_knowledge via the standard ToolFn interface."""
    project_id = ctx.project_context.project_id if ctx.project_context else None
    observation = await _search_knowledge_tool(
        query, project_id, ctx.knowledge_store, knowledge_config=ctx.knowledge_config
    )
    return ToolResult(
        name="search_knowledge",
        output=observation.output,
        success=observation.success,
        lean_code=observation.lean_code,
    )


async def _search_knowledge_tool(
    query: str,
    project_id: str | None,
    knowledge_store: object | None = None,
    *,
    knowledge_config: object | None = None,
) -> ToolObservation:
    if not project_id:
        return ToolObservation(
            success=True,
            output="No project context available; knowledge search skipped.",
        )
    if not query.strip():
        return ToolObservation(
            success=True,
            output="Empty knowledge query; nothing to search.",
        )
    try:
        store = knowledge_store
        if store is None:
            from math_agent.knowledge.supabase import KnowledgeStore

            store = (
                KnowledgeStore(knowledge_config=knowledge_config)
                if knowledge_config is not None
                else KnowledgeStore()
            )
        if not all(
            hasattr(store, method)
            for method in ("search_facts", "search_intuitions", "search_tricks")
        ):
            return ToolObservation(
                success=True,
                output="Knowledge search unavailable for this project store.",
            )
        facts = await asyncio.to_thread(store.search_facts, project_id, query, limit=5)
        intuitions = await asyncio.to_thread(store.search_intuitions, project_id, query, limit=5)
        tricks = await asyncio.to_thread(store.search_tricks, project_id, query, limit=5)
        lines = _format_knowledge_search_results(facts, intuitions, tricks)
        output = "\n".join(lines) if lines else "No relevant knowledge found."
        return ToolObservation(success=True, output=output)
    except Exception as e:
        return ToolObservation(
            success=False,
            output=f"Knowledge search failed: {e}",
            error=str(e),
        )


async def _tactic_search_tool(args_str: str, ctx: ToolContext) -> ToolResult:
    from math_agent.lean.proof_search import ProofSearch, TacticGenerator

    if ctx.lean_runner is None or ctx.llm is None:
        return ToolResult(
            name="tactic_search",
            output="tactic_search unavailable (Lean runner or LLM not configured).",
            success=False,
        )

    try:
        args = json.loads(args_str)
    except json.JSONDecodeError as e:
        return ToolResult(
            name="tactic_search",
            output=f"Invalid JSON args: {e}",
            success=False,
        )

    theorem = str(args.get("theorem_statement", "")).strip()
    if not theorem:
        return ToolResult(
            name="tactic_search",
            output="tactic_search requires a non-empty 'theorem_statement'.",
            success=False,
        )
    imports = str(args.get("imports", "")).strip()
    if re.search(r"(?m)^\s*import\s+Mathlib\s*$", imports):
        return ToolResult(
            name="tactic_search",
            output=(
                "tactic_search rejects the umbrella `import Mathlib` (too heavy "
                "for this host); pass precise imports like `import Mathlib.Algebra...`."
            ),
            success=False,
        )

    # Cheap pre-validation: elaborate the statement once (with a draft `sorry`
    # body) before entering REPL/batch search. A statement that does not parse
    # or elaborate would silently burn the whole search budget; return the
    # error immediately so the model can fix the statement.
    probe_statement = theorem
    if not re.search(r":=\s*by\s*$", probe_statement):
        probe_statement = f"{probe_statement} := by"
    probe_code = f"{imports}\n\n{probe_statement}\n  sorry" if imports else (
        f"{probe_statement}\n  sorry"
    )
    probe = await ctx.lean_runner.check_proof(probe_code, draft=True)
    if not probe.success and probe.failure_kind not in {
        "timeout",
        "lean_unavailable",
    }:
        detail = "\n".join(probe.errors) if probe.errors else probe.output
        return ToolResult(
            name="tactic_search",
            output=(
                "tactic_search rejected the statement: it does not parse/elaborate "
                "in Lean 4. Fix the statement (or its imports) and call "
                f"tactic_search again.\nErrors:\n{detail[:2000]}"
            ),
            success=False,
        )

    config_max_attempts = (
        ctx.agent_config.tactic_search_max_attempts
        if ctx.agent_config is not None
        else 32
    )
    try:
        max_attempts = int(args.get("max_attempts", config_max_attempts))
        if max_attempts < 1:
            max_attempts = config_max_attempts
        else:
            max_attempts = min(max_attempts, config_max_attempts)
    except (ValueError, TypeError):
        max_attempts = config_max_attempts

    config_max_depth = (
        ctx.agent_config.tactic_search_max_depth if ctx.agent_config is not None else 8
    )
    generator = TacticGenerator(
        ctx.prover_llm or ctx.llm,
        max_candidates=3,
        premise_retriever=ctx.premise_retriever,
        trace_memory=ctx.trace_memory,
        critic_llm=ctx.critic_llm,
    )
    search = ProofSearch(
        generator=generator,
        runner=ctx.lean_runner,
        max_attempts=max_attempts,
        max_depth=config_max_depth,
        premise_retriever=ctx.premise_retriever,
        repl_pool=ctx.repl_pool,
        imports=imports,
        trace_memory=ctx.trace_memory,
        progress_callback=ctx.event_callback,
    )

    try:
        wall_seconds = (
            ctx.agent_config.tactic_search_wall_seconds
            if ctx.agent_config is not None
            else 120.0
        )
        result = await asyncio.wait_for(search.search(theorem), timeout=wall_seconds)
    except asyncio.TimeoutError:
        return ToolResult(
            name="tactic_search",
            output="tactic_search timed out before finding a proof.",
            success=False,
        )
    except Exception as e:
        return ToolResult(
            name="tactic_search",
            output=f"tactic_search failed: {e}",
            success=False,
        )

    if result.success:
        return ToolResult(
            name="tactic_search",
            output=f"Proof found after {result.attempts} attempts:\n```lean\n{result.proof}\n```",
            success=True,
            lean_code=result.proof,
        )

    return ToolResult(
        name="tactic_search",
        output=(
            f"No proof found after {result.attempts} attempts. "
            f"Deepest partial proof:\n```lean\n{result.proof}\n```\n"
            f"Error: {result.error}"
        ),
        success=False,
        lean_code=result.proof,
    )


async def _prove_by_lemmas_unavailable_tool(
    _args_str: str, _ctx: ToolContext
) -> ToolResult:
    return ToolResult(
        name="prove_by_lemmas",
        output="prove_by_lemmas unavailable (Lean runner or LLM not configured).",
        success=False,
    )


def _parse_prove_by_lemmas_args(
    args_str: str,
) -> tuple[str, list[dict[str, Any]] | None]:
    """Parse prove_by_lemmas args: JSON {"statement": ..., "lemmas": ...} or a raw statement."""
    text = str(args_str or "")
    statement = ""
    lemmas_raw: Any = None
    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        parsed = None
    if isinstance(parsed, dict):
        statement = str(parsed.get("statement") or "").strip()
        lemmas_raw = parsed.get("lemmas")
    elif isinstance(parsed, str):
        statement = parsed.strip()
    else:
        # Tolerate a raw (non-JSON) statement string.
        statement = text.strip()

    if isinstance(lemmas_raw, str):
        try:
            lemmas_raw = json.loads(lemmas_raw)
        except (json.JSONDecodeError, TypeError):
            lemmas_raw = None
    lemmas: list[dict[str, Any]] | None = None
    if isinstance(lemmas_raw, list):
        lemmas = [item for item in lemmas_raw if isinstance(item, dict)]
        if not lemmas:
            lemmas = None
    return statement, lemmas


def _plan_from_ctx_dict(plan_data: dict[str, Any] | None) -> Any:
    """Parse the unified planner's Lean sketch dict into a FormalizationPlan (or None)."""
    if not plan_data:
        return None
    from math_agent.agent.planner import FormalizationPlan

    try:
        known = {
            key: value
            for key, value in plan_data.items()
            if key in FormalizationPlan.__dataclass_fields__
        }
        return FormalizationPlan(**known)
    except Exception:
        return None


async def _plan_lemma_decomposition(statement: str, ctx: ToolContext) -> Any:
    """Run the FormalizationPlanner to obtain goal_type/imports and a lemma decomposition."""
    from math_agent.agent.planner import FormalizationPlanner

    planner = FormalizationPlanner(ctx.llm)  # type: ignore[arg-type]
    try:
        return await planner.plan(statement)
    except Exception as exc:
        log.warning("prove_by_lemmas planning failed: %s", exc)
        return None


async def _prove_by_lemmas_tool(args_str: str, ctx: ToolContext) -> ToolResult:
    from math_agent.lean.lemma_executor import (
        LemmaDAGExecutor,
        sanitize_lemma_statement,
    )
    from math_agent.tools.lean import append_axioms_line, format_lean_result

    if ctx.lean_runner is None or ctx.llm is None:
        return ToolResult(
            name="prove_by_lemmas",
            output="prove_by_lemmas unavailable (Lean runner or LLM not configured).",
            success=False,
        )

    statement, lemmas = _parse_prove_by_lemmas_args(args_str)
    if not statement:
        return ToolResult(
            name="prove_by_lemmas",
            output="prove_by_lemmas requires a non-empty 'statement'.",
            success=False,
        )

    ctx_plan = _plan_from_ctx_dict(ctx.formalization_plan)
    plan = None
    if lemmas is not None:
        # The model supplied its own decomposition; reuse the existing plan's
        # goal_type/imports when available, otherwise plan them from scratch.
        base = ctx_plan if (ctx_plan is not None and ctx_plan.goal_type.strip()) else None
        if base is None:
            base = await _plan_lemma_decomposition(statement, ctx)
        if base is None or not base.goal_type.strip():
            return ToolResult(
                name="prove_by_lemmas",
                output=(
                    "Could not determine a Lean 4 goal type for this statement, so the "
                    "supplied lemmas cannot be assembled into a theorem. Try `formalize` "
                    "for a one-shot proof instead."
                ),
                success=False,
            )
        base.lemmas = lemmas
        base.problem = statement
        plan = base
    elif ctx_plan is not None and ctx_plan.lemmas and ctx_plan.goal_type.strip():
        # Reuse the unified planner's decomposition drafted up front.
        plan = ctx_plan
        if not plan.problem:
            plan.problem = statement
    else:
        plan = await _plan_lemma_decomposition(statement, ctx)
        if plan is None or not plan.lemmas or not plan.goal_type.strip():
            return ToolResult(
                name="prove_by_lemmas",
                output=(
                    "Could not produce a lemma decomposition for this statement (the planner "
                    "returned no lemmas or no goal type). Try `formalize` for a one-shot proof, "
                    "or call prove_by_lemmas again with an explicit 'lemmas' decomposition."
                ),
                success=False,
            )

    max_repair_attempts: int | None = None
    wall_seconds: float | None = None
    lean_codegen = ctx.lean_codegen
    if lean_codegen is not None:
        codegen_config = getattr(lean_codegen, "config", None)
        configured = getattr(codegen_config, "max_repair_attempts", None)
        if isinstance(configured, int) and configured > 0:
            max_repair_attempts = configured
        configured_wall = getattr(codegen_config, "lemma_executor_wall_seconds", None)
        if isinstance(configured_wall, (int, float)) and configured_wall > 0:
            wall_seconds = float(configured_wall)
    if wall_seconds is None:
        # Fall back to the runner's LeanConfig when no codegen is wired.
        runner_config = getattr(ctx.lean_runner, "config", None)
        configured_wall = getattr(runner_config, "lemma_executor_wall_seconds", None)
        if isinstance(configured_wall, (int, float)) and configured_wall > 0:
            wall_seconds = float(configured_wall)
    if max_repair_attempts is None:
        # Same fallback for the repair budget so lean.max_repair_attempts is
        # the single source (the executor defaults to LeanConfig otherwise).
        runner_config = getattr(ctx.lean_runner, "config", None)
        configured = getattr(runner_config, "max_repair_attempts", None)
        if isinstance(configured, int) and configured > 0:
            max_repair_attempts = configured
    max_parallel = 1
    rescue_enabled = False
    route_count = 1
    rescue_max_depth: int | None = None
    route_temperatures: list[float] | None = None
    difficulty_threshold: int | None = None
    max_routes_hard: int | None = None
    hook_max_attempts: int | None = None
    hook_max_depth: int | None = None
    for source in (
        getattr(ctx.lean_codegen, "config", None),
        getattr(ctx.lean_runner, "config", None),
    ):
        if source is None:
            continue
        configured_parallel = getattr(source, "lemma_max_parallel", None)
        if isinstance(configured_parallel, int) and configured_parallel > 0:
            max_parallel = configured_parallel
        if getattr(source, "lemma_rescue_enabled", False) is True:
            rescue_enabled = True
        configured_routes = getattr(source, "lemma_route_count", None)
        if isinstance(configured_routes, int) and configured_routes > 0:
            route_count = configured_routes
        configured_depth = getattr(source, "lemma_rescue_max_depth", None)
        if isinstance(configured_depth, int) and configured_depth > 0:
            rescue_max_depth = configured_depth
        configured_temperatures = getattr(source, "lemma_route_temperatures", None)
        if isinstance(configured_temperatures, (list, tuple)) and configured_temperatures:
            route_temperatures = [float(t) for t in configured_temperatures]
        configured_threshold = getattr(source, "lemma_difficulty_threshold", None)
        if isinstance(configured_threshold, int) and configured_threshold > 0:
            difficulty_threshold = configured_threshold
        configured_hard = getattr(source, "lemma_max_routes_hard", None)
        if isinstance(configured_hard, int) and configured_hard > 0:
            max_routes_hard = configured_hard
        configured_hook_attempts = getattr(source, "lemma_hook_max_attempts", None)
        if isinstance(configured_hook_attempts, int) and configured_hook_attempts > 0:
            hook_max_attempts = configured_hook_attempts
        configured_hook_depth = getattr(source, "lemma_hook_max_depth", None)
        if isinstance(configured_hook_depth, int) and configured_hook_depth > 0:
            hook_max_depth = configured_hook_depth
        break

    # Prover-first hook: try REPL tactic search (with the dedicated prover
    # model when configured) before spending an LLM codegen round per lemma.
    search_hook = None
    if ctx.repl_pool is not None:
        from math_agent.lean.proof_search import ProofSearch, TacticGenerator
        from math_agent.lean.repl_session import LeanReplPool

        pool_config = getattr(ctx.repl_pool, "config", None)
        if pool_config is not None and LeanReplPool.available(pool_config):
            hook_imports = "\n".join(
                f"import {module}"
                for module in sorted(
                    set(plan.recommended_imports) | {"Mathlib.Tactic.Common"}
                )
            )
            hook_generator = TacticGenerator(
                ctx.prover_llm or ctx.llm,
                max_candidates=3,
                premise_retriever=ctx.premise_retriever,
                trace_memory=ctx.trace_memory,
                critic_llm=ctx.critic_llm,
            )
            hook_search = ProofSearch(
                generator=hook_generator,
                runner=ctx.lean_runner,
                max_attempts=hook_max_attempts if hook_max_attempts is not None else 12,
                max_depth=hook_max_depth if hook_max_depth is not None else 6,
                premise_retriever=ctx.premise_retriever,
                repl_pool=ctx.repl_pool,
                imports=hook_imports,
                trace_memory=ctx.trace_memory,
            )

            async def search_hook(name: str, statement: str) -> str | None:
                # Guard the wrap: a statement that still carries its own
                # declaration header would produce unparseable Lean here.
                bare = sanitize_lemma_statement(statement)
                if not bare:
                    return None
                result = await hook_search.search(f"lemma {name} : {bare} := by")
                return result.proof if result.success else None

    executor = LemmaDAGExecutor(
        llm=ctx.llm,  # type: ignore[arg-type]
        runner=ctx.lean_runner,
        plan=plan,
        problem=statement,
        max_repair_attempts=max_repair_attempts,
        session_log=ctx.session_log,
        progress_callback=ctx.event_callback,
        wall_seconds=wall_seconds,
        search_hook=search_hook,
        max_parallel=max_parallel,
        rescue_enabled=rescue_enabled,
        route_count=route_count,
        rescue_max_depth=rescue_max_depth,
        route_temperatures=route_temperatures,
        difficulty_threshold=difficulty_threshold,
        max_routes_hard=max_routes_hard,
    )
    try:
        code = await executor.execute()
    except Exception as exc:
        log.exception("prove_by_lemmas executor failed")
        return ToolResult(
            name="prove_by_lemmas",
            output=f"Lemma-decomposition proof failed with an internal error: {exc}",
            success=False,
        )

    if not code:
        output = (
            "Lemma-decomposition proof failed: one of the lemmas or the final theorem "
            "could not be verified in Lean. Try `formalize` or `tactic_search`, or call "
            "prove_by_lemmas again with a different 'lemmas' decomposition."
        )
        # Hand partial progress back so the agent can reroute around the
        # failed step instead of starting from scratch.
        verified = list(getattr(executor, "verified_lemmas", []) or [])
        if verified:
            output += "\n\nVerified lemmas before the failure (you may reuse these):\n"
            output += "\n".join(f"\n```lean\n{lemma_code}\n```" for lemma_code in verified)
        failure = getattr(executor, "last_failure", None) or {}
        if failure:
            detail = str(failure.get("detail") or "")[:400]
            output += (
                f"\n\nFailure diagnostic: lemma={failure.get('lemma')}"
                f" failure_kind={failure.get('failure_kind')} detail={detail}"
            )
        return ToolResult(
            name="prove_by_lemmas",
            output=output,
            success=False,
        )

    try:
        result = await ctx.lean_runner.check_proof(code)
    except Exception as exc:
        return ToolResult(
            name="prove_by_lemmas",
            output=f"Final Lean verification failed: {exc}",
            success=False,
        )
    output = format_lean_result(code, result)
    success = bool(result.success and not result.uses_sorry)
    if success:
        output = await append_axioms_line(ctx.lean_runner, code, output)
    return ToolResult(
        name="prove_by_lemmas",
        output=output,
        success=success,
        lean_code=code,
    )


def _format_knowledge_search_results(
    facts: list[dict],
    intuitions: list[dict],
    tricks: list[dict],
) -> list[str]:
    lines: list[str] = []
    if facts:
        lines.append("Facts:")
        for f in facts:
            lines.append(f"  - {f.get('statement', '')}")
    if intuitions:
        lines.append("Intuitions:")
        for i in intuitions:
            lines.append(f"  - {i.get('title', '')}: {i.get('body', '')}")
    if tricks:
        lines.append("Tricks:")
        for t in tricks:
            lines.append(f"  - {t.get('title', '')}: {t.get('body', '')}")
    return lines
