from math_agent.agent.planner import FormalizationPlan
from math_agent.lean.context_builder import LeanContextBuilder


def test_build_wraps_body_with_section():
    plan = FormalizationPlan(
        variables=["{H : Type*}", "[InnerProductSpace ℝ H]"],
        assumptions=["hT : IsSelfAdjoint T"],
    )
    builder = LeanContextBuilder()
    body = "lemma foo : True := trivial"
    out = builder.build(plan, body)
    assert "section ProblemContext" in out
    assert "variable {H : Type*}" in out
    assert "variable [InnerProductSpace ℝ H]" in out
    assert "variable (hT : IsSelfAdjoint T)" in out
    assert body in out
    assert "end ProblemContext" in out
    assert out.index("section ProblemContext") < out.index(body) < out.index("end ProblemContext")


def test_empty_context_still_wraps():
    plan = FormalizationPlan()
    out = LeanContextBuilder().build(plan, "lemma foo : True := trivial")
    assert "section ProblemContext" in out
    assert "end ProblemContext" in out
    assert "lemma foo : True := trivial" in out
