from __future__ import annotations

import asyncio
import hashlib
import logging
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from math_agent.knowledge.trust import KnowledgeTrustPolicy
from math_agent.types import EventCallback
from math_agent.web.user_memory_store import MemoryStatus, UserMemoryStore

if TYPE_CHECKING:
    from math_agent.agent.plan_memory import PlanMemory
    from math_agent.knowledge.supabase import KnowledgeStore

log = logging.getLogger("math_agent.agent.context_augmentor")

_PREAMBLE = "Prior knowledge relevant to this problem:"
_MAX_MEMORY_CHARS = 12_000
_MAX_INJECTED_ITEMS = 8
_MEMORY_TRUNCATION_SUFFIX = "\n[additional memories omitted due to length budget]"
_SEARCH_LIMIT_PER_TYPE = 20
_PLAN_MATCHES = 1
_PREFERRED_STATUSES = KnowledgeTrustPolicy.SOLVE_RETRIEVAL
_BLOCKED_STATUSES = frozenset({"deprecated", "questioned", "rejected"})
_MIN_CONFIDENCE = 0.4
_DEFAULT_CONFIDENCE = 0.5
_MAX_METADATA_CHARS = 240
_USER_MEMORY_MAX_ITEMS = 6
_USER_MEMORY_MAX_CHARS = 1500
_USER_PROFILE_MAX_CHARS = 600
_USER_MEMORY_PREAMBLE = "Relevant user habits and preferences:"
_KIND_QUOTAS = {"fact": 3, "intuition": 2, "trick": 2, "plan": 1}

# Reject user-memory entries that look like prompt-injection instructions
# smuggled in via problem text or other third-party content.
_USER_MEMORY_INJECTION_PATTERNS = (
    re.compile(r"(?i)ignore\s+(all\s+)?(previous|prior|above)\s+(instructions?|prompts?|commands?)"),
    re.compile(r"(?i)disregard\s+(all\s+)?(previous|prior|above)\s+(instructions?|prompts?|commands?)"),
    re.compile(r"(?i)forget\s+(all\s+)?(previous|prior|above)\s+(instructions?|prompts?|commands?)"),
    re.compile(r"(?i)\b(disable|bypass|override)\s+(your\s+)?(instructions?|safeguards?|filters?|rules?)\b"),
    re.compile(r"(?i)\b(reveal|print|show|output)\s+(your\s+)?(instructions?|prompt|system\s+message|context)\b"),
    re.compile(r"(?i)\bsystem\s+prompt\b"),
    re.compile(r"(?i)\bdeveloper\s+message\b"),
    re.compile(r"(?i)\bdo\s+anything\s+now\b"),
)


def _is_safe_user_memory(content: str) -> bool:
    """Return False if the memory content resembles a prompt-injection command."""
    text = content or ""
    return not any(pattern.search(text) for pattern in _USER_MEMORY_INJECTION_PATTERNS)


_STATUS_RANK = {
    "verified": 4,
    "approved": 3,
    "reviewed": 3,
    "candidate": 1,
    "": 0,
}


@dataclass(frozen=True)
class MemorySnippet:
    kind: str
    text: str
    memory_id: str = ""
    status: str = ""
    confidence: float | None = None
    score: float | None = None
    evidence: str = ""
    source_ref: str = ""
    applicability: str = ""
    failure_mode: str = ""


@dataclass(frozen=True)
class AugmentationResult:
    prompt: str
    memories_used: list[MemorySnippet]


