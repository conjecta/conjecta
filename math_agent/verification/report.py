from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol


class VerificationBackend(Protocol):
    """Common interface for verification implementations.

    Backends may be LLM critics, Lean checks, static reviewers, or staged
    artifact verifiers. They should normalize their result into
    ``VerificationReport`` before crossing subsystem boundaries.
    """

    name: str

    async def verify(self, subject: Any, **kwargs: Any) -> "VerificationReport": ...


@dataclass
class VerificationReport:
    """Subsystem-neutral verification result."""

    source: str
    passed: bool
    issues: list[str] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)
    confidence: float = 0.0
    evidence: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def verdict(self) -> str:
        return "PASS" if self.passed else "FAIL"

    def to_review_result(self):
        from math_agent.agent.react_state import ReviewResult

        return ReviewResult(
            reviewer=self.source,
            verdict=self.verdict,
            issues=list(self.issues),
            suggestions=list(self.suggestions),
            confidence=self.confidence,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "passed": self.passed,
            "verdict": self.verdict,
            "issues": list(self.issues),
            "suggestions": list(self.suggestions),
            "confidence": self.confidence,
            "evidence": self.evidence,
            "metadata": dict(self.metadata),
        }


def report_from_review_result(review: Any) -> VerificationReport:
    return VerificationReport(
        source=review.reviewer,
        passed=review.verdict.upper() == "PASS",
        issues=list(review.issues),
        suggestions=list(review.suggestions),
        confidence=review.confidence,
        metadata={"verdict": review.verdict},
    )


def report_from_tool_observation(
    observation: Any,
    *,
    source: str,
    metadata: Mapping[str, Any] | None = None,
) -> VerificationReport:
    output = str(getattr(observation, "output", "") or "")
    error = getattr(observation, "error", None)
    passed = bool(getattr(observation, "success", False))
    issues: list[str] = []
    if not passed:
        if error:
            issues.append(str(error))
        elif output:
            issues.append(_truncate(output))

    return VerificationReport(
        source=source,
        passed=passed,
        issues=issues,
        evidence=output,
        confidence=1.0,
        metadata=dict(metadata or {}),
    )


def report_from_lean_result(
    result: Any,
    *,
    source: str = "lean",
) -> VerificationReport:
    passed = bool(getattr(result, "success", getattr(result, "accepted", False)))
    diagnostics = _diagnostics_to_dicts(getattr(result, "diagnostics", ()) or ())
    issues = _lean_issues(result, diagnostics, passed)
    metadata = _lean_metadata(result, diagnostics)
    return VerificationReport(
        source=source,
        passed=passed,
        issues=issues,
        suggestions=_lean_suggestions(metadata),
        confidence=1.0,
        evidence=str(getattr(result, "output", "") or getattr(result, "stderr", "") or ""),
        metadata=metadata,
    )


def _lean_issues(
    result: Any,
    diagnostics: list[dict[str, Any]],
    passed: bool,
) -> list[str]:
    errors = list(getattr(result, "errors", []) or [])
    if errors:
        return [str(error) for error in errors]
    if diagnostics:
        return [str(d.get("message") or d.get("kind") or "Lean diagnostic") for d in diagnostics]
    if passed:
        return []
    failure_kind = getattr(result, "failure_kind", None)
    if failure_kind:
        return [str(failure_kind)]
    output = str(getattr(result, "output", "") or getattr(result, "stderr", "") or "")
    if output:
        return [_truncate(output)]
    return ["Lean verification failed."]


def _lean_metadata(
    result: Any,
    diagnostics: list[dict[str, Any]],
) -> dict[str, Any]:
    keys = (
        "lean_file",
        "lean_available",
        "static_ok",
        "blocked_tokens",
        "verification_ok",
        "returncode",
        "failure_kind",
        "scanned_files",
        "candidate_level",
    )
    metadata: dict[str, Any] = {}
    for key in keys:
        if hasattr(result, key):
            value = getattr(result, key)
            if isinstance(value, tuple):
                value = list(value)
            metadata[key] = value
    if diagnostics:
        metadata["diagnostics"] = diagnostics
    return metadata


def _lean_suggestions(metadata: Mapping[str, Any]) -> list[str]:
    blocked_tokens = [str(token) for token in metadata.get("blocked_tokens", [])]
    if blocked_tokens:
        return [
            "Remove blocked Lean placeholder tokens: "
            + ", ".join(blocked_tokens)
            + "."
        ]
    if metadata.get("lean_available") is False:
        return ["Install Lean 4/lake or disable formal verification for this run."]
    return []


def _diagnostics_to_dicts(diagnostics: Any) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for diagnostic in diagnostics:
        if isinstance(diagnostic, Mapping):
            normalized.append(dict(diagnostic))
        elif hasattr(diagnostic, "to_dict"):
            normalized.append(diagnostic.to_dict())
        else:
            normalized.append({"message": str(diagnostic)})
    return normalized


def _truncate(text: str, limit: int = 2000) -> str:
    stripped = text.strip()
    if len(stripped) <= limit:
        return stripped
    return stripped[: limit - 1] + "..."
