from __future__ import annotations

from math_agent.agent.planner import FormalizationPlan


class LeanContextBuilder:
    """Translate a FormalizationPlan into a Lean section context."""

    def build(self, plan: FormalizationPlan, body: str) -> str:
        header = ["section ProblemContext"]
        for v in plan.variables:
            header.append(f"  variable {v}")
        for a in plan.assumptions:
            header.append(f"  variable ({a})")
        for inst in plan.instances:
            header.append(f"  {inst}")
        footer = ["end ProblemContext"]
        return "\n".join(header) + "\n\n" + body + "\n\n" + "\n".join(footer)