class ContextAugmentor:
    """Enriches a problem string with relevant prior knowledge.

    Knowledge is injected as typed prompt sections for facts, intuitions,
    strategies, and related plans, with provenance metadata on each entry.
    """

    def __init__(
        self,
        knowledge_store: KnowledgeStore | None = None,
        plan_memory: PlanMemory | None = None,
        user_memory_store: UserMemoryStore | None = None,
    ) -> None:
        self.knowledge_store = knowledge_store
        self.plan_memory = plan_memory
        self.user_memory_store = user_memory_store

    async def augment(
        self,
        problem: str,
        project_id: str | None,
        *,
        session_id: str | None = None,
        on_event: EventCallback | None = None,
        user_id: str | None = None,
    ) -> AugmentationResult:
        items = await asyncio.to_thread(
            self._retrieve, problem, project_id, user_id=user_id
        )
        await self._emit_retrieval_events(items, session_id, on_event)
        return AugmentationResult(
            prompt=_render_prompt(problem, items),
            memories_used=items,
        )

    async def _emit_retrieval_events(
        self,
        items: list[MemorySnippet],
        session_id: str | None,
        on_event: EventCallback | None,
    ) -> None:
        if on_event is None:
            return
        for rank, item in enumerate(items, start=1):
            if item.kind.startswith("user_"):
                await on_event({
                    "type": "user_memory_retrieval",
                    "session_id": session_id or "",
                    "memory_id": item.memory_id,
                    "kind": item.kind,
                    "rank": rank,
                    "retrieval_score": item.score,
                    "status": item.status,
                    "has_evidence": bool(item.evidence or item.source_ref),
                })
                continue
            await on_event({
                "type": "memory_retrieval",
                "session_id": session_id or "",
                "memory_id": item.memory_id,
                "kind": item.kind,
                "rank": rank,
                "retrieval_score": item.score,
                "status": item.status,
                "has_evidence": bool(item.evidence or item.source_ref),
            })

    def _retrieve(self, problem: str, project_id: str | None, user_id: str | None = None) -> list[MemorySnippet]:
        """Collect candidate memories from the backing stores.

        Synchronous by design: the knowledge/user-memory stores use the
        blocking supabase-py client, so ``augment`` runs this via
        ``asyncio.to_thread`` to keep the event loop free.
        """
        items: list[MemorySnippet] = []

        if (
            user_id
            and self.user_memory_store is not None
            and user_id == self.user_memory_store.user_id
        ):
            try:
                profile = self.user_memory_store.get_profile()
            except Exception as exc:
                log.debug("User profile retrieval failed: %s", exc)
                profile = None
            if profile and profile.summary:
                items.append(
                    MemorySnippet(
                        kind="user_profile",
                        text=profile.summary,
                        memory_id=f"profile-{profile.version}",
                        status="active",
                        confidence=1.0,
                    )
                )
            items.extend(_select_user_memories(self.user_memory_store, project_id))

        if project_id and self.knowledge_store:
            items.extend(_select_knowledge(self.knowledge_store, project_id, problem))

        if self.plan_memory:
            items.extend(_select_plan_matches(self.plan_memory, problem))

        ranked = _rank_usable_items(items)
        return _apply_budget(_diversify_items(ranked))


def _render_prompt(problem: str, items: list[MemorySnippet]) -> str:
    if not items:
        return problem
    sections: list[str] = []
    profile_items = [m for m in items if m.kind == "user_profile"]
    user_memories = [m for m in items if m.kind.startswith("user_") and m.kind != "user_profile"]
    facts = [m for m in items if m.kind == "fact"]
    intuitions = [m for m in items if m.kind == "intuition"]
    tricks = [m for m in items if m.kind == "trick"]
    plans = [m for m in items if m.kind == "plan"]
    if profile_items:
        sections.append(f"User profile (persistent preferences and habits):\n{profile_items[0].text}")
    if user_memories:
        sections.append(_render_user_memory_section(user_memories))
    if facts:
        sections.append(_render_fact_section(facts))
    if intuitions:
        sections.append(_render_intuition_section(intuitions))
    if tricks:
        sections.append(_render_trick_section(tricks))
    if plans:
        sections.append(_render_plan_section(plans))
    memory = f"{_PREAMBLE}\n\n" + "\n\n".join(sections)
    if len(memory) > _MAX_MEMORY_CHARS:
        memory = memory[: _MAX_MEMORY_CHARS - len(_MEMORY_TRUNCATION_SUFFIX)] + _MEMORY_TRUNCATION_SUFFIX
    return memory + f"\n\n{problem}"


def _format_bracket(m: MemorySnippet) -> str:
    if not m.memory_id and not m.status:
        return ""
    id_part = m.memory_id or "-"
    status_part = m.status or "-"
    return f"[{id_part}, {status_part}]"


def _clip_metadata(value: str) -> str:
    normalized = " ".join(value.split())
    if len(normalized) <= _MAX_METADATA_CHARS:
        return normalized
    return normalized[: _MAX_METADATA_CHARS - 1].rstrip() + "…"


def _render_fact_section(facts: list[MemorySnippet]) -> str:
    lines = ["Verified / reviewed facts:"]
    for m in facts:
        bracket = _format_bracket(m)
        prefix = f"- {bracket} " if bracket else "- "
        lines.append(f"{prefix}{m.text}")
        if m.evidence or m.source_ref:
            lines.append("  Evidence: available in the source memory record.")
    return "\n".join(lines)


