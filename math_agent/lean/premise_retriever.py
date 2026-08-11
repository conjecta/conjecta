from __future__ import annotations

import json
import logging
import os
import re
import tempfile
from collections import defaultdict
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

from math_agent.lean.mathlib_search import (
    MathlibSearch,
    _DECL_RE,
    _tokenize,
    default_search,
)

log = logging.getLogger("math_agent.lean.premise_retriever")

CACHE_SCHEMA_VERSION = 2


@dataclass(frozen=True)
class PremiseEntry:
    name: str
    module: str
    type: str
    docstring: str = ""
    file: str = ""
    line: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PremiseEntry:
        return cls(**data)

    def to_prompt_text(self) -> str:
        lines = [f"- `{self.name}` in `{self.module}`"]
        if self.type:
            lines.append(f"  type: {self.type.replace(chr(10), ' ')}")
        if self.docstring:
            doc = self.docstring.replace(chr(10), " ")
            lines.append(f"  doc: {doc[:120]}")
        return "\n".join(lines)


class PremiseRetriever:
    """Lazy cached retriever of mathlib4 declarations for a goal."""

    def __init__(
        self,
        mathlib_search: MathlibSearch | None = None,
        cache_dir: str | Path | None = None,
        entries: list[PremiseEntry] | None = None,
    ) -> None:
        self.search = mathlib_search
        if cache_dir is None:
            cache_dir = Path(".lean_workspace") / ".conjecta_cache" / "premise_index"
        self.cache_dir = Path(cache_dir)
        self._entries: list[PremiseEntry] | None = None
        self._token_index: dict[str, tuple[int, ...]] = {}
        if entries is not None:
            self._set_entries(entries)

    @property
    def index_path(self) -> Path:
        return self.cache_dir / "premises.jsonl"

    @property
    def fingerprint_path(self) -> Path:
        return self.cache_dir / "fingerprint.txt"

    @property
    def lock_path(self) -> Path:
        return self.cache_dir / "premises.lock"

    def _mathlib_fingerprint(self) -> str:
        """Return a string that changes when mathlib changes."""
        assert self.search is not None
        git_dir = self.search.root / ".git"
        if git_dir.exists():
            import subprocess

            try:
                result = subprocess.run(
                    ["git", "rev-parse", "HEAD"],
                    cwd=str(self.search.root),
                    capture_output=True,
                    text=True,
                    timeout=10,
                    check=False,
                )
                if result.returncode == 0:
                    return result.stdout.strip()
            except Exception:
                pass
        # Fallback: hash of a sample of file mtimes.
        import hashlib

        sample: list[str] = []
        for f in sorted(self.search.root.rglob("*.lean"))[:200]:
            sample.append(f"{f}:{f.stat().st_mtime}")
        return hashlib.sha256("\n".join(sample).encode("utf-8")).hexdigest()[:32]

    def _cache_fingerprint(self) -> str:
        return f"v{CACHE_SCHEMA_VERSION}:{self._mathlib_fingerprint()}"

    def _load_cached(self) -> list[PremiseEntry] | None:
        if not self.index_path.exists() or not self.fingerprint_path.exists():
            return None
        current_fp = self._cache_fingerprint()
        try:
            cached_fp = self.fingerprint_path.read_text(encoding="utf-8").strip()
        except OSError:
            return None
        if cached_fp != current_fp:
            return None
        entries: list[PremiseEntry] = []
        try:
            for line in self.index_path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    entries.append(PremiseEntry.from_dict(json.loads(line)))
        except (OSError, json.JSONDecodeError, TypeError):
            log.warning("Ignoring an unreadable premise cache at %s", self.index_path)
            return None
        return entries

    def _save_cached(self, entries: list[PremiseEntry]) -> None:
        self._atomic_write(
            self.index_path,
            "\n".join(json.dumps(e.to_dict()) for e in entries) + "\n",
        )
        self._atomic_write(self.fingerprint_path, self._cache_fingerprint())

    def _atomic_write(self, destination: Path, content: str) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary_name = tempfile.mkstemp(
            prefix=f".{destination.name}.",
            dir=destination.parent,
            text=True,
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)

    @contextmanager
    def _exclusive_cache_lock(self) -> Iterator[None]:
        """Serialize cache rebuilds across web workers and CLI processes."""
        import fcntl

        self.cache_dir.mkdir(parents=True, exist_ok=True)
        with self.lock_path.open("a+", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def _set_entries(self, entries: list[PremiseEntry]) -> list[PremiseEntry]:
        self._entries = entries
        token_index: dict[str, list[int]] = defaultdict(list)
        for position, entry in enumerate(entries):
            text = " ".join(
                [entry.name, entry.type, entry.docstring, entry.module]
            )
            tokens = {token.lower() for token in _tokenize(text) if len(token) >= 3}
            for token in tokens:
                token_index[token].append(position)
        self._token_index = {
            token: tuple(positions) for token, positions in token_index.items()
        }
        return entries

    def build_index(self, *, force: bool = False) -> list[PremiseEntry]:
        """Build or load the cached declaration index."""
        if not force and self._entries is not None:
            return self._entries

        if self.search is None:
            self.search = default_search()

        with self._exclusive_cache_lock():
            if not force and self._entries is not None:
                return self._entries
            cached = None if force else self._load_cached()
            if cached is not None:
                return self._set_entries(cached)

            log.info("Building premise index from %s", self.search.root)
            entries: list[PremiseEntry] = []
            seen: set[str] = set()
            # Scan all Mathlib .lean files for declarations.
            for file in self.search.all_declaration_files():
                rel = str(file.relative_to(self.search.root))
                if not rel.startswith("Mathlib"):
                    continue
                try:
                    lines = file.read_text(encoding="utf-8").splitlines()
                except Exception:
                    continue
                for line_num, line in enumerate(lines, start=1):
                    stripped = line.strip()
                    if not _DECL_RE.match(stripped):
                        continue
                    entry = self.search._load_declaration(rel, line_num, lines)
                    if entry is None:
                        continue
                    name = entry.get("name", "")
                    key = f"{entry.get('file')}:{entry.get('line')}"
                    if not name or key in seen:
                        continue
                    seen.add(key)
                    entries.append(
                        PremiseEntry(
                            name=name,
                            module=entry.get("module", ""),
                            type=entry.get("type", ""),
                            docstring=entry.get("docstring", ""),
                            file=entry.get("file", ""),
                            line=entry.get("line", 0),
                        )
                    )
            self._save_cached(entries)
            log.info("Premise index built: %d entries", len(entries))
            return self._set_entries(entries)

    def retrieve(self, goal: str, *, top_k: int = 5) -> list[PremiseEntry]:
        entries = self.build_index()
        if not entries:
            return []
        # Use the in-memory inverted index instead of rebuilding searchable text
        # for every declaration on every tactic-generation turn.
        query_tokens = {t.lower() for t in _tokenize(goal) if len(t) >= 3}
        if not query_tokens:
            return []

        positions: set[int] = set()
        for token in query_tokens:
            positions.update(self._token_index.get(token, ()))
        candidates = [entries[position] for position in sorted(positions)]

        if not candidates:
            return []

        # Re-rank with BM25 if available.
        ranked = _rank_premises_by_bm25(candidates, goal, top_k=top_k * 3)
        return _apply_structural_boost(ranked, goal, top_k=top_k)

    def repair_imports_for_errors(
        self, lean_code: str, errors: list[str]
    ) -> str:
        """Return an import block that may fix unknown-constant errors.

        The returned string contains one `import Module` line per suggested
        module, or an empty string if no candidates are found. Callers prepend
        the block to the theorem statement.
        """
        from math_agent.lean.mathlib_search import _UNKNOWN_CONSTANT_PATTERNS

        names: list[str] = []
        for err in errors:
            for pattern in _UNKNOWN_CONSTANT_PATTERNS:
                match = pattern.search(err)
                if match:
                    names.append(match.group(1))
                    break

        modules: set[str] = set()
        for name in names:
            for entry in self.retrieve(name, top_k=3):
                if entry.module.startswith("Mathlib."):
                    modules.add(entry.module)

        if not modules:
            return ""

        existing = {
            line.strip().removeprefix("import ").strip()
            for line in lean_code.splitlines()
            if line.strip().startswith("import ")
        }
        new_modules = sorted(modules - existing)
        if not new_modules:
            return ""
        return "\n".join(f"import {m}" for m in new_modules)


_HEAD_CONSTANT_RE = re.compile(r"\b[A-Z][A-Za-z0-9_'.]*\b")


def _apply_structural_boost(
    ranked: list[PremiseEntry], goal: str, *, top_k: int
) -> list[PremiseEntry]:
    """Boost entries whose type shares head constants with the goal.

    BM25 over names/docstrings misses the strongest signal in a proof state:
    the type constructors in play (`Nat`, `List`, `Fintype`, ...). Entries
    whose own type mentions the same heads get a stable-sort boost while the
    BM25 order breaks ties. Dotted heads match per segment (`List.length`
    also contributes `List`).
    """

    def heads_of(text: str) -> set[str]:
        heads: set[str] = set()
        for token in _HEAD_CONSTANT_RE.findall(text):
            for segment in token.split("."):
                if len(segment) >= 2:
                    heads.add(segment)
        return heads

    heads = heads_of(goal)
    if not heads or not ranked:
        return ranked[:top_k]

    def shared(entry: PremiseEntry) -> int:
        return len(heads & heads_of(entry.type))

    boosted = sorted(ranked, key=lambda entry: -shared(entry))
    return boosted[:top_k]


def _rank_premises_by_bm25(
    candidates: list[PremiseEntry], query: str, *, top_k: int
) -> list[PremiseEntry]:
    try:
        from rank_bm25 import BM25Okapi
    except Exception:
        return _rank_by_token_overlap(candidates, query, top_k=top_k)

    query_tokens = _tokenize(query)
    docs: list[list[str]] = []
    for entry in candidates:
        text = " ".join([entry.name, entry.type, entry.docstring, entry.module])
        docs.append([t for t in _tokenize(text)])
    try:
        bm25 = BM25Okapi(docs)
        scores = bm25.get_scores(query_tokens)
    except Exception:
        return _rank_by_token_overlap(candidates, query, top_k=top_k)

    scored = sorted(zip(scores, candidates), key=lambda x: (-x[0], x[1].name))
    return [entry for _, entry in scored[:top_k]]


def _rank_by_token_overlap(
    candidates: list[PremiseEntry], query: str, *, top_k: int
) -> list[PremiseEntry]:
    """Fallback relevance ranking when BM25 is unavailable.

    Scores candidates by weighted substring matches of the query tokens in the
    declaration name, module path, and type/docstring. This is enough to bring
    directly relevant declarations (e.g. ``irrational_sqrt_two``) to the top of
    a small result set.
    """
    query_tokens = [t.lower() for t in _tokenize(query)]
    if not query_tokens:
        return candidates[:top_k]

    def score(entry: PremiseEntry) -> int:
        name = entry.name.lower()
        module = entry.module.lower()
        body = f"{entry.type} {entry.docstring}".lower()
        total = 0
        for token in query_tokens:
            if token in name:
                total += 10
            if token in module:
                total += 5
            if token in body:
                total += 1
        return total

    ranked = sorted(candidates, key=lambda e: (-score(e), e.name))
    return ranked[:top_k]
