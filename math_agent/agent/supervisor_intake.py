"""First-step supervisor intake: digest referenced sources for the react engine."""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from math_agent.agent.prompts import SUPERVISOR_INTAKE_SYSTEM, with_time_context
from math_agent.llm.base import LLMBackend
from math_agent.source_fetch import combine_source_text, fetch_sources_from_prompt
from math_agent.web.json_utils import complete_json_object

log = logging.getLogger("math_agent.agent.supervisor")

VALID_INTENTS = frozenset({"clarify", "extend", "new_problem"})
_TEMPORAL_QUERY_RE = re.compile(
    r"(?:最新|近[期年]|今年|当前|近日|刚刚|本届|"
    r"\blatest\b|\brecent\b|\bthis\s+year\b|\bcurrent\b|"
    r"\bnewest\b|\bmost\s+recent\b|\b202[4-9]\b|\b203\d\b)",
    re.IGNORECASE,
)
_LEAN_REQUEST_RE = re.compile(
    r"(?:\blean(?:\s*4)?\b|\bformaliz(?:e|ed|ing|ation)\b|"
    r"\bformal(?:ly)?\s+(?:proof|prove|verify|verification)\b|"
    r"形式化|形式验证|形式证明|用\s*lean)",
    re.IGNORECASE,
)
_DIAGRAM_REQUEST_RE = re.compile(
    r"(?:画图|图解|示意图|作图|绘图|"
    r"\bdraw(?:\s+a)?\s+(?:diagram|figure|picture|plot)\b|"
    r"\bplot(?:\s+a)?\s+(?:diagram|figure|graph)\b|"
    r"\billustrate\b|\bwith\s+(?:a\s+)?(?:diagram|figure|picture)\b)",
    re.IGNORECASE,
)
_THEOREM_REQUEST_RE = re.compile(
    r"(?:\bprove\b|\bshow\s+that\b|\btheorem\b|\blemma\b|证明|求证|定理|引理)",
    re.IGNORECASE,
)


@dataclass
class IntakeResult:
    strategy: str
    intent: str = "new_problem"
    source_digest: str = ""
    source_label: str = ""
    source_text: str = ""
    fetch_failed: bool = False
    needs_search: bool = False
    search_query: str = ""
    search_results: str = ""
    require_formal_verification: bool = False


def requires_formal_verification(problem: str) -> bool:
    """Return whether the user explicitly requested a formal/Lean proof."""
    return bool(_LEAN_REQUEST_RE.search(_authoritative_request(problem) or ""))


def requires_diagram(problem: str) -> bool:
    """Return whether the user explicitly asked for a diagram/figure."""
    return bool(_DIAGRAM_REQUEST_RE.search(_authoritative_request(problem) or ""))


def _authoritative_request(problem: str) -> str:
    """Prefer the current-turn request when conversation context is prepended."""
    text = problem or ""
    for marker in (
        "Current user request (authoritative target):",
        "Current question:",
    ):
        if marker in text:
            return text.rsplit(marker, 1)[-1]
    return text


def looks_temporal(problem: str) -> bool:
    """True when the prompt likely depends on the current calendar year."""
    return bool(_TEMPORAL_QUERY_RE.search(_authoritative_request(problem) or ""))


async def _intake_web_search(query: str) -> str:
    from math_agent.search.web_search import web_search_with_fallback

    provider, text = await web_search_with_fallback(query)
    if provider == "none":
        return ""
    if provider == "duckduckgo":
        return f"[web search via DuckDuckGo]\n{text}"
    return text


def resolve_formal_verification(problem: str, verifier_config=None) -> bool:
    """Resolve the single production boundary for required formal proof."""
    policy = str(getattr(verifier_config, "formal_policy", "explicit") or "explicit")
    policy = policy.strip().lower()
    if policy not in {"explicit", "all_theorems", "disabled"}:
        policy = "explicit"
    if policy == "disabled":
        return False
    if requires_formal_verification(problem):
        return True
    require_all = policy == "all_theorems" or bool(
        getattr(verifier_config, "require_lean_for_theorems", False)
    )
    return require_all and bool(_THEOREM_REQUEST_RE.search(problem or ""))


def resolve_intent(
    raw: str | None,
    *,
    has_history: bool,
    problem: str = "",
) -> str:
    """Map raw intake intent to a validated routing intent."""
    if not has_history:
        return "new_problem"
    # Tool-bearing follow-ups must not take the clarify light path: Lean and
    # diagram requests need the full ReAct loop (and conclude gates).
    request = _authoritative_request(problem)
    if request and (_LEAN_REQUEST_RE.search(request) or _DIAGRAM_REQUEST_RE.search(request)):
        return "extend"
    intent = (raw or "").strip().lower()
    if intent in VALID_INTENTS:
        return intent
    return "extend"


