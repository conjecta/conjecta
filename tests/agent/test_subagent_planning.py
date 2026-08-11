from math_agent.agent.subagent import SubagentSpec, build_subagent_config
from math_agent.config import AgentConfig


def test_subagent_config_disables_planning():
    config = build_subagent_config(AgentConfig(planning_enabled=True), SubagentSpec())
    assert config.planning_enabled is False


def test_subagent_config_keeps_other_overrides():
    config = build_subagent_config(
        AgentConfig(), SubagentSpec(max_steps=4, max_tool_calls=2)
    )
    assert config.max_react_steps == 4
    assert config.max_tool_calls == 2
