from math_agent.agent.prompts import build_react_system_prompt, current_time_context


def test_informal_prompt_does_not_force_lean_workflow():
    prompt = build_react_system_prompt(
        tool_descriptions="- compute(...)",
        require_formal_verification=False,
    )

    assert "Ordinary mathematical proofs should not be forced" in prompt
    assert "MUST use" not in prompt
    assert 'Conclude with {"answer": "..."}' in prompt
    assert "Current date (UTC):" in prompt
    assert current_time_context().split("(")[0].strip() in prompt


def test_formal_prompt_requires_explicit_evidence_binding():
    prompt = build_react_system_prompt(
        tool_descriptions="- lean_check(...)",
        require_formal_verification=True,
    )

    assert "Formal evidence ID" in prompt
    assert '"evidence_id": "formal-..."' in prompt
    assert "Evidence before a rejected" in prompt
    assert "conclusion cannot be reused" in prompt
    assert "Current date (UTC):" in prompt
