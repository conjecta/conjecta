from __future__ import annotations

import logging
import re
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Any

try:
    from rank_bm25 import BM25Okapi

    _BM25_AVAILABLE = True
except Exception:  # pragma: no cover
    BM25Okapi = None  # type: ignore
    _BM25_AVAILABLE = False

log = logging.getLogger("math_agent.lean.mathlib_search")

# Process-level cache for ripgrep results, keyed by (mathlib root, pattern,
# max_results). The search corpus is a pinned mathlib4 lake dependency, which
# is effectively immutable for the lifetime of a server process (it only
# changes when the workspace is re-provisioned, i.e. across restarts), so no
# explicit invalidation is needed. Entries are evicted FIFO once the cap is
# exceeded.
_RG_CACHE_MAX_ENTRIES = 512
_rg_cache: dict[tuple[str, str, int], list[dict[str, Any]]] = {}
_rg_cache_lock = threading.Lock()

# Patterns that identify an unknown constant/identifier in Lean diagnostics.
_UNKNOWN_CONSTANT_PATTERNS = [
    re.compile(r"Unknown constant `([^`]+)`"),
    re.compile(r"Unknown identifier `([^`]+)`"),
    re.compile(r"Unknown constant '([^']+)'"),
    re.compile(r"Unknown identifier '([^']+)'"),
    re.compile(r"unknown constant '([^']+)'"),
    re.compile(r"unknown identifier '([^']+)'"),
]

# Lean declaration keywords, optionally preceded by attributes like `@[simp]`
# or visibility/modifier keywords (`private`, `protected`, `noncomputable`, `partial`).
_DECL_RE = re.compile(
    r"^(?:\s*@\[[^\n]+\]\s+)*(?:private\s+|protected\s+|noncomputable\s+|partial\s+)*(?:theorem|lemma|def|instance|abbrev|structure|class|inductive|opaque|axiom)\s+([^\s\(:]+)"
)

# Tokens that are Lean keywords or otherwise uninteresting for import suggestion.
_LEAN_KEYWORDS = {
    "Mathlib",
    "Mathlib4",
    "import",
    "open",
    "variable",
    "variables",
    "universe",
    "universes",
    "section",
    "end",
    "namespace",
    "where",
    "deriving",
    "instance",
    "class",
    "structure",
    "inductive",
    "theorem",
    "lemma",
    "def",
    "abbrev",
    "opaque",
    "axiom",
    "example",
    "by",
    "have",
    "let",
    "rcases",
    "intro",
    "intros",
    "exact",
    "apply",
    "rw",
    "simp",
    "nlinarith",
    "linarith",
    "omega",
    "ring",
    "tauto",
    "aesop",
    "all_goals",
    "try",
    "repeat",
    "assume",
    "show",
    "from",
    "fun",
    "λ",
    "forall",
    "exists",
    "Prop",
    "Type",
    "Sort",
}


