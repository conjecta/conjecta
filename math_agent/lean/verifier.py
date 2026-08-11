from __future__ import annotations

import re
import shutil
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from math_agent.lean.mathlib_search import _DECL_RE


class LeanDiagnosticKind(str, Enum):
    """Normalized categories for Lean compiler diagnostics."""

    SYNTAX = "syntax"
    UNKNOWN_CONSTANT = "unknown_constant"
    TYPE_MISMATCH = "type_mismatch"
    UNSOLVED_GOALS = "unsolved_goals"
    MISSING_INSTANCE = "missing_instance"
    TERMINATION = "termination"
    BAD_IMPORT = "bad_import"
    TIMEOUT = "timeout"
    LEAN_UNAVAILABLE = "lean_unavailable"
    PLACEHOLDER = "placeholder"
    UNSAFE_SOURCE = "unsafe_source"
    LEAN_ERROR = "lean_error"


@dataclass(frozen=True)
class LeanDiagnostic:
    """A single normalized Lean diagnostic suitable for repair routing."""

    kind: str
    message: str
    line: int | None = None
    column: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "message": self.message,
            "line": self.line,
            "column": self.column,
        }


@dataclass(frozen=True)
class BlockedTokenLocation:
    """Where a blocked placeholder token was found."""

    token: str
    lean_file: str


@dataclass
class LeanCheckResult:
    """Combined static and executable Lean verification result."""

    lean_file: str
    lean_available: bool
    static_ok: bool
    blocked_tokens: tuple[str, ...] = ()
    verification_ok: bool | None = None
    returncode: int | None = None
    stdout: str = ""
    stderr: str = ""
    diagnostics: tuple[LeanDiagnostic, ...] = ()
    scanned_files: tuple[str, ...] = ()
    blocked_token_locations: tuple[BlockedTokenLocation, ...] = ()

    @property
    def accepted(self) -> bool:
        return (
            self.lean_available
            and self.static_ok
            and self.verification_ok is True
        )

    @property
    def lean_ok(self) -> bool | None:
        return self.verification_ok

    @property
    def failure_kind(self) -> str | None:
        if self.accepted:
            return None
        if self.diagnostics:
            return self.diagnostics[0].kind
        if not self.lean_available:
            return LeanDiagnosticKind.LEAN_UNAVAILABLE.value
        if not self.static_ok:
            return LeanDiagnosticKind.PLACEHOLDER.value
        return LeanDiagnosticKind.LEAN_ERROR.value

    def to_dict(self) -> dict[str, Any]:
        return {
            "lean_file": self.lean_file,
            "lean_available": self.lean_available,
            "static_ok": self.static_ok,
            "blocked_tokens": list(self.blocked_tokens),
            "verification_ok": self.verification_ok,
            "returncode": self.returncode,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "diagnostics": [d.to_dict() for d in self.diagnostics],
            "scanned_files": list(self.scanned_files),
            "blocked_token_locations": [
                {"token": loc.token, "lean_file": loc.lean_file}
                for loc in self.blocked_token_locations
            ],
            "accepted": self.accepted,
            "failure_kind": self.failure_kind,
        }

    def to_verification_report(self):
        from math_agent.verification import report_from_lean_result

        return report_from_lean_result(self)