class SupervisorIntake:
    """Deterministic intake for fresh prompts; enriched routing when context needs it."""

    def __init__(self, llm: LLMBackend) -> None:
        self.llm = llm

    async def analyze(
        self,
        problem: str,
        *,
        has_history: bool = False,
        proactive_search: bool = False,
    ) -> IntakeResult:
        from math_agent.source_fetch import extract_arxiv_ids, extract_urls

        # Detect whether the prompt references any sources at all
        referenced_urls = extract_urls(problem)
        referenced_arxiv = extract_arxiv_ids(problem)
        sources_referenced = bool(referenced_urls or referenced_arxiv)

        require_formal_verification = requires_formal_verification(problem)
        if not has_history and not sources_referenced and not proactive_search:
            if looks_temporal(problem):
                query = (_authoritative_request(problem) or problem).strip()[:200]
                search_results = await _intake_web_search(query) if query else ""
                if search_results:
                    log.info(
                        "Supervisor intake temporal search: query=%r chars=%d",
                        query[:120],
                        len(search_results),
                    )
                else:
                    log.warning(
                        "Supervisor intake temporal search returned no results: query=%r",
                        query[:120],
                    )
                return IntakeResult(
                    strategy="react",
                    intent="new_problem",
                    require_formal_verification=require_formal_verification,
                    needs_search=True,
                    search_query=query,
                    search_results=search_results,
                )
            return IntakeResult(
                strategy="react",
                intent="new_problem",
                require_formal_verification=require_formal_verification,
            )

        sources = await fetch_sources_from_prompt(problem)
        source_label, source_text = combine_source_text(sources)

        fetch_failed = sources_referenced and not source_text
        if fetch_failed:
            log.warning(
                "Supervisor intake: source fetch failed for referenced URL(s)/arXiv ID(s) "
                "in prompt. urls=%s arxiv=%s — agent will work from memory, not the actual source.",
                referenced_urls,
                referenced_arxiv,
            )

        user_prompt = problem
        if source_text:
            user_prompt = (
                f"{problem}\n\n"
                f"Fetched source material ({source_label}):\n{source_text}"
            )
            log.info(
                "Supervisor intake fetched %d source(s), label=%s chars=%d",
                len(sources),
                source_label[:120],
                len(source_text),
            )
        elif fetch_failed:
            user_prompt = (
                f"{problem}\n\n"
                f"NOTE: The system attempted to fetch the referenced source(s) "
                f"({', '.join(referenced_arxiv) or ', '.join(referenced_urls)}) but failed. "
                f"No source text is available. Base your analysis only on what can be inferred "
                f"from the prompt itself, and flag this limitation explicitly in source_digest."
            )

        try:
            data = await complete_json_object(
                self.llm,
                user=user_prompt,
                system=with_time_context(SUPERVISOR_INTAKE_SYSTEM),
                temperature=0.0,
            )
            if data:
                result = _normalize_intake(data, has_history=has_history, problem=problem)
                result.fetch_failed = fetch_failed
                if source_text:
                    result.source_text = source_text
                    if not result.source_label:
                        result.source_label = source_label
                # Clarify follow-ups should not pay for live web search.
                if result.intent == "clarify":
                    result.needs_search = False
                    result.search_query = ""
                    result.search_results = ""
                elif result.needs_search and result.search_query:
                    result.search_results = await _intake_web_search(result.search_query)
                    if result.search_results:
                        log.info(
                            "Supervisor intake search completed: query=%r chars=%d",
                            result.search_query[:120],
                            len(result.search_results),
                        )
                    else:
                        log.warning(
                            "Supervisor intake search failed: query=%r",
                            result.search_query[:120],
                        )
                return result
        except Exception as exc:
            log.warning("Supervisor intake failed (%s), falling back to react", exc)

        result = IntakeResult(
            strategy="react",
            intent=resolve_intent(None, has_history=has_history, problem=problem),
            fetch_failed=fetch_failed,
            require_formal_verification=require_formal_verification,
        )
        if source_text:
            result.source_text = source_text
            result.source_label = source_label
        return result


def _normalize_intake(
    data: dict,
    *,
    has_history: bool = False,
    problem: str = "",
) -> IntakeResult:
    # react is the only solve engine now; intake enriches + routes follow-up intent.
    source_digest = (data.get("source_digest") or "").strip()
    source_label = (data.get("source_label") or "").strip()
    needs_search = bool(data.get("needs_search"))
    search_query = (data.get("search_query") or "").strip()
    intent = resolve_intent(
        data.get("intent"),
        has_history=has_history,
        problem=problem,
    )
    return IntakeResult(
        strategy="react",
        intent=intent,
        source_digest=source_digest,
        source_label=source_label,
        needs_search=needs_search,
        search_query=search_query,
        require_formal_verification=requires_formal_verification(problem),
    )