class MathlibSearch:
    """Search for declarations inside a local mathlib4 checkout."""

    def __init__(self, mathlib_root: str | Path | None = None):
        if mathlib_root is None:
            # Common locations for a mathlib4 dependency.
            candidates = [
                Path(".lean_workspace/.lake/packages/mathlib4"),
                Path(".lean_workspace/.lake/packages/mathlib"),
                Path(".lake/packages/mathlib4"),
                Path(".lake/packages/mathlib"),
                Path("lake-packages/mathlib4"),
                Path("lake-packages/mathlib"),
            ]
            for candidate in candidates:
                if candidate.exists():
                    mathlib_root = candidate
                    break
        if mathlib_root is None:
            raise RuntimeError(
                "mathlib4 checkout not found. Set mathlib_root or ensure a workspace exists."
            )
        self.root = Path(mathlib_root).resolve()
        self.rg = shutil.which("rg")
        if self.rg is None:
            raise RuntimeError("ripgrep (rg) is required for mathlib4 search")

    def all_declaration_files(self) -> list[Path]:
        """Return all .lean files under the mathlib root."""
        return sorted(self.root.rglob("*.lean"))

    def search_by_name(self, name: str, *, max_results: int = 10) -> list[dict[str, Any]]:
        """Search for a declaration with the exact or basename match."""
        candidates: list[dict[str, Any]] = []
        seen: set[str] = set()

        names_to_try = _candidate_names(name)
        for try_name in names_to_try:
            pattern = rf"^(?:\s*@\[[^\n]+\]\s+)*(?:theorem|lemma|def|instance|abbrev|structure|class|inductive|opaque|axiom)\s+{re.escape(try_name)}\b"
            results = self._rg(pattern, max_results=max_results * 2)
            for result in results:
                key = f"{result['file']}:{result['line']}"
                if key in seen:
                    continue
                seen.add(key)
                entry = self._load_declaration(result["file"], result["line"])
                if entry:
                    candidates.append(entry)
                if len(candidates) >= max_results:
                    return candidates
        return candidates

    def suggest_imports_for_code(
        self,
        lean_code: str,
        *,
        max_imports: int = 10,
        score_threshold: float | None = None,
    ) -> list[str]:
        """Suggest concrete mathlib4 import modules for the identifiers used in `lean_code`.

        ``score_threshold`` (e.g. 0.4) keeps only modules whose score is at least
        that fraction of the top module's score. This weeds out spurious
        matches while preserving genuinely relevant modules.
        """
        code = _strip_lean_comments(lean_code)
        # Drop import lines; module paths like `Mathlib.Data.Real.Irrational`
        # are not useful identifiers for searching declarations.
        code = re.sub(r"(?m)^\s*import\s+.*$", "", code)
        # Drop the local declaration name from theorem/lemma/def lines so we do
        # not search for them.
        code = _DECL_RE.sub(lambda m: m.group(0)[: m.start(1) - m.start(0)], code)

        tokens = set(re.findall(r"[A-Za-z_][A-Za-z0-9_'.]*", code))
        candidates: list[tuple[str, int]] = []
        for t in tokens:
            if t in _LEAN_KEYWORDS or len(t) < 3:
                continue
            # Ignore leftover module paths (e.g. from malformed imports).
            if t.startswith("Mathlib"):
                continue
            # Qualified theorem names (e.g. Nat.dvd_gcd), capitalized
            # identifiers (types/classes like Even / Irrational), and long
            # snake-case theorem names (e.g. irrational_sqrt_two) are the best
            # signals for which module to import.
            if "." in t:
                candidates.append((t, len(t) + 2))
            elif t[0].isupper():
                candidates.append((t, len(t)))
            elif len(t) >= 8:
                candidates.append((t, len(t)))

        module_scores: dict[str, int] = {}
        for name, score in candidates:
            name_lower = name.lower()
            for entry in self.search_by_name(name, max_results=3):
                module = entry.get("module")
                # Restrict to real mathlib modules; skip test files and
                # unrelated package internals.
                if not module or not module.startswith("Mathlib."):
                    continue
                if module.startswith("MathlibTest."):
                    continue
                bonus = score if name_lower in module.lower() else 0
                module_scores[module] = module_scores.get(module, 0) + score + bonus

        if not module_scores:
            return []

        # Prefer modules with the highest identifier score, then break ties by
        # shallower / more "core" mathlib paths.
        sorted_modules = sorted(
            module_scores.items(),
            key=lambda x: (-x[1], len(x[0].split(".")), x[0]),
        )

        top_score = sorted_modules[0][1]
        if score_threshold is not None and top_score > 0:
            min_score = top_score * score_threshold
            sorted_modules = [m for m in sorted_modules if m[1] >= min_score]

        return [m for m, _ in sorted_modules[:max_imports]]

    def repair_imports(self, lean_code: str) -> str:
        """Replace bad/too-broad imports with concrete mathlib4 modules.

        If `lean_code` imports `Mathlib` or references a module whose source
        file does not exist in the local mathlib4 checkout, the import block is
        rewritten using modules inferred from the identifiers used in the code.
        """
        import_lines: list[tuple[int, str, list[str]]] = []
        for idx, line in enumerate(lean_code.splitlines()):
            stripped = line.strip()
            if stripped.startswith("import "):
                modules = stripped.removeprefix("import ").split()
                import_lines.append((idx, line, modules))

        if not import_lines:
            return lean_code

        # Determine whether any existing import is problematic.
        needs_repair = False
        for _, _, modules in import_lines:
            for module in modules:
                if module == "Mathlib":
                    needs_repair = True
                    break
                rel = Path(*module.split(".")).with_suffix(".lean")
                if module.startswith("Mathlib.") and not (self.root / rel).exists():
                    needs_repair = True
                    break
            if needs_repair:
                break

        if not needs_repair:
            return lean_code

        suggested = self.suggest_imports_for_code(
            lean_code, max_imports=8, score_threshold=0.55
        )
        if not suggested:
            return lean_code

        # Keep any non-Mathlib imports that actually exist (e.g. local modules).
        kept: list[str] = []
        seen: set[str] = set()
        for _, _, modules in import_lines:
            for module in modules:
                if module == "Mathlib" or module.startswith("Mathlib."):
                    continue
                if module not in seen:
                    kept.append(module)
                    seen.add(module)

        # Add a generic tactic module if tactics are used; then add suggestions.
        new_imports = ["import Mathlib.Tactic.Common"] + [f"import {m}" for m in suggested if m not in seen]
        if kept:
            new_imports = [f"import {m}" for m in kept] + new_imports

        first_import_idx = import_lines[0][0]
        lines = lean_code.splitlines()
        import_indices = {idx for idx, _, _ in import_lines}
        # Remove all old import lines and insert the repaired block where the
        # first import used to be.
        lines = [ln for i, ln in enumerate(lines) if i not in import_indices]
        lines[first_import_idx:first_import_idx] = new_imports
        return "\n".join(lines)

    def _rank_by_bm25(
        self,
        candidates: list[dict[str, Any]],
        query: str,
        *,
        max_results: int = 10,
    ) -> list[dict[str, Any]]:
        """Re-rank declaration candidates using BM25 over their tokenized types."""
        if not _BM25_AVAILABLE or not candidates:
            return candidates[:max_results]

        query_tokens = _tokenize(query)
        docs: list[list[str]] = []
        for entry in candidates:
            text = " ".join(
                [
                    entry.get("name", ""),
                    entry.get("type", ""),
                    entry.get("docstring", ""),
                ]
            )
            docs.append([t for t in _tokenize(text) if t not in _LEAN_KEYWORDS])

        try:
            bm25 = BM25Okapi(docs)
            scores = bm25.get_scores(query_tokens)
        except Exception as exc:
            log.debug("BM25 mathlib ranking failed: %s", exc)
            return candidates[:max_results]

        scored = sorted(
            zip(scores, candidates),
            key=lambda x: (-x[0], x[1].get("name", "")),
        )
        return [entry for _, entry in scored[:max_results]]

    def search_by_type_snippet(
        self, snippet: str, *, max_results: int = 10
    ) -> list[dict[str, Any]]:
        """Search for declarations whose type contains the given snippet.

        Uses ripgrep for fast filtering, then BM25 to re-rank candidates by
        relevance to the full query.
        """
        tokens = [t for t in _tokenize(snippet) if len(t) >= 3]
        if not tokens:
            return []

        # Use the longest token for a fast ripgrep filter.
        main_token = max(tokens, key=len)
        rg_pattern = re.escape(main_token)
        raw = self._rg(rg_pattern, max_results=max_results * 20)

        candidates: list[dict[str, Any]] = []
        seen: set[str] = set()
        for result in raw:
            entry = self._load_declaration(result["file"], result["line"])
            if entry is None:
                continue
            key = f"{entry['file']}:{entry['line']}"
            if key in seen:
                continue
            seen.add(key)
            type_text = entry.get("type", "")
            # Keep candidates that contain any of the query tokens (softer filter).
            if any(t.lower() in type_text.lower() for t in tokens):
                candidates.append(entry)

        return self._rank_by_bm25(candidates, snippet, max_results=max_results)

    def find_candidates_for_errors(
        self, errors: list[str], *, max_per_error: int = 3, max_total: int = 12
    ) -> list[dict[str, Any]]:
        """Extract unknown constants from errors and search mathlib for them."""
        names: list[str] = []
        for err in errors:
            for pattern in _UNKNOWN_CONSTANT_PATTERNS:
                match = pattern.search(err)
                if match:
                    names.append(match.group(1))
                    break

        candidates: list[dict[str, Any]] = []
        seen: set[str] = set()
        for name in names:
            for entry in self.search_by_name(name, max_results=max_per_error):
                key = f"{entry['file']}:{entry['line']}"
                if key in seen:
                    continue
                seen.add(key)
                entry["trigger_error"] = name
                candidates.append(entry)
                if len(candidates) >= max_total:
                    return candidates
        return candidates

    def format_candidates_for_prompt(
        self, errors: list[str], *, max_total: int = 12
    ) -> str:
        """Return a prompt block listing mathlib declarations relevant to errors."""
        candidates = self.find_candidates_for_errors(errors, max_total=max_total)
        if not candidates:
            return ""
        lines = ["--- Relevant mathlib4 declarations ---"]
        for entry in candidates:
            name = entry.get("name", "?")
            module = entry.get("module", "?")
            decl = entry.get("declaration", "").replace("\n", " ").strip()
            if len(decl) > 200:
                decl = decl[:200] + " ..."
            lines.append(f"- `{name}` (in `{module}`)")
            if decl:
                lines.append(f"  {decl}")
        return "\n".join(lines)

    def _rg(self, pattern: str, max_results: int = 50) -> list[dict[str, Any]]:
        """Run ripgrep over Mathlib/*.lean and return file/line matches.

        Results are cached process-wide keyed by (root, pattern, max_results);
        the mathlib4 checkout is a pinned dependency that does not change
        while the process runs. Cached lists must be treated as read-only.
        """
        cache_key = (str(self.root), pattern, max_results)
        with _rg_cache_lock:
            cached = _rg_cache.get(cache_key)
        if cached is not None:
            return list(cached)
        cmd = [
            self.rg,
            "--json",
            "--max-count",
            str(max_results),
            "-n",
            "-g",
            "*.lean",
            pattern,
        ]
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
                cwd=str(self.root),
                stdin=subprocess.DEVNULL,
            )
        except Exception as exc:
            log.warning("mathlib rg search failed: %s", exc)
            return []

        results: list[dict[str, Any]] = []
        for line in proc.stdout.splitlines():
            if not line.startswith("{"):
                continue
            try:
                import json

                data = json.loads(line)
            except Exception:
                continue
            if data.get("type") != "match":
                continue
            match = data.get("data", {})
            path_data = match.get("path", {})
            file_path = path_data.get("text") or path_data.get("bytes")
            if not file_path:
                continue
            rel_path = Path(file_path)
            if rel_path.is_absolute():
                try:
                    rel_path = rel_path.relative_to(self.root)
                except ValueError:
                    pass
            results.append(
                {
                    "file": str(rel_path),
                    "line": match.get("line_number", 0),
                    "text": (
                        match.get("lines", {}).get("text", "").strip()
                    ),
                }
            )
        with _rg_cache_lock:
            if len(_rg_cache) >= _RG_CACHE_MAX_ENTRIES:
                _rg_cache.pop(next(iter(_rg_cache)))
            _rg_cache[cache_key] = results
        return list(results)

    def _load_declaration(
        self, file: str, line_number: int, lines: list[str] | None = None
    ) -> dict[str, Any] | None:
        """Read the declaration around a match and extract name/type/docstring.

        If ``lines`` is provided, it is used instead of re-reading ``file`` from
        disk. This avoids duplicate I/O when the caller has already loaded the
        file, e.g. during a full index build.
        """
        if lines is None:
            target = self.root / file
            if not target.exists():
                return None
            try:
                lines = target.read_text(encoding="utf-8").splitlines()
            except Exception:
                return None
        if line_number < 1 or line_number > len(lines):
            return None

        idx = line_number - 1
        decl_line = lines[idx]
        decl_match = _DECL_RE.match(decl_line.strip())
        if not decl_match:
            return None
        name = decl_match.group(1)

        # Collect only the declaration header.  Most mathlib declarations put
        # both their type and ``:= by`` on the first line, so starting from the
        # *following* line accidentally indexed proof bodies (and sometimes the
        # next declaration) as the type.
        header_lines: list[str] = []
        for j in range(idx, min(idx + 32, len(lines))):
            text = lines[j]
            stripped = text.strip()
            if not stripped:
                continue
            if j > idx and _DECL_RE.match(stripped):
                break

            terminator = _declaration_body_offset(text)
            header = (
                text[:terminator].rstrip()
                if terminator is not None
                else text.rstrip()
            )
            if header.strip():
                header_lines.append(header.strip() if j == idx else header)
            if terminator is not None:
                break

        declaration = "\n".join(header_lines).strip()
        first_header = header_lines[0].strip() if header_lines else decl_line.strip()
        first_match = _DECL_RE.match(first_header)
        signature_lines: list[str] = []
        if first_match is not None:
            first_signature = first_header[first_match.end() :].strip()
            if first_signature:
                signature_lines.append(first_signature)
            signature_lines.extend(line.strip() for line in header_lines[1:])
        signature = "\n".join(line for line in signature_lines if line).strip()

        # Look for a docstring immediately above.
        doc_lines: list[str] = []
        for j in range(idx - 1, max(idx - 10, -1), -1):
            text = lines[j].strip()
            if text.startswith("/--") or text.startswith("/-"):
                doc_lines.insert(0, text)
                break
            elif doc_lines or text.startswith("--"):
                doc_lines.insert(0, text)
            else:
                break

        return {
            "file": str(file),
            "line": line_number,
            "name": name,
            "declaration": declaration,
            "docstring": "\n".join(doc_lines).strip(),
            "type": signature,
            "module": _lean_module_name(file),
        }


