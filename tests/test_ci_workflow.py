from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CI = ROOT / ".github" / "workflows" / "ci.yml"
LEAN_CI = ROOT / ".github" / "workflows" / "lean-integration.yml"


def _workflow() -> str:
    return CI.read_text(encoding="utf-8")


def test_ci_uses_least_privilege_and_a_current_node_20_frontend_job():
    workflow = _workflow()

    assert re.search(r"(?m)^permissions:\s*\n\s+contents:\s*read\s*$", workflow)
    assert not re.search(r"(?m)^\s+[a-z-]+:\s*write\s*$", workflow)
    assert re.search(r"(?m)^\s{2}frontend:\s*$", workflow)
    assert "actions/setup-node@v4" in workflow
    node = re.search(r"node-version:\s*['\"]?([0-9]+(?:\.[0-9]+){0,2})", workflow)
    assert node, "frontend CI must pin a Node version"
    major, *rest = [int(part) for part in node.group(1).split(".")]
    minor = rest[0] if rest else 0
    assert (major, minor) >= (20, 19)


def test_frontend_ci_runs_install_checks_tests_build_and_audit():
    workflow = _workflow()

    for command in (
        "npm ci",
        "npm run typecheck",
        "npm test -- --run",
        "npm run build",
        "npm audit --audit-level=low",
    ):
        assert command in workflow
    assert "math_agent/web/frontend" in workflow
    assert "CONJECTA_FRONTEND_OUT_DIR" in workflow
    assert "runner.temp" in workflow


def test_lean_gate_uses_portable_token_boundaries_and_handles_grep_errors():
    workflow = _workflow()

    for unsupported in ("(?<!", "(?<=", "(?!", "(?="):
        assert unsupported not in workflow
    assert "(^|[^[:alnum:]_])" in workflow
    assert "([^[:alnum:]_]|$)" in workflow
    assert "grep_status=$?" in workflow
    assert re.search(r'case\s+"\$grep_status"\s+in', workflow)
    assert re.search(r"(?m)^\s*0\)\s*$", workflow)
    assert re.search(r"(?m)^\s*1\)\s*$", workflow)
    assert re.search(r"(?m)^\s*\*\)\s*$", workflow)
    assert "grep failed" in workflow.lower()
    assert "Lean file discovery failed" in workflow
    assert re.search(r"if\s+!\s+find\b", workflow)


def test_lean_integration_is_isolated_from_default_python_ci():
    workflow = _workflow()
    lean_workflow = LEAN_CI.read_text(encoding="utf-8")

    assert '-m "not integration"' in workflow
    assert "workflow_dispatch:" in lean_workflow
    assert "actions/cache@v4" in lean_workflow
    assert "math-agent-lean-setup" in lean_workflow
    assert "-m integration" in lean_workflow
