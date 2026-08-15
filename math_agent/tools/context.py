"""Execution context passed to every tool call.

The ``math_agent.agent.*`` types are imported under TYPE_CHECKING only:
``math_agent.agent.__init__`` eagerly imports the ReAct agent, which imports
this package back, so a runtime import here would be circular.

TODO(refactor): several fields are typed ``object | None`` because the
concrete collaborators (stores, backends, configs) live in modules that
would import this package back. Tighten these to real types in a follow-up
pass once the dependency direction is fully inverted.
"""
from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from math_agent.config import AgentConfig, SearchConfig
from math_agent.tools.results import ToolResult

if TYPE_CHECKING:
    from math_agent.agent.react_state import ProjectContext
    from math_agent.agent.state import ReasoningState
    from math_agent.lean.codegen import LeanCodegen
    from math_agent.lean.premise_retriever import PremiseRetriever
    from math_agent.lean.runner import LeanRunner


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