def _declaration_body_offset(line: str) -> int | None:
    """Return where a declaration body starts on ``line``, if present."""
    assignment = line.find(":=")
    where_match = re.search(r"\bwhere\b", line)
    offsets = [
        offset
        for offset in (
            assignment,
            where_match.start() if where_match else -1,
        )
        if offset >= 0
    ]
    return min(offsets) if offsets else None


def _candidate_names(name: str) -> list[str]:
    """Generate search variants for a possibly namespaced Lean name."""
    name = name.strip("`'")
    parts = name.split(".")
    variants: list[str] = []
    seen: set[str] = set()

    def add(var: str) -> None:
        if len(var) >= 3 and var not in seen:
            variants.append(var)
            seen.add(var)

    # Full namespaced name.
    add(name)

    # If the last segment is a common projection (mp/mpr/left/right/1/2),
    # also try the parent name.
    if len(parts) >= 2 and parts[-1] in {"mp", "mpr", "left", "right", "1", "2"}:
        add(".".join(parts[:-1]))

    # Drop leading namespaces one by one.
    for i in range(1, min(len(parts), 5)):
        add(".".join(parts[i:]))

    # Try the base identifier alone, but only if it is reasonably specific.
    if len(parts) > 1:
        base = parts[-1]
        # Skip bare projection names; use the parent name instead.
        if base not in {"mp", "mpr", "left", "right", "1", "2"}:
            add(base)
        elif len(parts) >= 3:
            add(parts[-2])
    return variants


