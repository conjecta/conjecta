from __future__ import annotations

import re
from dataclasses import dataclass, field

from .mathlib_search import MathlibSearch, _DECL_RE, default_search


# Lean keywords / tactic names that should not be treated as external constants.
_LEAN_KEYWORDS = {
    "import", "open", "namespace", "section", "end", "variable", "variables",
    "universe", "universes", "set_option", "attribute", "local", "scoped",
    "theorem", "lemma", "def", "instance", "abbrev", "structure", "class",
    "inductive", "opaque", "axiom", "example", "where", "extends", "deriving",
    "by", "have", "let", "show", "suffices", "from", "calc", "done", "sorry",
    "exact", "exact?", "apply", "apply?", "refine", "refine?", "rewrite",
    "rw", "rw?", "simp", "simp?", "simp_all", "simp_all?", "norm_num",
    "linarith", "nlinarith", "omega", "ring", "ring_nf", "field_simp",
    "aesop", "tauto", "trivial", "rfl", "exact_rfl", "cc", "contradiction",
    "exfalso", "assumption", "intro", "intros", "revert", "generalize",
    "induction", "cases", "case", "rcases", "obtain", "destruct", "split",
    "left", "right", "constructor", "existsi", "use", "fun", "λ", "forall",
    "∀", "exists", "∃", "Pi", "Sigma", "if", "then", "else", "match",
    "return", "do", "let!", "←", "try", "catch", "repeat", "first", "all_goals",
    "any_goals", "focus", "solve1", "swap", "admit", "proof", "qed",
    "Type", "Sort", "Prop",
}

# Common projection suffixes; the parent name is what matters for existence checks.
_PROJECTION_SUFFIXES = {"mp", "mpr", "left", "right", "1", "2", "symm", "trans", "refl", "inv"}

# Fields that are legitimately accessed on local hypotheses in typical proofs.
# These should NOT be flagged as suspicious projections.
_COMMON_LOCAL_FIELDS = {"num", "den", "re", "im", "fst", "snd", "1", "2"}

# Qualified identifiers that start with an uppercase letter are likely mathlib names.
_QUALIFIED_NAME_RE = re.compile(r"[A-Z][A-Za-z0-9_]*(?:\.[A-Za-z0-9_']+)+")

# Bare capitalized identifiers are usually type/class names from mathlib/core.
_BARE_TYPE_RE = re.compile(r"\b[A-Z][A-Za-z0-9_']{2,}\b")

# Simple tokenization for unqualified identifiers.
_TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_'_]*")

# Heuristic: identifiers that look like explicit mathlib constants often contain
# underscores and digits and are 8+ chars long (e.g. `coprime_num_den`).
_CONSTANT_LIKE_RE = re.compile(r"\b[a-z_][a-z0-9_']{7,}\b")


@dataclass
class CriticIssue:
    """A single issue reported by the critic."""

    name: str
    category: str
    suggestion: str
    context: str = ""


@dataclass
class CriticResult:
    """Result of critiquing generated Lean code before verification."""

    issues: list[CriticIssue] = field(default_factory=list)
    unknown_constants: list[str] = field(default_factory=list)
    suspicious_projections: list[str] = field(default_factory=list)

    @property
    def has_issues(self) -> bool:
        return bool(self.issues)

    def to_prompt_block(self) -> str:
        """Format issues as a prompt block for the repair LLM."""
        if not self.issues:
            return ""
        lines = ["--- Critic pre-check found likely problems ---"]
        for issue in self.issues:
            lines.append(f"* {issue.category}: `{issue.name}`")
            if issue.context:
                lines.append(f"  Location:\n{issue.context}")
            lines.append(f"  Required fix: {issue.suggestion}")
        lines.append(
            "CRITICAL: You MUST fix EVERY issue listed above. "
            "For any unknown mathlib constant, DELETE the explicit name and use "
            "`exact?` (single goal), `apply?` (implication), or `rw?` (rewrite). "
            "For any suspicious projection on a local hypothesis, DELETE the entire line "
            "or rewrite it using `rcases`/`obtain`/`cases`."
        )
        return "\n".join(lines)


