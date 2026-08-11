from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class LeanResult:
    """Result of checking a piece of Lean 4 code."""

    success: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    uses_sorry: bool = False
    output: str = ""  # full compiler/lake output for diagnostics

    # Extended gate information imported from verifier-first design.
    lean_available: bool = True
    static_ok: bool = True
    blocked_tokens: list[str] = field(default_factory=list)
    failure_kind: str | None = None
    diagnostics: list[dict[str, Any]] = field(default_factory=list)
    scanned_files: list[str] = field(default_factory=list)
    candidate_level: str | None = None
    # True when the check ran in draft mode (sorry/admit holes tolerated);
    # a draft result is never a complete proof.
    draft: bool = False

    def with_note(self, note: str) -> "LeanResult":
        """Return a copy with a note prepended to the output."""
        from dataclasses import replace
        return replace(self, output=f"{note}\n{self.output}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "errors": self.errors,
            "warnings": self.warnings,
            "uses_sorry": self.uses_sorry,
            "output": self.output,
            "lean_available": self.lean_available,
            "static_ok": self.static_ok,
            "blocked_tokens": self.blocked_tokens,
            "failure_kind": self.failure_kind,
            "diagnostics": self.diagnostics,
            "scanned_files": self.scanned_files,
            "candidate_level": self.candidate_level,
            "draft": self.draft,
        }

    def to_verification_report(self):
        from math_agent.verification import report_from_lean_result

        return report_from_lean_result(self)