def _lean_module_name(file_path: str) -> str:
    """Convert 'Mathlib/Data/Finite/Defs.lean' to 'Mathlib.Data.Finite.Defs'."""
    p = Path(file_path)
    if p.suffix == ".lean":
        p = p.with_suffix("")
    return ".".join(p.parts)


def _tokenize(text: str) -> list[str]:
    """Extract simple alphanumeric Lean identifier tokens, splitting snake_case.

    Keeps the full identifier as a token for exact matches while also emitting
    each underscore-separated subword so that queries like ``sqrt`` can recall
    declarations named ``irrational_sqrt_two``.
    """
    identifiers = re.findall(r"[A-Za-z_][A-Za-z0-9_']*", text)
    tokens: list[str] = []
    seen: set[str] = set()
    for ident in identifiers:
        if ident not in seen:
            tokens.append(ident)
            seen.add(ident)
        for part in ident.split("_"):
            if part and part not in seen:
                tokens.append(part)
                seen.add(part)
    return tokens


def _strip_lean_comments(text: str) -> str:
    """Remove Lean line and nested block comments."""
    output: list[str] = []
    index = 0
    block_depth = 0
    while index < len(text):
        current = text[index : index + 2]
        if block_depth:
            if current == "/-":
                block_depth += 1
                index += 2
            elif current == "-/":
                block_depth -= 1
                index += 2
            else:
                if text[index] == "\n":
                    output.append("\n")
                index += 1
            continue

        if current == "/-":
            block_depth = 1
            index += 2
            continue
        if current == "--":
            newline = text.find("\n", index)
            if newline == -1:
                break
            output.append("\n")
            index = newline + 1
            continue

        output.append(text[index])
        index += 1

    return "".join(output)


def default_search() -> MathlibSearch:
    """Return a default search using the project's workspace mathlib."""
    return MathlibSearch()
