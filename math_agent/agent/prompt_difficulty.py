"""Easy/hard prompt classification for skipping heavy pipeline stages.

Two layers:

1. ``trivially_easy`` — a structural short-circuit for obviously trivial
   prompts (tiny pure-arithmetic expressions). No keyword tables, no LLM call.
2. ``classify_easy_prompt`` — everything else goes to one cheap critic call
   returning ``{"difficulty": "easy"|"hard", "reason": ...}``. Any failure
   (exception, malformed JSON, unknown verdict) falls back to *hard*, which
   only means the solve keeps the full pipeline — a safe, conservative
   default, since the verdict never changes correctness requirements.

Set ``AgentConfig.easy_prompt_classifier = "rules"`` to skip the critic call
entirely and treat every non-trivial prompt as hard.
"""
from __future__ import annotations

import json
import logging
import re

from math_agent.agent.prompts import PROMPT_DIFFICULTY_SYSTEM
from math_agent.llm.base import LLMBackend, Message

# Tiny pure arithmetic / expression prompts (digits and operators only).
_TRIVIAL_ARITHMETIC_RE = re.compile(r"[\d\s+\-*/^().,=？?]+")
_TRIVIAL_MAX_CHARS = 40
_PROBLEM_MAX_CHARS = 2000

log = logging.getLogger("math_agent.agent.prompt_difficulty")


def trivially_easy(problem: str) -> bool:
    """Structural short-circuit: a tiny pure-arithmetic prompt needs no pipeline."""
    text = (problem or "").strip()
    return (
        bool(text)
        and len(text) <= _TRIVIAL_MAX_CHARS
        and bool(_TRIVIAL_ARITHMETIC_RE.fullmatch(text))
    )


def _parse_difficulty(raw: str) -> tuple[bool, str]:
    """Parse the critic verdict; raise on anything malformed or ambiguous."""
    text = (raw or "").strip()
    if text.startswith("```"):
        lines = text.splitlines()
        text = "\n".join(
            lines[1:-1] if lines and lines[-1].startswith("```") else lines[1:]
        ).strip()
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("difficulty classification must be a JSON object")
    difficulty = str(data.get("difficulty") or "").strip().lower()
    if difficulty not in {"easy", "hard"}:
        raise ValueError(f"unknown difficulty verdict: {difficulty!r}")
    return difficulty == "easy", str(data.get("reason") or "")[:160]


async def classify_easy_prompt(
    problem: str,
    critic_llm: LLMBackend,
    *,
    mode: str = "critic",
    run_log: logging.Logger | None = None,
) -> bool:
    """Whether *problem* is easy enough to skip planning/claim-check/review.

    The verdict only gates heavy pipeline stages, so the conservative "hard"
    fallback on any classifier failure is safe.
    """
    logger = run_log or log
    text = (problem or "").strip()
    if not text:
        return False
    if trivially_easy(text):
        return True
    if str(mode or "critic").strip().lower() == "rules":
        return False
    try:
        response = await critic_llm.complete(
            [Message(role="user", content=f"Problem:\n{text[:_PROBLEM_MAX_CHARS]}")],
            system=PROMPT_DIFFICULTY_SYSTEM,
            temperature=0.0,
        )
        easy, reason = _parse_difficulty(response.text)
    except Exception as exc:
        logger.warning(
            "Prompt difficulty classification failed; treating as hard: %s", exc
        )
        return False
    logger.info(
        "Prompt difficulty: %s (%s)", "easy" if easy else "hard", reason or "no reason"
    )
    return easy