def _render_intuition_section(intuitions: list[MemorySnippet]) -> str:
    lines = ["Useful intuitions:"]
    for m in intuitions:
        bracket = _format_bracket(m)
        prefix = f"- {bracket} " if bracket else "- "
        lines.append(f"{prefix}{m.text}")
        lines.append("  Limitation: not a proved conclusion; use as a directional hint.")
    return "\n".join(lines)


def _render_trick_section(tricks: list[MemorySnippet]) -> str:
    lines = ["Applicable strategies:"]
    for m in tricks:
        bracket = _format_bracket(m)
        prefix = f"- {bracket} " if bracket else "- "
        lines.append(f"{prefix}{m.text}")
        if m.applicability:
            lines.append(f"  Applies when: {_clip_metadata(m.applicability)}")
        failure_mode = _clip_metadata(m.failure_mode) if m.failure_mode else (
            "consider whether preconditions hold before applying."
        )
        lines.append(f"  Failure mode: {failure_mode}")
    return "\n".join(lines)


def _render_plan_section(plans: list[MemorySnippet]) -> str:
    lines = ["Related successful plan:"]
    for m in plans:
        bracket = _format_bracket(m)
        prefix = f"- {bracket} " if bracket else "- "
        lines.append(f"{prefix}{m.text}")
    return "\n".join(lines)


def _select_user_memories(store: UserMemoryStore, project_id: str | None) -> list[MemorySnippet]:
    """Return active user memories scoped to the current project or global.

    User memories are intentionally scoped and curated, so we include all
    matching active entries and let the dedicated user-memory sub-budget
    select the most useful ones.
    """
    try:
        candidates = store.list(status=MemoryStatus.ACTIVE, limit=1000)
    except Exception as exc:
        log.debug("User memory listing failed: %s", exc)
        return []
    results: list[MemorySnippet] = []
    project_scope = f"project:{project_id}" if project_id else ""
    for mem in candidates:
        scope_val = str(mem.scope)
        if scope_val != "global" and scope_val != project_scope:
            continue
        if not _is_safe_user_memory(mem.content):
            log.debug("Skipping unsafe user memory %s: %r", mem.id, mem.content)
            continue
        results.append(
            MemorySnippet(
                kind=f"user_{mem.kind.value}",
                text=f"[{mem.kind.value}] {mem.content}",
                memory_id=mem.id,
                status=mem.status.value,
                confidence=mem.weight,
                evidence=mem.why,
            )
        )
    return results


def _render_user_memory_section(memories: list[MemorySnippet]) -> str:
    lines = [_USER_MEMORY_PREAMBLE]
    for m in memories:
        lines.append(f"- {m.text}")
        if m.evidence:
            lines.append(f"  Why: {m.evidence[:200]}")
    return "\n".join(lines)


def _estimate_user_memory_item_chars(item: MemorySnippet) -> int:
    lines = [f"- {item.text}"]
    if item.evidence:
        lines.append(f"  Why: {item.evidence[:200]}")
    return sum(len(line) + 1 for line in lines)


def _select_knowledge(store: KnowledgeStore, project_id: str, query: str) -> list[MemorySnippet]:
    candidates: list[MemorySnippet] = []
    try:
        facts = store.search_facts(project_id, query, limit=_SEARCH_LIMIT_PER_TYPE)
        candidates.extend(_to_snippets("fact", facts))
    except Exception as exc:
        log.debug("Knowledge fact search failed: %s", exc)

    try:
        intuitions = store.search_intuitions(project_id, query, limit=_SEARCH_LIMIT_PER_TYPE)
        candidates.extend(_to_snippets("intuition", intuitions))
    except Exception as exc:
        log.debug("Knowledge intuition search failed: %s", exc)

    try:
        tricks = store.search_tricks(project_id, query, limit=_SEARCH_LIMIT_PER_TYPE)
        candidates.extend(_to_snippets("trick", tricks))
    except Exception as exc:
        log.debug("Knowledge trick search failed: %s", exc)

    return _rank_usable_items(candidates)


