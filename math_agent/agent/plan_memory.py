from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
from collections.abc import Mapping
from dataclasses import asdict
from pathlib import Path
from typing import Any

from math_agent.agent.planner import FormalizationPlan
from math_agent.knowledge.trust import KnowledgeTrustPolicy
from math_agent.search.text_retrieval import multilingual_tokens

try:
    from rank_bm25 import BM25Okapi

    _BM25_AVAILABLE = True
except Exception:  # pragma: no cover - fallback if package is missing
    BM25Okapi = None  # type: ignore
    _BM25_AVAILABLE = False

log = logging.getLogger("math_agent.agent.plan_memory")

DEFAULT_RUNTIME_MEMORY_PATH = "logs/plan_memory.jsonl"
DEFAULT_SEED_MEMORY_PATH = "data/plan_memory.jsonl"

_STOPWORDS = {
    "a", "an", "the", "and", "or", "but", "if", "then", "else", "for",
    "to", "of", "in", "on", "at", "by", "with", "from", "as", "is", "are",
    "was", "were", "be", "been", "being", "have", "has", "had", "do", "does",
    "did", "will", "would", "could", "should", "may", "might", "must", "can",
    "this", "that", "these", "those", "it", "its", "we", "you", "they", "them",
    "their", "there", "where", "when", "why", "how", "what", "which", "who",
    "prove", "show", "theorem", "lemma", "every", "all", "any", "some", "let",
}

_FILE_LOCKS_GUARD = threading.Lock()
_FILE_LOCKS: dict[Path, threading.RLock] = {}
_PLAN_STRING_FIELDS = frozenset(
    {"problem", "restatement", "goal_type", "proof_strategy", "notes", "verified_code"}
)
_PLAN_OPTIONAL_STRING_FIELDS = frozenset({"recommended_theorem", "recommended_module"})
_PLAN_STRING_LIST_FIELDS = frozenset(
    {"recommended_imports", "open_namespaces", "variables", "assumptions", "instances"}
)
_PLAN_STATUSES = KnowledgeTrustPolicy.SOLVE_RETRIEVAL