class LeanCritic:
    """Fast pre-verification critic for generated Lean 4 code.

    The critic does NOT run `lake build`; it uses static analysis and a local
    mathlib4 index to catch common mistakes (unknown constants, suspicious
    projections on local hypotheses) before the expensive verifier is invoked.
    """

    def __init__(self, mathlib_search: MathlibSearch | None = None):
        self.search = mathlib_search or default_search()

    def critique(self, lean_code: str) -> CriticResult:
        """Inspect ``lean_code`` and return likely problems."""
        result = CriticResult()
        # Import lines are checked separately for the umbrella import; module
        # names should not be treated as unknown constants/types.
        code_without_imports = "\n".join(
            line for line in lean_code.splitlines()
            if not re.match(r"^\s*import\b", line)
        )
        self._check_qualified_names(code_without_imports, result)
        self._check_bare_type_names(code_without_imports, result)
        self._check_invented_constants(code_without_imports, result)
        self._check_suspicious_projections(code_without_imports, result)
        self._check_static_blockers(code_without_imports, result)
        self._check_umbrella_import(lean_code, result)
        return result

    def _check_umbrella_import(self, lean_code: str, result: CriticResult) -> None:
        """Flag `import Mathlib` (the umbrella import).

        The umbrella import loads all of Mathlib, which makes `lake build` take
        minutes and routinely hit the build timeout. Force replacement with
        specific module imports before the expensive verifier runs.
        """
        if re.search(r"^\s*import\s+Mathlib\s*$", lean_code, re.MULTILINE):
            result.issues.append(
                CriticIssue(
                    name="import Mathlib",
                    category="umbrella import (build timeout risk)",
                    suggestion=(
                        "Replace `import Mathlib` with specific module imports for "
                        "only the definitions/lemmas used (e.g. "
                        "`import Mathlib.Data.Finset.Card`). The umbrella import is "
                        "forbidden because it makes `lake build` time out."
                    ),
                )
            )

    def _check_invented_constants(self, lean_code: str, result: CriticResult) -> None:
        """Flag snake-case identifiers that look like mathlib constants but do not exist.

        This catches invented theorem names such as `dvd_of_emod_eq_zero`.
        """
        lean_code = _without_import_lines(lean_code)
        lines = lean_code.splitlines()
        declared: set[str] = set()
        for match in _DECL_RE.finditer(lean_code):
            declared.add(match.group(1))

        names = sorted(set(_CONSTANT_LIKE_RE.findall(lean_code)))
        for name in names:
            if name in _LEAN_KEYWORDS or name in declared:
                continue
            if self.search.search_by_name(name, max_results=1):
                continue
            result.unknown_constants.append(name)
            result.issues.append(
                CriticIssue(
                    name=name,
                    category="likely unknown constant",
                    suggestion="Remove the explicit name and use `exact?`, `apply?`, or `rw?`; or verify the exact mathlib4 theorem name.",
                    context=self._context_for_name(lean_code, lines, name),
                )
            )

    def _check_bare_type_names(self, lean_code: str, result: CriticResult) -> None:
        """Flag bare capitalized identifiers that do not exist in mathlib/core.

        This catches invented type names like `Rational` in theorem statements.
        """
        lean_code = _without_import_lines(lean_code)
        lines = lean_code.splitlines()
        declared: set[str] = set()
        for match in _DECL_RE.finditer(lean_code):
            declared.add(match.group(1))

        names = sorted(set(_BARE_TYPE_RE.findall(lean_code)))
        for name in names:
            if name in _LEAN_KEYWORDS or name in declared:
                continue
            if self.search.search_by_name(name, max_results=1):
                continue
            result.unknown_constants.append(name)
            result.issues.append(
                CriticIssue(
                    name=name,
                    category="likely unknown type",
                    suggestion="Replace with the correct mathlib4 type name (e.g. `Irrational` instead of `Rational`).",
                    context=self._context_for_name(lean_code, lines, name),
                )
            )

    def _check_qualified_names(self, lean_code: str, result: CriticResult) -> None:
        """Flag qualified names whose root declaration does not exist in mathlib."""
        lean_code = _without_import_lines(lean_code)
        lines = lean_code.splitlines()
        names = sorted(set(_QUALIFIED_NAME_RE.findall(lean_code)))
        for name in names:
            if self._name_exists(name):
                continue
            # Avoid duplicates when the parent was already reported.
            root = name.split(".")[0]
            if any(i.name.startswith(root + ".") or i.name == root for i in result.issues):
                continue
            result.unknown_constants.append(name)
            result.issues.append(
                CriticIssue(
                    name=name,
                    category="likely unknown constant",
                    suggestion="Remove the explicit name and use `exact?`, `apply?`, or `rw?`.",
                    context=self._context_for_name(lean_code, lines, name),
                )
            )

    def _check_suspicious_projections(self, lean_code: str, result: CriticResult) -> None:
        """Flag projections on single-letter or obviously-local identifiers.

        LLMs often invent fields like `r.cop` on a hypothesis `r`; such
        projections almost never exist and should be replaced with a proper
        destructuring step (`rcases`, `obtain`, etc.).
        """
        lines = lean_code.splitlines()
        # Pattern: a short lower-case identifier followed by a dot and a field.
        pattern = re.compile(r"\b([a-z][a-z0-9_']{0,7})\.([a-z][a-z0-9_']{1,20})\b")
        for match in pattern.finditer(lean_code):
            var, field = match.groups()
            token = match.group(0)
            if (
                token in _LEAN_KEYWORDS
                or field in _PROJECTION_SUFFIXES
                or field in _COMMON_LOCAL_FIELDS
            ):
                continue
            # Heuristic: if the variable is single-letter, the projection is
            # almost certainly invented.
            if len(var) <= 2:
                result.suspicious_projections.append(token)
                result.issues.append(
                    CriticIssue(
                        name=token,
                        category="suspicious projection on local hypothesis",
                        suggestion=(
                            f"Do not use `{token}`. Delete or rewrite the line "
                            f"containing `{token}` so that `{var}` is destructured "
                            f"with `rcases`/`obtain`/`cases` before accessing a field."
                        ),
                        context=self._context_at_pos(lines, lean_code[: match.start()].count("\n")),
                    )
                )

    def _context_for_name(self, lean_code: str, lines: list[str], name: str) -> str:
        """Return the first line where ``name`` appears, with a little context."""
        for idx, line in enumerate(lines):
            if name in line:
                start = max(0, idx - 1)
                end = min(len(lines), idx + 2)
                return "\n".join(f"{i + 1}: {lines[i]}" for i in range(start, end))
        return ""

    def _context_at_pos(self, lines: list[str], line_number: int) -> str:
        """Return a few lines around ``line_number`` (0-based)."""
        start = max(0, line_number - 1)
        end = min(len(lines), line_number + 2)
        return "\n".join(f"{i + 1}: {lines[i]}" for i in range(start, end))

    def _check_static_blockers(self, lean_code: str, result: CriticResult) -> None:
        """Flag obvious anti-patterns like `sorry` or `admit`."""
        blockers = {"sorry", "admit"}
        for token in blockers:
            if re.search(rf"\b{token}\b", lean_code):
                result.issues.append(
                    CriticIssue(
                        name=token,
                        category="placeholder tactic",
                        suggestion="Remove the placeholder and complete the proof.",
                    )
                )

    def _name_exists(self, name: str) -> bool:
        """Return True if any variant of ``name`` occurs in the local mathlib.

        We search for the literal identifier anywhere in Mathlib rather than
        requiring a definition line match: that avoids the false positives from
        ``search_by_name`` expanding ``Foo.bar.rfl`` into bare ``rfl``.
        """
        for variant in _name_variants(name):
            # Only consider reasonably long names; short bases are too noisy.
            if len(variant) < 3:
                continue
            try:
                matches = self.search._rg(re.escape(variant), max_results=1)
            except Exception:
                return True  # Conservative: assume exists if search fails.
            if matches:
                return True
        return False


def _name_variants(name: str) -> list[str]:
    """Generate existence-check variants without falling back to bare projection names.

    For ``Nat.thisTheoremDoesNotExist.rfl`` we want to check the full name, the
    parent ``Nat.thisTheoremDoesNotExist``, and the un-namespaced parent
    ``thisTheoremDoesNotExist``. We do NOT want to check bare ``rfl`` because
    that would make every invented constant appear to exist.
    """
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

    # Drop trailing projections one at a time.
    while len(parts) >= 2 and parts[-1] in _PROJECTION_SUFFIXES | {"rfl"}:
        parts = parts[:-1]
        add(".".join(parts))

    # Drop leading namespaces one by one (keeping any trailing projection).
    for i in range(1, min(len(parts), 5)):
        add(".".join(parts[i:]))

    return variants


def _without_import_lines(lean_code: str) -> str:
    return "\n".join(
        line for line in lean_code.splitlines() if not line.strip().startswith("import ")
    )


def default_critic() -> LeanCritic:
    """Return a critic using the project's default mathlib4 search."""
    return LeanCritic()