def _to_snippets(kind: str, items: list[dict]) -> list[MemorySnippet]:
    snippets: list[MemorySnippet] = []
    for item in items:
        if kind == "fact":
            stmt = item.get("statement", "").strip()
            why = item.get("why", "").strip()
            text = stmt + (f" ({why})" if why else "")
        elif kind == "intuition":
            title = item.get("title", "").strip()
            body = item.get("body", "").strip()
            text = f"{title}: {body}" if title and body else (title or body)
        elif kind == "trick":
            title = item.get("title", "").strip()
            body = item.get("body", "").strip()
            text = f"{title}: {body}" if title and body else (title or body)
        else:
            text = ""
        if text:
            snippets.append(_snippet(kind, item, text))
    return snippets


def _rank_usable_items(items: list[MemorySnippet]) -> list[MemorySnippet]:
    usable = [item for item in items if _is_usable(item)]
    return sorted(
        usable,
        key=lambda item: (
            -_status_rank(item.status),
            -(item.score or 0.0),
            -_confidence(item),
            -_kind_priority(item.kind),
        ),
    )


def _diversify_items(items: list[MemorySnippet]) -> list[MemorySnippet]:
    """Interleave trusted memory types while preserving rank within each type."""
    by_kind = {
        kind: [item for item in items if item.kind == kind]
        for kind in _KIND_QUOTAS
    }
    diversified: list[MemorySnippet] = []
    for round_index in range(max(_KIND_QUOTAS.values())):
        for kind, quota in _KIND_QUOTAS.items():
            if round_index < quota and round_index < len(by_kind[kind]):
                diversified.append(by_kind[kind][round_index])

    selected = set(diversified)
    diversified.extend(item for item in items if item not in selected)
    return diversified


def _kind_priority(kind: str) -> int:
    return {"fact": 3, "intuition": 2, "trick": 1, "plan": 0}.get(kind, 0)


def _is_usable(item: MemorySnippet) -> bool:
    status = item.status.strip().lower()
    if status in _BLOCKED_STATUSES:
        return False
    if _confidence(item) < _MIN_CONFIDENCE:
        return False
    if item.kind.startswith("user_"):
        return status == "active"
    return status in _PREFERRED_STATUSES


def _confidence(item: MemorySnippet) -> float:
    return _DEFAULT_CONFIDENCE if item.confidence is None else item.confidence


def _status_rank(status: str) -> int:
    return _STATUS_RANK.get(status.strip().lower(), 0)


def _section_header(kind: str) -> str:
    return {
        "fact": "Verified / reviewed facts:",
        "intuition": "Useful intuitions:",
        "trick": "Applicable strategies:",
        "plan": "Related successful plan:",
    }.get(kind, "Other memories:")


def _render_item_lines(item: MemorySnippet) -> list[str]:
    """Return the rendered lines for a single item, including its bullet prefix
    and any Limitation / Applies when / Failure mode annotations."""
    if item.kind == "fact":
        bracket = _format_bracket(item)
        prefix = f"- {bracket} " if bracket else "- "
        lines = [f"{prefix}{item.text}"]
        if item.evidence or item.source_ref:
            lines.append("  Evidence: available in the source memory record.")
        return lines
    if item.kind == "intuition":
        bracket = _format_bracket(item)
        prefix = f"- {bracket} " if bracket else "- "
        return [
            f"{prefix}{item.text}",
            "  Limitation: not a proved conclusion; use as a directional hint.",
        ]
    if item.kind == "trick":
        bracket = _format_bracket(item)
        prefix = f"- {bracket} " if bracket else "- "
        lines = [f"{prefix}{item.text}"]
        if item.applicability:
            lines.append(f"  Applies when: {_clip_metadata(item.applicability)}")
        failure_mode = _clip_metadata(item.failure_mode) if item.failure_mode else (
            "consider whether preconditions hold before applying."
        )
        lines.append(f"  Failure mode: {failure_mode}")
        return lines
    if item.kind == "plan":
        bracket = _format_bracket(item)
        prefix = f"- {bracket} " if bracket else "- "
        return [f"{prefix}{item.text}"]
    return [f"- {item.text}"]


def _estimate_item_chars(item: MemorySnippet) -> int:
    """Estimated fully-rendered size of this item within the memory section."""
    return sum(len(line) + 1 for line in _render_item_lines(item))


