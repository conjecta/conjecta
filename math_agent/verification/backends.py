from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from math_agent.verification.report import VerificationReport, report_from_lean_result


class LeanRunnerVerificationBackend:
    """Verification backend adapter for the existing LeanRunner API."""

    def __init__(self, lean_runner: Any, *, name: str = "lean") -> None:
        self.lean_runner = lean_runner
        self.name = name

    async def verify(self, subject: Any, **kwargs: Any) -> VerificationReport:
        lean_code = self._extract_lean_code(subject)
        result = await self.lean_runner.check_proof(lean_code)
        report = report_from_lean_result(result, source=self.name)
        metadata = kwargs.get("metadata")
        if isinstance(metadata, Mapping):
            report.metadata.update(dict(metadata))
        return report

    def _extract_lean_code(self, subject: Any) -> str:
        if isinstance(subject, str):
            return subject
        if isinstance(subject, Mapping):
            code = subject.get("lean_code") or subject.get("code")
            if isinstance(code, str):
                return code
        code = getattr(subject, "lean_code", None)
        if isinstance(code, str):
            return code
        raise TypeError("Lean verification subject must be a Lean code string or contain lean_code/code.")
