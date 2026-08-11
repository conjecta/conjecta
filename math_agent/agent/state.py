from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class StepType(Enum):
    REASONING = "reasoning"
    TOOL_USE = "tool_use"
    FORMALIZATION = "formalization"
    CONCLUSION = "conclusion"


@dataclass
class VerificationResult:
    passed: bool
    issues: list[str] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)
    lean_checked: bool = False
    # Authoritative verification: Lean when feasible, otherwise human review.
    verification_method: str | None = None  # "lean" | "human_review"
    requires_human_review: bool = False
    lean_skip_reason: str | None = None


@dataclass
class ReasoningStep:
    content: str
    step_type: StepType = StepType.REASONING
    confidence: float = 0.0
    verified: bool = False
    verification: VerificationResult | None = None
    lean_code: str | None = None
    tool_name: str | None = None
    tool_result: str | None = None
    tool_success: bool = False


@dataclass
class ReasoningState:
    problem: str
    steps: list[ReasoningStep] = field(default_factory=list)
    current_goal: str = ""
    sub_goals: list[str] = field(default_factory=list)

    def context_window(self, max_steps: int = 10) -> str:
        """Return recent reasoning context for the LLM."""
        recent = self.steps[-max_steps:]
        parts = [f"Problem: {self.problem}\n"]
        if self.current_goal:
            parts.append(f"Current goal: {self.current_goal}\n")
        for i, step in enumerate(recent, 1):
            prefix = f"Step {len(self.steps) - len(recent) + i}"
            parts.append(f"{prefix} [{step.step_type.value}]: {step.content}")
            if step.tool_result:
                parts.append(f"  Tool result: {step.tool_result}")
            if step.verification and not step.verification.passed:
                parts.append(f"  REJECTED: {'; '.join(step.verification.issues)}")
        return "\n".join(parts)
