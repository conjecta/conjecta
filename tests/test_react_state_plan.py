from math_agent.agent.react_state import ReActTrace


def test_plan_block_label():
    trace = ReActTrace(problem="Prove X", current_goal="Prove X", plan_text="step one")
    assert "Plan:\nstep one" in trace.context_window()


def test_plan_block_survives_preamble_budget_squeeze():
    # required blocks (problem 2008 + goal 2014 + plan ~2506 chars) must fit the
    # essential budget (12000 - 4000 reserve = 8000) while the optional preamble
    # (3000 clipped) is what gets dropped.
    trace = ReActTrace(problem="p" * 2000, current_goal="g" * 2000)
    trace.plan_text = "use induction " * 180
    trace.context_preamble = "c" * 9000
    window = trace.context_window(max_chars=12_000)
    assert "use induction" in window
