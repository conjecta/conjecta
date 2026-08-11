from __future__ import annotations

import json
import logging
import re
from typing import Any, Iterable

from math_agent.agent.react_state import ReActSolution, ReActTrace
from math_agent.billing.models import LLMResponse
from math_agent.llm.base import LLMBackend, Message
from math_agent.web.user_memory_store import (
    MemoryScope,
    MemoryStatus,
    UserMemory,
    UserMemoryEntryKind,
    UserMemoryStore,
)

log = logging.getLogger("math_agent.agent.user_memory")

_PROBLEM_MAX_CHARS = 4_000
_ANSWER_MAX_CHARS = 4_000
_CONVERSATION_TURN_MAX_CHARS = 800
_TRACE_TURN_LIMIT = 12
_TRACE_THOUGHT_MAX_CHARS = 800
_TRACE_ACTION_MAX_CHARS = 500
_TRACE_OBSERVATION_MAX_CHARS = 600
_REPAIR_INPUT_MAX_CHARS = 12_000
_EMAIL_RE = re.compile(r"(?i)(?<![\w.+-])[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_PHONE_RE = re.compile(r"(?<!\d)(?:\+?\d[\s().-]*){10,15}(?!\d)")
_SECRET_ASSIGNMENT_RE = re.compile(
    r"(?i)\b(?:api[_ -]?key|access[_ -]?token|password|passwd|secret)\b\s*[:=]\s*\S+"
)
_SECRET_PREFIX_RE = re.compile(
    r"(?i)\b(?:sk|pk|rk|ghp|github_pat|xox[baprs]|AIza)[-_][A-Za-z0-9_-]{8,}"
)
_BEARER_RE = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}")
_JWT_RE = re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b")


def _response_text(response: LLMResponse) -> str:
    """Extract text from an LLM response."""
    return response.text or ""


def _has_verified_evidence(
    item: dict[str, Any],
    trace: ReActTrace,
    conversation_history: list[dict[str, str]],
) -> bool:
    """Return True when the memory content is grounded in non-LLM source material.

    A memory is considered verified when its content appears verbatim in the
    problem statement, a user conversation turn, or a tool observation output.
    This prevents an LLM extractor from fabricating a high-weight memory and
    immediately promoting it to ACTIVE.
    """
    content = str(item.get("content") or "").strip().lower()
    if not content:
        return False

    source_texts: list[str] = [trace.problem.lower()]
    for turn in conversation_history or []:
        if str(turn.get("role", "")).lower() == "user":
            source_texts.append(str(turn.get("text", "") or "").lower())
    for turn in getattr(trace, "turns", []) or []:
        observation = getattr(turn, "observation", None)
        if observation is not None:
            source_texts.append(str(getattr(observation, "output", "") or "").lower())

    normalized = " ".join(content.split())
    if len(normalized) < 4:
        return False
    for source in source_texts:
        if normalized in source:
            return True
    return False


_EXTRACTION_SYSTEM = """You are a user-memory extraction assistant for a mathematical reasoning agent.

Given the original problem, the agent's final answer, the reasoning trace, and any prior conversation, extract reusable signals about the USER (not about the math).

Allowed memory kinds:
- preference: language, style, verbosity, format, identity/goal
- technique: reusable user habits or heuristics
- correction: a boundary or error the user has corrected
- context: active domain/project context

Output ONLY valid JSON of this shape:
{
  "add": [
    {"kind": "preference", "content": "...", "why": "...", "weight": 0.0-1.0, "scope": "global|project:<project_id>"}
  ],
  "update": [{"id": "um-xxx", "weight": 0.95, "why": "..."}],
  "snooze": [{"id": "um-yyy", "reason": "..."}]
}

Rules:
- Extract only reusable, non-obvious user signals.
- Do not record specific problem answers.
- Deduplicate against existing memories: merge or increment weight instead of adding.
- Corrections need a concrete, generalizable lesson.
- Default new memories to candidate unless confidence is very high (weight >= 0.85).
- Do not record credentials, phone numbers, secrets, or other PII.
- If no useful memory can be extracted, return empty arrays.
"""

_REPAIR_SYSTEM = (
    "You output only valid JSON matching this schema:\n"
    '{"add":[{"kind":"preference|technique|correction|context","content":"...","why":"...",'
    '"weight":0.5,"scope":"global|project:<project_id>"}],'
    '"update":[{"id":"um-xxx","weight":0.9,"why":"..."}],'
    '"snooze":[{"id":"um-yyy","reason":"..."}]}\n'
    "Preserve fields from the previous response whenever possible. "
    "Return empty arrays if repair is impossible."
)