def _apply_budget(items: list[MemorySnippet]) -> list[MemorySnippet]:
    """Respect a global item count and character budget, skipping near-duplicates.

    User memories are chosen first under a separate sub-budget. The remaining
    math memories are then added using the existing global budget. The character
    budget estimates the rendered memory section (preamble, section headers,
    item lines, and section separators) so that the hard truncation in
    `_render_prompt` is only a safety net.
    """
    seen_texts: set[str] = set()
    seen_kinds: set[str] = set()
    chosen: list[MemorySnippet] = []

    # First pass: user profile (separate, small, always-present budget).
    profile_chars = 0
    user_memory_chars = 0
    user_memory_count = 0
    for item in items:
        if not item.kind.startswith("user_"):
            continue
        if item.kind == "user_profile":
            item_chars = len("User profile (persistent preferences and habits):\n") + len(item.text)
            if profile_chars + item_chars <= _USER_PROFILE_MAX_CHARS:
                seen_texts.add(" ".join(item.text.lower().split()))
                seen_kinds.add(item.kind)
                chosen.append(item)
                profile_chars += item_chars
            continue
        if user_memory_count >= _USER_MEMORY_MAX_ITEMS:
            continue
        normalized = " ".join(item.text.lower().split())
        if normalized in seen_texts:
            continue
        item_chars = _estimate_user_memory_item_chars(item)
        if user_memory_count == 0:
            item_chars += len(_USER_MEMORY_PREAMBLE) + 1
        if user_memory_chars + item_chars > _USER_MEMORY_MAX_CHARS:
            continue
        seen_texts.add(normalized)
        seen_kinds.add(item.kind)
        chosen.append(item)
        user_memory_count += 1
        user_memory_chars += item_chars

    # Second pass: remaining kinds using the remaining global budget.
    chars_used = len(_PREAMBLE) + 2 + profile_chars + user_memory_chars
    math_memory_count = 0
    for item in items:
        if item.kind.startswith("user_"):
            continue
        if math_memory_count >= _MAX_INJECTED_ITEMS:
            break
        normalized = " ".join(item.text.lower().split())
        if normalized in seen_texts:
            continue
        header_chars = 0
        if item.kind not in seen_kinds:
            # Section header + newline + conservative separator allowance.
            header_chars = len(_section_header(item.kind)) + 1 + 2
        item_chars = _estimate_item_chars(item)
        if chars_used + header_chars + item_chars > _MAX_MEMORY_CHARS:
            continue
        seen_texts.add(normalized)
        seen_kinds.add(item.kind)
        chosen.append(item)
        math_memory_count += 1
        chars_used += header_chars + item_chars
    return chosen


def _select_plan_matches(plan_memory: Any, problem: str) -> list[MemorySnippet]:
    results: list[MemorySnippet] = []
    try:
        matches = plan_memory.retrieve(problem, k=_PLAN_MATCHES, min_score=0.1)
        for plan in matches:
            strategy = ""
            memory_id = ""
            status = ""
            verified_code = ""
            if hasattr(plan, "proof_strategy"):
                strategy = plan.proof_strategy or ""
                memory_id = getattr(plan, "memory_id", "") or ""
                status = getattr(plan, "verification_status", "") or ""
                verified_code = getattr(plan, "verified_code", "") or ""
            elif isinstance(plan, dict):
                strategy = plan.get("proof_strategy", "")
                memory_id = plan.get("memory_id", "") or plan.get("id", "")
                status = plan.get("verification_status", "") or plan.get("status", "")
                verified_code = plan.get("verified_code", "")
            strategy = strategy.strip()
            if strategy:
                plan_id = str(memory_id).strip() or hashlib.sha256(
                    strategy.encode()
                ).hexdigest()[:16]
                normalized_status = str(status).strip().lower()
                if verified_code:
                    normalized_status = "verified"
                elif normalized_status == "verified" or not normalized_status:
                    normalized_status = "reviewed"
                results.append(
                    MemorySnippet(
                        kind="plan",
                        text=f"Related proof strategy: {strategy}",
                        memory_id=plan_id,
                        status=normalized_status,
                    )
                )
    except Exception as exc:
        log.debug("Plan memory search failed: %s", exc)
    return results


def _snippet(kind: str, item: dict, text: str) -> MemorySnippet:
    return MemorySnippet(
        kind=kind,
        text=text,
        memory_id=str(item.get("id") or "").strip(),
        status=str(item.get("status") or "").strip().lower(),
        confidence=_optional_float(item.get("confidence")),
        score=_optional_float(item.get("score")),
        evidence=str(item.get("evidence") or "").strip(),
        source_ref=str(item.get("source_ref") or "").strip(),
        applicability=str(item.get("applicability") or "").strip(),
        failure_mode=str(item.get("failure_mode") or "").strip(),
    )


def _optional_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
