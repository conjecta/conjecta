from math_agent.agent.planner import FormalizationPlan


def test_plan_stores_context_fields():
    plan = FormalizationPlan(
        problem="Spectral theorem",
        variables=["{H : Type*}", "[InnerProductSpace ℝ H]"],
        assumptions=["hT : IsSelfAdjoint T"],
        instances=[],
    )
    assert plan.variables == ["{H : Type*}", "[InnerProductSpace ℝ H]"]
    assert plan.assumptions == ["hT : IsSelfAdjoint T"]
    assert plan.instances == []


def test_prompt_block_includes_context():
    plan = FormalizationPlan(
        restatement="Show spectral theorem",
        goal_type="...",
        variables=["{H : Type*}"],
        assumptions=["hT : IsSelfAdjoint T"],
    )
    block = plan.to_prompt_block()
    assert "Variables:" in block
    assert "{H : Type*}" in block
    assert "Assumptions:" in block
    assert "hT : IsSelfAdjoint T" in block