def _plan_entry_id(
    problem: str,
    goal_type: str,
    plan: Mapping[str, Any],
    verified_code: str,
) -> str:
    canonical = json.dumps(
        {
            "problem": problem,
            "goal_type": goal_type,
            "plan": plan,
            "verified_code": verified_code,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return "plan-" + hashlib.sha256(canonical.encode()).hexdigest()[:24]


def _canonical_path(path: Path) -> Path:
    return path.expanduser().resolve(strict=False)


def _lock_for_runtime_file(path: Path) -> threading.RLock:
    key = _canonical_path(path)
    with _FILE_LOCKS_GUARD:
        lock = _FILE_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _FILE_LOCKS[key] = lock
        return lock


def _file_signature(path: Path) -> tuple[int, int, int, int, int] | None:
    try:
        stat = path.stat()
    except OSError:
        return None
    return (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)


def _is_string_list(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def _valid_lemmas(value: Any) -> bool:
    if not isinstance(value, list):
        return False
    for lemma in value:
        if not isinstance(lemma, Mapping):
            return False
        for key, field_value in lemma.items():
            if not isinstance(key, str):
                return False
            if key == "depends_on":
                if not _is_string_list(field_value):
                    return False
            elif not isinstance(field_value, str):
                return False
    return True


def _validated_entry(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    problem = value.get("problem", "")
    goal_type = value.get("goal_type", "")
    verified_code = value.get("verified_code", "")
    memory_id = value.get("id", "")
    verification_status = value.get("verification_status", "")
    plan_value = value.get("plan")
    if not all(
        isinstance(field, str)
        for field in (
            problem,
            goal_type,
            verified_code,
            memory_id,
            verification_status,
        )
    ):
        return None
    if not isinstance(plan_value, Mapping):
        return None

    plan = dict(plan_value)
    for field in _PLAN_STRING_FIELDS:
        if field in plan and not isinstance(plan[field], str):
            return None
    for field in _PLAN_OPTIONAL_STRING_FIELDS:
        if field in plan and plan[field] is not None and not isinstance(plan[field], str):
            return None
    if "is_standard_result" in plan and not isinstance(plan["is_standard_result"], bool):
        return None
    for field in _PLAN_STRING_LIST_FIELDS:
        if field in plan and not _is_string_list(plan[field]):
            return None
    if not _valid_lemmas(plan.get("lemmas", [])):
        return None

    allowed_plan_fields = (
        _PLAN_STRING_FIELDS
        | _PLAN_OPTIONAL_STRING_FIELDS
        | _PLAN_STRING_LIST_FIELDS
        | {"is_standard_result", "lemmas"}
    )
    normalized_plan = {key: field for key, field in plan.items() if key in allowed_plan_fields}
    normalized_plan["lemmas"] = [dict(lemma) for lemma in plan.get("lemmas", [])]
    normalized_status = verification_status.strip().lower()
    if verified_code:
        normalized_status = "verified"
    elif normalized_status not in _PLAN_STATUSES or normalized_status == "verified":
        normalized_status = "reviewed"
    return {
        "id": memory_id.strip() or _plan_entry_id(
            problem, goal_type, normalized_plan, verified_code
        ),
        "problem": problem,
        "goal_type": goal_type,
        "plan": normalized_plan,
        "verified_code": verified_code,
        "verification_status": normalized_status,
    }


class PlanMemory:
    """Persistent memory for successful formalization plans with BM25 retrieval.

    Plans are stored as JSON lines.  At load time we build a BM25 index over the
    problem text, goal type, proof strategy, notes, and lemma statements.  This
    gives robust retrieval for semantically similar problems without requiring
    embedding models or network access.
    """

    def __init__(
        self,
        path: str | Path | None = None,
        *,
        seed_path: str | Path | None = DEFAULT_SEED_MEMORY_PATH,
    ):
        self.path = Path(path or os.getenv("CONJECTA_PLAN_MEMORY_PATH") or DEFAULT_RUNTIME_MEMORY_PATH)
        self.seed_path = Path(seed_path) if seed_path else None
        self._runtime_is_seed = bool(
            self.seed_path is not None
            and _canonical_path(self.seed_path) == _canonical_path(self.path)
        )
        self._file_lock = _lock_for_runtime_file(self.path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._entries: list[dict[str, Any]] = []
        self._documents: list[list[str]] = []
        self._bm25: Any = None
        self._index_dirty = False
        self._runtime_signature: tuple[int, int, int, int, int] | None = None
        with self._file_lock:
            self._load_and_build_index()

    def _tokenize(self, text: str) -> list[str]:
        """Extract multilingual mathematical tokens for BM25 retrieval."""
        return [token for token in multilingual_tokens(text) if token not in _STOPWORDS]

    def _entry_document(self, entry: dict[str, Any]) -> list[str]:
        """Build a token list representing a memory entry for BM25."""
        parts: list[str] = []
        parts.extend(self._tokenize(entry.get("problem", "")))
        parts.extend(self._tokenize(entry.get("goal_type", "")))

        plan = entry.get("plan") or {}
        parts.extend(self._tokenize(plan.get("restatement", "")))
        parts.extend(self._tokenize(plan.get("proof_strategy", "")))
        parts.extend(self._tokenize(plan.get("notes", "")))
        for lemma in plan.get("lemmas", []):
            parts.extend(self._tokenize(lemma.get("statement", "")))
            parts.extend(self._tokenize(lemma.get("proof_hint", "")))

        # Verified code carries concrete identifiers (ZMod, Irrational, etc.)
        # that are strong retrieval signals.
        parts.extend(self._tokenize(entry.get("verified_code", "")))
        return parts

    def _load_and_build_index(self) -> None:
        """Load all entries from disk and rebuild the BM25 index."""
        self._entries = []
        if self.seed_path is not None:
            self._load_file(self.seed_path)
        if not self._runtime_is_seed:
            self._load_file(self.path)

        self._rebuild_index()
        self._runtime_signature = _file_signature(self.path)

    def _load_file(self, path: Path) -> None:
        if not path.exists():
            return
        try:
            with path.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = _validated_entry(json.loads(line))
                        if entry is not None:
                            self._entries.append(entry)
                    except Exception:
                        continue
        except Exception as exc:
            log.debug("PlanMemory load failed for %s: %s", path, exc)

    def _rebuild_index(self) -> None:
        """Rebuild the BM25 index in memory and clear the dirty flag."""
        self._documents = [self._entry_document(e) for e in self._entries]
        if _BM25_AVAILABLE and self._documents:
            try:
                self._bm25 = BM25Okapi(self._documents)
            except Exception as exc:
                log.warning("BM25 index rebuild failed: %s", exc)
                self._bm25 = None
        else:
            self._bm25 = None
        self._index_dirty = False

    def _refresh_if_changed(self) -> None:
        """Reload when another instance has changed this tenant's runtime file."""
        if _file_signature(self.path) != self._runtime_signature:
            self._load_and_build_index()

    def retrieve(
        self,
        problem: str,
        goal_type: str = "",
        *,
        k: int = 2,
        min_score: float = 0.0,
    ) -> list[FormalizationPlan]:
        """Return up to ``k`` past plans most relevant to the new problem."""
        return [
            plan for _, plan in self.retrieve_with_scores(
                problem, goal_type, k=k, min_score=min_score
            )
        ]

    def retrieve_with_scores(
        self,
        problem: str,
        goal_type: str = "",
        *,
        k: int = 2,
        min_score: float = 0.0,
    ) -> list[tuple[float, FormalizationPlan]]:
        """Return up to ``k`` past plans with their BM25 scores."""
        with self._file_lock:
            self._refresh_if_changed()
            if self._index_dirty:
                self._rebuild_index()
            if not self._entries:
                return []

            query = self._tokenize(problem) + self._tokenize(goal_type)
            if not query:
                return []

            scored: list[tuple[float, dict[str, Any]]] = []
            if self._bm25 is not None:
                try:
                    scores = self._bm25.get_scores(query)
                    for idx, score in enumerate(scores):
                        if score >= min_score:
                            scored.append((float(score), self._entries[idx]))
                except Exception as exc:
                    log.debug("BM25 retrieval failed: %s", exc)

            # Fallback to simple token overlap if BM25 is unavailable.
            if not scored:
                query_set = set(query)
                for entry in self._entries:
                    doc_set = set(self._entry_document(entry))
                    score = len(query_set & doc_set)
                    if score >= min_score:
                        scored.append((float(score), entry))

            scored.sort(key=lambda x: (-x[0], x[1].get("problem", "")))
            results: list[tuple[float, FormalizationPlan]] = []
            for score, entry in scored[:k]:
                try:
                    plan_data = dict(entry.get("plan", {}))
                    plan_data.pop("problem", None)
                    plan_data.pop("verified_code", None)
                    plan_data.pop("memory_id", None)
                    plan_data.pop("verification_status", None)
                    plan = FormalizationPlan(
                        **plan_data,
                        problem=entry.get("problem", ""),
                        verified_code=entry.get("verified_code", ""),
                        memory_id=entry.get("id", ""),
                        verification_status=entry.get("verification_status", ""),
                    )
                    results.append((score, plan))
                except Exception:
                    continue
            return results

    def record(
        self,
        problem: str,
        goal_type: str,
        plan: FormalizationPlan,
        verified_code: str = "",
        verification_status: str = "",
    ) -> None:
        """Persist a successful plan."""
        if self._runtime_is_seed:
            log.warning("Refusing to record a plan into the read-only seed file: %s", self.path)
            return
        entry = _validated_entry({
            "problem": problem,
            "goal_type": goal_type,
            "plan": asdict(plan),
            "verified_code": verified_code,
            "verification_status": verification_status,
        })
        if entry is None:
            log.warning("Refusing to record a malformed formalization plan")
            return
        try:
            with self._file_lock:
                self._refresh_if_changed()
                with self.path.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(entry, ensure_ascii=False) + "\n")
                self._entries.append(entry)
                # Defer the index rebuild to the next retrieval; rank_bm25 has
                # no incremental add, so rebuilding per record is O(n) each time.
                self._index_dirty = True
                self._runtime_signature = _file_signature(self.path)
            log.info("Recorded plan for problem: %s", problem[:80])
        except Exception as exc:
            log.debug("PlanMemory record failed: %s", exc)

    def record_if_success(
        self,
        problem: str,
        goal_type: str,
        plan: FormalizationPlan,
        success: bool,
        verified_code: str = "",
    ) -> None:
        """Only record plans that led to a verified proof."""
        if success and (plan.lemmas or plan.recommended_theorem):
            self.record(problem, goal_type, plan, verified_code=verified_code)
