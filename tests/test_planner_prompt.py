from math_agent.agent.planner import FormalizationPlanner


def test_system_prompt_mentions_context_fields():
    assert "variables" in FormalizationPlanner.SYSTEM
    assert "assumptions" in FormalizationPlanner.SYSTEM
    assert "instances" in FormalizationPlanner.SYSTEM


def test_system_prompt_never_instructs_null_for_string_fields():
    assert '"recommended_theorem": null' not in FormalizationPlanner.SYSTEM
    assert '"recommended_module": null' not in FormalizationPlanner.SYSTEM
    assert '"recommended_theorem": ""' in FormalizationPlanner.SYSTEM
    assert '"recommended_module": ""' in FormalizationPlanner.SYSTEM
