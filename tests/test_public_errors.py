from math_agent.web.public_errors import DEFAULT_SOLVE_ERROR, public_solve_error


def test_public_solve_error_never_exposes_upstream_details():
    internal = RuntimeError(
        "502 from relay.vendor.example/v1: invalid upstream token sk-secret"
    )

    message = public_solve_error(internal)

    assert message == DEFAULT_SOLVE_ERROR
    assert "relay" not in message
    assert "vendor" not in message
    assert "token" not in message


def test_public_solve_error_keeps_only_actionable_status_categories():
    assert public_solve_error(status_code=429) == "当前请求较多，请稍后重试。"
    assert "附件" in public_solve_error(status_code=413)