class UserMemoryConsolidator:
    def __init__(
        self,
        llm: LLMBackend,
        store: UserMemoryStore | None = None,
    ) -> None:
        self.llm = llm
        self.store = store

    async def consolidate(
        self,
        trace: ReActTrace,
        solution: ReActSolution,
        conversation_history: list[dict[str, str]] | None = None,
        *,
        source_session_id: str = "",
    ) -> list[UserMemory]:
        active: list[UserMemory] = []
        rejected: list[UserMemory] = []
        if self.store is not None:
            active = self.store.list(status=MemoryStatus.ACTIVE, limit=100)
            rejected = self.store.list_rejected(limit=50)
        prompt = self._build_prompt(trace, solution, conversation_history or [], active, rejected)
        raw: str | None = None
        data: dict[str, Any] | None = None
        try:
            raw = await self.llm.complete(
                [Message(role="user", content=prompt)],
                system=_EXTRACTION_SYSTEM,
                temperature=0.2,
            )
            data = _parse_json(_response_text(raw))
        except Exception as exc:
            log.warning("User memory extraction failed, attempting repair: %s", exc)
        if data is None:
            data = await self._repair(_response_text(raw) or "")
        return self._apply(
            data,
            trace,
            conversation_history=conversation_history or [],
            source_session_id=source_session_id,
        )

    def _build_prompt(
        self,
        trace: ReActTrace,
        solution: ReActSolution,
        conversation_history: list[dict[str, str]],
        active: list[UserMemory],
        rejected: list[UserMemory],
    ) -> str:
        lines = [
            f"Current project id: {trace.project_context.project_id or 'default'}",
            f"Problem: {_bounded_text(trace.problem, _PROBLEM_MAX_CHARS)}",
            f"Final answer: {_bounded_text(solution.final_answer, _ANSWER_MAX_CHARS)}",
        ]
        if conversation_history:
            lines.append("\nConversation history:")
            for turn in conversation_history[-10:]:
                role = _bounded_text(str(turn.get("role", "user")), 24)
                text = _bounded_text(
                    str(turn.get("text", "")), _CONVERSATION_TURN_MAX_CHARS
                )
                lines.append(f"{role}: {text}")
        if trace.turns:
            lines.append("\nReasoning trace:")
            for turn in trace.turns[-_TRACE_TURN_LIMIT:]:
                lines.append(f"\nStep {turn.step_num}")
                thought = _bounded_text(
                    str(getattr(turn, "thought", "")), _TRACE_THOUGHT_MAX_CHARS
                )
                lines.append(f"Thought: {thought}")
                action = getattr(turn, 'action', None)
                action_name = action.name if action else ''
                action_args = _bounded_text(
                    str(getattr(action, "args", "") if action else ""),
                    _TRACE_ACTION_MAX_CHARS,
                )
                lines.append(f"Action: {action_name}({action_args})")
                observation = getattr(turn, 'observation', None)
                observation_output = _bounded_text(
                    str(getattr(observation, "output", "") if observation else ""),
                    _TRACE_OBSERVATION_MAX_CHARS,
                )
                lines.append(f"Observation: {observation_output}")
        if active:
            lines.append("\nExisting active user memories (do not duplicate):")
            for mem in active:
                lines.append(f"- [{mem.kind.value}] {mem.content}")
        if rejected:
            lines.append("\nMemories the user has rejected or deleted (do NOT recreate):")
            for mem in rejected:
                lines.append(f"- [{mem.kind.value}] {mem.content}")
        return "\n".join(lines)

    async def _repair(self, raw: str) -> dict[str, Any]:
        repair_prompt = (
            "The previous response was not valid JSON. Output ONLY valid JSON matching "
            "the requested schema, with no markdown fences and no commentary.\n\n"
            f"Previous response:\n{_bounded_text(raw, _REPAIR_INPUT_MAX_CHARS)}\n\n"
            "Now output valid JSON:"
        )
        try:
            repaired = await self.llm.complete(
                [Message(role="user", content=repair_prompt)],
                system=_REPAIR_SYSTEM,
                temperature=0.0,
            )
            return _parse_json(_response_text(repaired)) or {"add": [], "update": [], "snooze": []}
        except Exception:
            log.warning("User memory extraction repair failed")
            return {"add": [], "update": [], "snooze": []}

    def _apply(
        self,
        data: dict[str, Any],
        trace: ReActTrace,
        *,
        conversation_history: list[dict[str, str]] = None,
        source_session_id: str = "",
    ) -> list[UserMemory]:
        added: list[UserMemory] = []
        if self.store is None:
            return added
        conversation_history = conversation_history or []
        project_id = trace.project_context.project_id or "default"
        user_id = self.store.user_id
        existing: dict[str, UserMemory] = {m.id: m for m in self.store.list(limit=None)}
        rejected = self.store.list_rejected(limit=None)

        add_items = data.get("add", [])
        for item in add_items if isinstance(add_items, list) else []:
            try:
                if not isinstance(item, dict):
                    continue
                mem = self._normalize_add(
                    item,
                    user_id,
                    project_id,
                    trace=trace,
                    conversation_history=conversation_history or [],
                    source_session_id=source_session_id,
                )
            except Exception as exc:
                log.debug("Skipping invalid user memory add: %s", exc)
                continue
            if (
                self._is_duplicate(mem, existing.values())
                or self._is_duplicate(mem, rejected)
                or self._is_duplicate(mem, added)
            ):
                continue
            try:
                saved = self.store.add(
                    content=mem.content,
                    kind=mem.kind,
                    why=mem.why,
                    weight=mem.weight,
                    status=mem.status,
                    scope=mem.scope,
                    source_session_id=mem.source_session_id,
                )
                added.append(saved)
            except Exception as exc:
                log.warning("Failed to add user memory: %s", exc)

        update_items = data.get("update", [])
        for item in update_items if isinstance(update_items, list) else []:
            try:
                if not isinstance(item, dict):
                    continue
                memory_id = str(item.get("id") or "")
                if not memory_id or memory_id not in existing:
                    continue
                fields: dict[str, Any] = {}
                if "weight" in item:
                    fields["weight"] = max(0.0, min(1.0, float(item["weight"])))
                if "why" in item:
                    fields["why"] = str(item["why"])[:200]
                self.store.update(memory_id, fields)
            except Exception as exc:
                log.warning("Failed to update user memory: %s", exc)

        snooze_items = data.get("snooze", [])
        for item in snooze_items if isinstance(snooze_items, list) else []:
            try:
                if not isinstance(item, dict):
                    continue
                memory_id = str(item.get("id") or "")
                if memory_id and memory_id in existing:
                    self.store.update(memory_id, {"status": MemoryStatus.SNOOZED.value})
            except Exception as exc:
                log.warning("Failed to snooze user memory: %s", exc)

        return added

    def _normalize_add(
        self,
        item: dict[str, Any],
        user_id: str,
        project_id: str,
        *,
        trace: ReActTrace,
        conversation_history: list[dict[str, str]],
        source_session_id: str = "",
    ) -> UserMemory:
        kind = UserMemoryEntryKind(str(item.get("kind") or "preference"))
        content = str(item.get("content") or "").strip()
        if not content:
            raise ValueError("content is required")
        scope_val = str(item.get("scope") or "global").strip()
        if scope_val.startswith("project:"):
            scope = MemoryScope.project(project_id)
        else:
            scope = MemoryScope.GLOBAL
        try:
            weight = max(0.0, min(1.0, float(item.get("weight", 0.5))))
        except (TypeError, ValueError):
            weight = 0.5
        # High weight alone must not promote a memory to ACTIVE; require
        # grounding in non-LLM source material (problem, user turn, or tool
        # observation). This closes a prompt-injection / self-promotion channel.
        status = MemoryStatus.CANDIDATE
        if weight >= 0.85 and _has_verified_evidence(item, trace, conversation_history):
            status = MemoryStatus.ACTIVE
        why = str(item.get("why") or "")[:200]
        if _contains_sensitive_data(content) or _contains_sensitive_data(why):
            raise ValueError("memory contains sensitive personal data")
        return UserMemory(
            content=content,
            user_id=user_id,
            kind=kind,
            why=why,
            weight=weight,
            status=status,
            scope=scope,
            source_session_id=source_session_id,
        )

    def _is_duplicate(self, mem: UserMemory, existing: Iterable[UserMemory]) -> bool:
        new_norm = " ".join(mem.content.lower().split())
        for other in existing:
            if other.kind != mem.kind:
                continue
            other_norm = " ".join(other.content.lower().split())
            if new_norm == other_norm or (len(new_norm) > 12 and new_norm in other_norm) or (len(other_norm) > 12 and other_norm in new_norm):
                return True
        return False


def _parse_json(text: str) -> dict[str, Any] | None:
    text = (text or "").strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        return None


def _bounded_text(value: str, max_chars: int) -> str:
    text = value or ""
    if len(text) <= max_chars:
        return text
    return text[: max(0, max_chars - 1)].rstrip() + "…"


def _contains_sensitive_data(value: str) -> bool:
    text = value or ""
    return any(
        pattern.search(text)
        for pattern in (
            _EMAIL_RE,
            _PHONE_RE,
            _SECRET_ASSIGNMENT_RE,
            _SECRET_PREFIX_RE,
            _BEARER_RE,
            _JWT_RE,
        )
    )