class LeanVerifier:
    """Conservative no-placeholder gate for Lean 4 files.

    Scans the target file and its local import closure for blocked tokens
    (sorry / admit / axiom by default) before trusting any compiler output.

    With ``allow_sorry=True`` (draft mode) ``sorry``/``admit`` holes are
    tolerated so incomplete proof skeletons can be type-checked cheaply;
    axioms and unsafe constructs stay blocked, and a draft result must never
    be treated as a complete proof.
    """

    #: Placeholder tokens tolerated in draft mode (``allow_sorry=True``).
    DRAFT_ALLOWED_TOKENS = ("sorry", "admit")

    BLOCKED_TOKEN_PATTERNS = {
        "sorry": re.compile(r"(?<![A-Za-z0-9_])sorry(?![A-Za-z0-9_])"),
        "proof_wanted": re.compile(r"(?<![A-Za-z0-9_])proof_wanted(?![A-Za-z0-9_])"),
        "sorryAx": re.compile(r"(?<![A-Za-z0-9_])sorryAx(?![A-Za-z0-9_])"),
        "#exit": re.compile(r"(?<![A-Za-z0-9_])#exit(?![A-Za-z0-9_])"),
        "admit": re.compile(r"(?<![A-Za-z0-9_])admit(?![A-Za-z0-9_])"),
        "axiom": re.compile(r"(?<![A-Za-z0-9_])axiom(?![A-Za-z0-9_])"),
        # `constant c : P` is an axiom declaration under another spelling.
        "constant": re.compile(r"(?<![A-Za-z0-9_])constant(?![A-Za-z0-9_])"),
    }
    # Lean elaboration can execute metaprograms.  Generated/user-provided proof
    # files are data, so reject command forms that can perform compile-time IO,
    # extend the parser, or bypass the trusted proof surface.
    UNSAFE_SOURCE_PATTERNS = {
        "run_cmd": re.compile(r"(?<![A-Za-z0-9_])run_cmd(?![A-Za-z0-9_])"),
        "run_tac": re.compile(r"(?<![A-Za-z0-9_])run_tac(?![A-Za-z0-9_])"),
        "#eval": re.compile(r"(?<![A-Za-z0-9_])#eval(?![A-Za-z0-9_])"),
        "#reduce": re.compile(r"(?<![A-Za-z0-9_])#reduce(?![A-Za-z0-9_])"),
        "unsafe": re.compile(r"(?<![A-Za-z0-9_])unsafe(?![A-Za-z0-9_])"),
        "opaque": re.compile(r"(?<![A-Za-z0-9_])opaque(?![A-Za-z0-9_])"),
        "initialize": re.compile(r"(?<![A-Za-z0-9_])initialize(?![A-Za-z0-9_])"),
        "foreign": re.compile(r"(?<![A-Za-z0-9_])foreign(?![A-Za-z0-9_])"),
        "extern": re.compile(r"(?<![A-Za-z0-9_])extern(?![A-Za-z0-9_])"),
        # Match `elab` / `macro` and their `_rules` forms. A bare word-boundary
        # after `elab` misses `elab_rules` because `_` is a word char.
        "elab": re.compile(r"(?<![A-Za-z0-9_])elab(?:_rules)?(?![A-Za-z0-9_])"),
        "macro": re.compile(r"(?<![A-Za-z0-9_])macro(?:_rules)?(?![A-Za-z0-9_])"),
        "syntax": re.compile(r"(?<![A-Za-z0-9_])syntax(?![A-Za-z0-9_])"),
        "implemented_by": re.compile(r"implemented_by"),
        "include_str": re.compile(r"(?<![A-Za-z0-9_])include_str(?![A-Za-z0-9_])"),
        # Any IO / filesystem namespace identifier — not only `IO.`.
        # `open IO` + `FS.writeFile` previously bypassed the `IO\.` gate.
        "IO": re.compile(r"(?<![A-Za-z0-9_])(?:IO|BaseIO|EIO)(?![A-Za-z0-9_])"),
        "FS": re.compile(r"(?<![A-Za-z0-9_])FS(?![A-Za-z0-9_])"),
        "System": re.compile(r"(?<![A-Za-z0-9_])System(?![A-Za-z0-9_])"),
        "liftIO": re.compile(r"(?<![A-Za-z0-9_])liftIO(?![A-Za-z0-9_])"),
        "CommandElabM": re.compile(r"(?<![A-Za-z0-9_])CommandElabM(?![A-Za-z0-9_])"),
        "TermElabM": re.compile(r"(?<![A-Za-z0-9_])TermElabM(?![A-Za-z0-9_])"),
    }

    def __init__(
        self,
        lean_executable: str | None = None,
        lake_executable: str | None = None,
        cwd: str | Path | None = None,
        timeout_seconds: int = 30,
        blocked_tokens: tuple[str, ...] = (
            "sorry", "admit", "axiom", "constant", "proof_wanted", "sorryAx", "#exit"
        ),
        reject_unsafe_source: bool = True,
        allowed_import_prefixes: tuple[str, ...] = ("Mathlib", "Std", "Batteries"),
        import_roots: tuple[str | Path, ...] = (),
        prefer_lake_env: bool = True,
        allow_sorry: bool = False,
    ):
        self.lean_executable = (
            lean_executable if lean_executable is not None else shutil.which("lean")
        )
        self.lake_executable = (
            lake_executable if lake_executable is not None else shutil.which("lake")
        )
        self.cwd = Path(cwd) if cwd is not None else None
        self.timeout_seconds = timeout_seconds
        self.allow_sorry = allow_sorry
        if allow_sorry:
            blocked_tokens = tuple(
                token
                for token in blocked_tokens
                if token not in self.DRAFT_ALLOWED_TOKENS
            )
        self.blocked_tokens = blocked_tokens
        self.reject_unsafe_source = reject_unsafe_source
        self.allowed_import_prefixes = allowed_import_prefixes
        self.import_roots = tuple(Path(root) for root in import_roots)
        self.prefer_lake_env = prefer_lake_env

    def check_static(self, lean_file: str | Path) -> LeanCheckResult:
        """Run only the static placeholder gate."""
        lean_file = Path(lean_file)
        scan = self._scan_import_closure(lean_file)
        blocked = tuple(sorted({loc.token for loc in scan["blocked"]}))
        diagnostics: tuple[LeanDiagnostic, ...] = ()
        if blocked:
            diagnostics = (
                LeanDiagnostic(
                    kind=(
                        LeanDiagnosticKind.UNSAFE_SOURCE.value
                        if any(token.startswith("unsafe:") for token in blocked)
                        else LeanDiagnosticKind.PLACEHOLDER.value
                    ),
                    message=(
                        "static Lean source gate found blocked constructs: "
                        + ", ".join(blocked)
                    ),
                ),
            )
        return LeanCheckResult(
            lean_file=str(lean_file),
            lean_available=self._lean_available(),
            static_ok=not blocked,
            blocked_tokens=blocked,
            diagnostics=diagnostics,
            scanned_files=tuple(str(p) for p in scan["files"]),
            blocked_token_locations=tuple(scan["blocked"]),
        )

    def scan_source(self, code: str, *, label: str = "<source>") -> LeanCheckResult:
        """Static gate over an in-memory source string (no import closure).

        Same blocked-token/unsafe-construct rules as :meth:`check_static`,
        for callers that stream code into a long-running process (e.g. the
        Lean REPL) instead of writing a file.
        """
        stripped = _strip_lean_comments_and_strings(code)
        blocked: list[BlockedTokenLocation] = []
        for token in self.blocked_tokens:
            pattern = self.BLOCKED_TOKEN_PATTERNS.get(token) or re.compile(
                rf"(?<![A-Za-z0-9_]){re.escape(token)}(?![A-Za-z0-9_])"
            )
            if pattern.search(stripped):
                blocked.append(BlockedTokenLocation(token=token, lean_file=label))
        if self.reject_unsafe_source:
            for name, pattern in self.UNSAFE_SOURCE_PATTERNS.items():
                if pattern.search(stripped):
                    blocked.append(
                        BlockedTokenLocation(token=f"unsafe:{name}", lean_file=label)
                    )
            for module in _lean_import_modules(stripped):
                if not any(
                    module == prefix or module.startswith(f"{prefix}.")
                    for prefix in self.allowed_import_prefixes
                ):
                    blocked.append(
                        BlockedTokenLocation(
                            token=f"unsafe:import:{module}", lean_file=label
                        )
                    )
        tokens = tuple(sorted({loc.token for loc in blocked}))
        diagnostics: tuple[LeanDiagnostic, ...] = ()
        if tokens:
            diagnostics = (
                LeanDiagnostic(
                    kind=(
                        LeanDiagnosticKind.UNSAFE_SOURCE.value
                        if any(token.startswith("unsafe:") for token in tokens)
                        else LeanDiagnosticKind.PLACEHOLDER.value
                    ),
                    message=(
                        "static Lean source gate found blocked constructs: "
                        + ", ".join(tokens)
                    ),
                ),
            )
        return LeanCheckResult(
            lean_file=label,
            lean_available=self._lean_available(),
            static_ok=not tokens,
            blocked_tokens=tokens,
            diagnostics=diagnostics,
            scanned_files=(label,),
            blocked_token_locations=tuple(blocked),
        )

    def evaluate(
        self,
        lean_file: str | Path,
        verification_ok: bool | None,
        returncode: int | None,
        stdout: str,
        stderr: str,
    ) -> LeanCheckResult:
        """Evaluate a Lean file given an already-executed build result."""
        lean_file = Path(lean_file)
        scan = self._scan_import_closure(lean_file)
        blocked = tuple(sorted({loc.token for loc in scan["blocked"]}))
        static_ok = not blocked

        diagnostics: tuple[LeanDiagnostic, ...] = ()
        if not static_ok:
            diagnostics = (
                LeanDiagnostic(
                    kind=(
                        LeanDiagnosticKind.UNSAFE_SOURCE.value
                        if any(token.startswith("unsafe:") for token in blocked)
                        else LeanDiagnosticKind.PLACEHOLDER.value
                    ),
                    message=(
                        "static Lean source gate found blocked constructs: "
                        + ", ".join(blocked)
                    ),
                ),
            )
        elif verification_ok is False:
            diagnostics = parse_lean_diagnostics(stdout=stdout, stderr=stderr)
        elif verification_ok is None and not self.lean_executable:
            diagnostics = (
                LeanDiagnostic(
                    kind=LeanDiagnosticKind.LEAN_UNAVAILABLE.value,
                    message="lean executable is not available",
                ),
            )

        return LeanCheckResult(
            lean_file=str(lean_file),
            lean_available=self._lean_available(),
            static_ok=static_ok,
            blocked_tokens=blocked,
            verification_ok=verification_ok,
            returncode=returncode,
            stdout=stdout,
            stderr=stderr,
            diagnostics=diagnostics,
            scanned_files=tuple(str(p) for p in scan["files"]),
            blocked_token_locations=tuple(scan["blocked"]),
        )

    def verify_file(self, lean_file: str | Path) -> LeanCheckResult:
        """Run Lean on a file and return a combined static + executable result."""
        lean_file = Path(lean_file)
        scan = self._scan_import_closure(lean_file)
        blocked = tuple(sorted({loc.token for loc in scan["blocked"]}))
        static_ok = not blocked
        verification_ok: bool | None = None
        stdout = ""
        stderr = ""
        returncode: int | None = None
        diagnostics: tuple[LeanDiagnostic, ...] = ()

        project_root = (
            self._find_lake_project_root(lean_file)
            if self.prefer_lake_env and (self.lean_executable or self.lake_executable)
            else None
        )

        if not static_ok:
            # Source inspection is deliberately verifier-first: never elaborate
            # code that is already outside the trusted proof subset.
            verification_ok = False
        elif self.lean_executable or self.lake_executable:
            try:
                if project_root is not None and self.lake_executable:
                    cmd: Sequence[str] = [
                        self.lake_executable,
                        "env",
                        "lean",
                        str(lean_file),
                    ]
                    cwd = project_root
                else:
                    cmd = [self.lean_executable or "lean", str(lean_file)]
                    cwd = self.cwd

                completed = subprocess.run(
                    cmd,
                    cwd=cwd,
                    text=True,
                    capture_output=True,
                    timeout=self.timeout_seconds,
                    check=False,
                )
                stdout = completed.stdout
                stderr = completed.stderr
                returncode = completed.returncode
                verification_ok = returncode == 0
            except subprocess.TimeoutExpired as exc:
                stdout = exc.stdout or ""
                stderr = exc.stderr or "verification command timed out"
                diagnostics = (
                    LeanDiagnostic(
                        kind=LeanDiagnosticKind.TIMEOUT.value,
                        message="lean command timed out",
                    ),
                )
        else:
            diagnostics = (
                LeanDiagnostic(
                    kind=LeanDiagnosticKind.LEAN_UNAVAILABLE.value,
                    message="lean executable is not available",
                ),
            )

        if not static_ok:
            diagnostics = (
                LeanDiagnostic(
                    kind=(
                        LeanDiagnosticKind.UNSAFE_SOURCE.value
                        if any(token.startswith("unsafe:") for token in blocked)
                        else LeanDiagnosticKind.PLACEHOLDER.value
                    ),
                    message=(
                        "static Lean source gate found blocked constructs: "
                        + ", ".join(blocked)
                    ),
                ),
            ) + tuple(
                diagnostic
                for diagnostic in diagnostics
                if diagnostic.kind != LeanDiagnosticKind.LEAN_UNAVAILABLE.value
            )
        elif verification_ok is False:
            diagnostics = parse_lean_diagnostics(stdout=stdout, stderr=stderr)
        elif verification_ok is True and (self.lean_executable or self.lake_executable):
            # Capture any warnings emitted during the successful build before
            # the axiom audit potentially adds its own diagnostics.
            if stdout.strip() or stderr.strip():
                diagnostics = parse_lean_diagnostics(stdout=stdout, stderr=stderr)
            # Axiom audit: even if rc==0, reject proofs that depend on
            # disallowed axioms such as sorryAx.
            audit_ok, forbidden, audit_stdout, audit_stderr, audit_rc = self._audit_axioms(
                lean_file, project_root
            )
            if not audit_ok:
                verification_ok = False
                if audit_rc == 0:
                    audit_message = (
                        "axiom audit found disallowed axioms: "
                        + ", ".join(sorted(set(forbidden)))
                    )
                else:
                    detail = _first_diagnostic_message(audit_stderr or audit_stdout)
                    audit_message = (
                        f"axiom audit command failed (return code {audit_rc}): {detail}"
                    )
                    returncode = audit_rc
                diagnostics = diagnostics + (
                    LeanDiagnostic(
                        kind=LeanDiagnosticKind.UNSAFE_SOURCE.value,
                        message=audit_message,
                    ),
                )

        return LeanCheckResult(
            lean_file=str(lean_file),
            lean_available=self._lean_available(),
            static_ok=static_ok,
            blocked_tokens=blocked,
            verification_ok=verification_ok,
            returncode=returncode,
            stdout=stdout,
            stderr=stderr,
            diagnostics=diagnostics,
            scanned_files=tuple(str(p) for p in scan["files"]),
            blocked_token_locations=tuple(scan["blocked"]),
        )

    def _lean_available(self) -> bool:
        """Return True when a Lean executable or lake environment is available."""
        if self.lean_executable:
            return True
        if self.prefer_lake_env and self.lake_executable:
            return True
        return False

    def _scan_import_closure(
        self,
        lean_file: Path,
    ) -> dict[str, tuple[Path, ...] | tuple[BlockedTokenLocation, ...]]:
        files: list[Path] = []
        blocked: list[BlockedTokenLocation] = []
        seen: set[Path] = set()
        self._scan_file(lean_file, files=files, blocked=blocked, seen=seen)
        return {"files": tuple(files), "blocked": tuple(blocked)}

    def _scan_file(
        self,
        lean_file: Path,
        files: list[Path],
        blocked: list[BlockedTokenLocation],
        seen: set[Path],
    ) -> None:
        key = lean_file.resolve() if lean_file.exists() else lean_file.absolute()
        if key in seen or not lean_file.exists():
            return
        seen.add(key)
        files.append(lean_file)
        text = lean_file.read_text(encoding="utf-8")
        code = _strip_lean_comments_and_strings(text)
        for token in self.blocked_tokens:
            pattern = self.BLOCKED_TOKEN_PATTERNS.get(token)
            if pattern is None:
                pattern = re.compile(
                    rf"(?<![A-Za-z0-9_]){re.escape(token)}(?![A-Za-z0-9_])"
                )
            if pattern.search(code):
                blocked.append(
                    BlockedTokenLocation(token=token, lean_file=str(lean_file))
                )

        if self.reject_unsafe_source:
            for name, pattern in self.UNSAFE_SOURCE_PATTERNS.items():
                if pattern.search(code):
                    blocked.append(
                        BlockedTokenLocation(
                            token=f"unsafe:{name}",
                            lean_file=str(lean_file),
                        )
                    )

            for module in _lean_import_modules(code):
                if not any(
                    module == prefix or module.startswith(f"{prefix}.")
                    for prefix in self.allowed_import_prefixes
                ):
                    blocked.append(
                        BlockedTokenLocation(
                            token=f"unsafe:import:{module}",
                            lean_file=str(lean_file),
                        )
                    )

        for module in _lean_import_modules(code):
            imported = self._resolve_local_import(module, lean_file)
            if imported is not None:
                self._scan_file(imported, files=files, blocked=blocked, seen=seen)

    def _resolve_local_import(self, module: str, current_file: Path) -> Path | None:
        relative = Path(*module.split(".")).with_suffix(".lean")
        roots = _candidate_import_roots(
            current_file=current_file,
            cwd=self.cwd,
            import_roots=self.import_roots,
        )
        for root in roots:
            candidate = root / relative
            if candidate.exists():
                return candidate
        return None

    def _find_lake_project_root(self, lean_file: Path) -> Path | None:
        """Return the nearest parent directory containing a lakefile.lean."""
        for parent in lean_file.resolve().parents:
            if (parent / "lakefile.lean").exists():
                return parent
        return None

    ALLOWED_AXIOMS = {"propext", "Quot.sound", "Classical.choice"}

    def _audit_axioms(
        self,
        lean_file: Path,
        project_root: Path | None,
    ) -> tuple[bool, list[str], str, str, int | None]:
        """Run a second Lean invocation with #print axioms for each declaration.

        Returns (audit_ok, forbidden_axioms, stdout, stderr, returncode).
        If any axiom outside ALLOWED_AXIOMS is found, audit_ok is False.
        If the audit command itself fails, audit_ok is False.
        """
        if project_root is None and self.prefer_lake_env:
            project_root = self._find_lake_project_root(lean_file)

        text = lean_file.read_text(encoding="utf-8")
        decl_names: list[str] = []
        for line in text.splitlines():
            match = _DECL_RE.match(line.strip())
            if match:
                decl_names.append(match.group(1))

        if not decl_names:
            return True, [], "", "", 0

        audit_file = lean_file.with_suffix(".audit.lean")
        audit_text = text + "\n" + "\n".join(
            f"#print axioms {name}" for name in decl_names
        )
        audit_file.write_text(audit_text, encoding="utf-8")

        try:
            if project_root is not None and self.lake_executable:
                cmd: Sequence[str] = [
                    self.lake_executable,
                    "env",
                    "lean",
                    str(audit_file),
                ]
                cwd = project_root
            else:
                cmd = [self.lean_executable or "lean", str(audit_file)]
                cwd = self.cwd

            completed = subprocess.run(
                cmd,
                cwd=cwd,
                text=True,
                capture_output=True,
                timeout=self.timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return False, [], "", "audit command timed out", 1
        finally:
            audit_file.unlink(missing_ok=True)

        if completed.returncode != 0:
            return False, [], completed.stdout, completed.stderr, completed.returncode

        forbidden: list[str] = []
        depends_re = re.compile(
            r"^'(?P<name>[^']+)' depends on axioms: \[(?P<axioms>[^\]]*)\]"
        )
        for line in completed.stdout.splitlines():
            match = depends_re.match(line.strip())
            if not match:
                continue
            axioms = {a.strip() for a in match.group("axioms").split(",") if a.strip()}
            forbidden.extend(axioms - self.ALLOWED_AXIOMS)

        return (not forbidden), sorted(set(forbidden)), completed.stdout, completed.stderr, completed.returncode


def verify_lean_file(
    lean_file: str | Path,
    lean_executable: str | None = None,
    lake_executable: str | None = None,
    timeout_seconds: int = 30,
) -> LeanCheckResult:
    """Convenience wrapper: verify a single Lean file with a fresh verifier."""
    return LeanVerifier(
        lean_executable=lean_executable,
        lake_executable=lake_executable,
        timeout_seconds=timeout_seconds,
    ).verify_file(lean_file)




def parse_lean_diagnostics(
    stdout: str = "", stderr: str = ""
) -> tuple[LeanDiagnostic, ...]:
    text = f"{stderr}\n{stdout}".strip()
    if not text:
        text = "lean command failed"
    kind = classify_lean_diagnostic_kind(text)
    line, column = _first_lean_location(text)
    return (
        LeanDiagnostic(
            kind=kind.value,
            message=_first_diagnostic_message(text),
            line=line,
            column=column,
        ),
    )


def classify_lean_diagnostic_kind(text: str) -> LeanDiagnosticKind:
    lowered = text.lower()
    if "timed out" in lowered or "timeout" in lowered:
        return LeanDiagnosticKind.TIMEOUT
    if "unknown constant" in lowered or "unknown identifier" in lowered:
        return LeanDiagnosticKind.UNKNOWN_CONSTANT
    if "type mismatch" in lowered or "application type mismatch" in lowered:
        return LeanDiagnosticKind.TYPE_MISMATCH
    if "unsolved goals" in lowered or "unsolved goal" in lowered:
        return LeanDiagnosticKind.UNSOLVED_GOALS
    if "failed to synthesize" in lowered and "instance" in lowered:
        return LeanDiagnosticKind.MISSING_INSTANCE
    if "failed to compile definition" in lowered and "termination" in lowered:
        return LeanDiagnosticKind.TERMINATION
    if "unknown module prefix" in lowered or "no such file or directory" in lowered:
        return LeanDiagnosticKind.BAD_IMPORT
    if "unexpected token" in lowered:
        return LeanDiagnosticKind.SYNTAX
    if "expected" in lowered and "got" in lowered:
        return LeanDiagnosticKind.SYNTAX
    return LeanDiagnosticKind.LEAN_ERROR


def _strip_lean_comments_and_strings(text: str) -> str:
    """Remove comments/strings in one pass without letting either hide code."""
    output: list[str] = []
    index = 0
    block_depth = 0
    in_string = False
    escaped = False
    while index < len(text):
        pair = text[index : index + 2]
        char = text[index]
        if block_depth:
            if pair == "/-":
                block_depth += 1
                output.extend("  ")
                index += 2
            elif pair == "-/":
                block_depth -= 1
                output.extend("  ")
                index += 2
            else:
                output.append("\n" if char == "\n" else " ")
                index += 1
            continue
        if in_string:
            output.append("\n" if char == "\n" else " ")
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            index += 1
            continue
        if pair == "/-":
            block_depth = 1
            output.extend("  ")
            index += 2
        elif pair == "--":
            newline = text.find("\n", index)
            if newline == -1:
                output.extend(" " * (len(text) - index))
                break
            output.extend(" " * (newline - index))
            index = newline
        elif char == "'":
            char_end = _find_char_literal_end(text, index)
            if char_end is not None:
                output.extend(" " * (char_end - index))
                index = char_end
            else:
                output.append(char)
                index += 1
        elif char == '"':
            in_string = True
            output.append(" ")
            index += 1
        else:
            output.append(char)
            index += 1
    return "".join(output)


def _find_char_literal_end(text: str, start: int) -> int | None:
    """Return the index just after a Lean character literal, or None.

    Handles single characters ('a'), simple escapes ('\\n', '\"', '\\'),
    and hex/unicode escapes ('\\xNN', '\\uNNNN').
    If the apostrophe is not the start of a valid literal (e.g. an identifier
    prime), None is returned so the caller treats it as ordinary code.
    """
    if start + 2 < len(text) and text[start + 2] == "'" and text[start + 1] != "\\":
        return start + 3
    if start + 1 < len(text) and text[start + 1] == "\\":
        if start + 3 < len(text) and text[start + 3] == "'":
            return start + 4
        if start + 5 < len(text) and text[start + 2] == "x" and _is_hex(text, start + 3, 2) and text[start + 5] == "'":
            return start + 6
        if start + 7 < len(text) and text[start + 2] == "u" and _is_hex(text, start + 3, 4) and text[start + 7] == "'":
            return start + 8
    return None


def _is_hex(text: str, start: int, length: int) -> bool:
    """Return True if text[start:start+length] are all hexadecimal digits."""
    if start + length > len(text):
        return False
    for char in text[start:start + length]:
        if char not in "0123456789abcdefABCDEF":
            return False
    return True


def _lean_import_modules(code: str) -> tuple[str, ...]:
    modules: list[str] = []
    for line in code.splitlines():
        stripped = line.strip()
        if not stripped.startswith("import "):
            continue
        for module in stripped.removeprefix("import ").split():
            if module:
                modules.append(module)
    return tuple(modules)


def _candidate_import_roots(
    current_file: Path,
    cwd: Path | None,
    import_roots: tuple[Path, ...],
) -> tuple[Path, ...]:
    roots: list[Path] = []
    roots.extend(import_roots)
    if cwd is not None:
        roots.append(cwd)
    roots.extend(current_file.parents)

    unique: list[Path] = []
    seen: set[Path] = set()
    for root in roots:
        key = root.resolve() if root.exists() else root.absolute()
        if key not in seen:
            unique.append(root)
            seen.add(key)
    return tuple(unique)


def _first_lean_location(text: str) -> tuple[int | None, int | None]:
    match = re.search(r":(?P<line>\d+):(?P<column>\d+):\s*(?:error|warning):", text)
    if not match:
        return None, None
    return int(match.group("line")), int(match.group("column"))


def _first_diagnostic_message(text: str) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped
    return "lean command failed"
