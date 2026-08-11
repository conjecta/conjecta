from math_agent.web.app import prefix_history


def test_no_history_returns_problem_unchanged():
    assert prefix_history("new q", []) == "new q"


def test_history_is_rendered_above_problem():
    hist = [{"role": "user", "text": "prove sqrt2 irrational"},
            {"role": "assistant", "text": "It is irrational."}]
    out = prefix_history("why?", hist)
    assert "prove sqrt2 irrational" in out
    assert "It is irrational." in out
    assert out.strip().endswith("why?")
