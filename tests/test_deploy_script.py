from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / "scripts" / "deploy_conjecta.sh"
README = ROOT / "README.md"
WORKFLOW = ROOT / "WORKFLOW.md"


def _deploy() -> str:
    return DEPLOY.read_text(encoding="utf-8")


def test_deploy_script_is_strict_locked_configurable_and_non_destructive():
    script = _deploy()

    assert script.startswith("#!/usr/bin/env bash\n")
    assert "set -Eeuo pipefail" in script
    assert "flock -n" in script
    for variable in (
        "CONJECTA_REPO_DIR",
        "CONJECTA_SERVICE_NAME",
        "CONJECTA_DEPLOY_REF",
        "CONJECTA_RUNTIME_ARCHIVE_DIR",
        "CONJECTA_NGINX_SITE",
    ):
        assert variable in script
    assert "set -x" not in script
    assert "reset --hard" not in script
    assert "git clean" not in script
    assert "rm -rf" not in script


def test_deploy_preserves_dirty_state_and_only_fast_forwards_exact_ref():
    script = _deploy()

    assert "git status --porcelain" in script
    assert re.search(r"git .*stash push .*--include-untracked", script)
    assert "date -u" in script
    assert "stash apply" in script
    assert "git fetch" in script
    assert "git switch main" in script
    assert "git merge --ff-only" in script
    assert "rev-parse" in script
    assert "target_commit" in script
    assert re.search(r"HEAD.*target_commit|target_commit.*HEAD", script)


def test_deploy_installs_builds_and_verifies_before_restart():
    script = _deploy()

    assert "sync --frozen --no-dev --python" in script
    assert "/usr/bin/python3.10" in script
    assert "UV_PROJECT_ENVIRONMENT" in script
    assert ".venv.release-" in script
    assert "ln -s" in script
    assert "unlink" in script
    assert "python3 -m venv" in script
    assert re.search(r'"\$PYTHON"\s+-m\s+pip\s+install', script)
    assert "npm ci" in script
    assert "npm run build" in script
    assert "CONJECTA_FRONTEND_OUT_DIR" in script
    assert "index.html" in script
    assert "/static/" in script
    assert 'diff -qr "$STATIC_DIR" "$STATIC_RELEASE"' not in script
    assert re.search(
        r'cmp\s+(?:--silent|-s)\s+"\$STATIC_DIR/index\.html"\s+"\$STATIC_RELEASE/index\.html"',
        script,
    )
    assert "nginx -t" in script
    assert re.search(r"systemctl\s+stop", script)
    assert re.search(r"systemctl\s+restart", script)
    assert re.search(r"systemctl\s+is-active", script)
    assert "healthz" in script
    assert re.search(r"for\s+\(\(.*attempt", script)
    assert "restore_previous_runtime" in script
    assert "Previous static assets remain archived" in script


def test_deploy_preflight_enforces_proxy_upload_and_pdf_runtime_boundaries():
    script = _deploy()

    assert "--no-proxy-headers" in script
    assert "--ws-max-size" in script
    assert "16777216" in script
    assert "client_max_body_size" in script
    assert "16m" in script
    assert "$remote_addr" in script
    assert "$scheme" in script
    assert "X-Forwarded-For" in script
    assert "$proxy_add_x_forwarded_for" not in script
    for command in ("pdfinfo", "pdftoppm"):
        assert re.search(rf"require_command\s+{command}\b", script)
        assert re.search(rf"{command}\s+-v", script)
    assert "poppler-utils" in script


def test_deploy_prepares_runtime_for_non_root_systemd_identity():
    script = _deploy()

    assert "--property=User" in script
    assert "--property=Group" in script
    assert "SERVICE_USER" in script and "SERVICE_GROUP" in script
    assert re.search(r'SERVICE_USER.*!=\s*"root"|"\$SERVICE_USER"\s*!=\s*"root"', script)
    assert re.search(r'chmod\s+0750\s+"\$RUNTIME_ARCHIVE_DIR"', script)
    assert re.search(
        r'chown\s+-R\s+"\$SERVICE_USER:\$SERVICE_GROUP"\s+"\$VENV_RELEASE"\s+"\$STATIC_RELEASE"',
        script,
    )


def test_deploy_makes_root_checked_out_source_readable_by_service_group():
    script = _deploy()

    assert "make_tracked_tree_service_readable" in script
    assert "git ls-files -z" in script
    assert re.search(r'chown\s+"root:\$\{SERVICE_GROUP\}"\s+"\$tracked_path"', script)
    assert re.search(r'chmod\s+g\+r\s+"\$tracked_path"', script)
    assert re.search(r'chmod\s+g\+rx\s+"\$parent"', script)
    assert script.index('git merge --ff-only "$target_commit"') < script.index(
        "make_tracked_tree_service_readable\n"
    )
    assert script.index("make_tracked_tree_service_readable\n") < script.index(
        'UV_PROJECT_ENVIRONMENT="$VENV_RELEASE"'
    )


def test_docs_match_the_single_engine_trust_and_manual_deploy_contracts():
    readme = README.read_text(encoding="utf-8")
    workflow = WORKFLOW.read_text(encoding="utf-8")
    combined = readme + "\n" + workflow

    for required in (
        "SupervisorAgent",
        "ReActAgent",
        "best_effort",
        "HttpOnly",
        "candidate",
        "approved",
        "verified",
        "supabase_knowledge_schema.sql",
        "supabase_tenant_schema.sql",
        "deploy_conjecta.sh",
        "--no-proxy-headers",
        "--ws-max-size 16777216",
        "client_max_body_size 16m",
        "poppler-utils",
    ):
        assert required in combined
    assert combined.index("supabase_knowledge_schema.sql") < combined.index(
        "supabase_tenant_schema.sql"
    )
    assert re.search(r"cookie[- ]only", combined, flags=re.IGNORECASE)
    assert re.search(r"do not.*(second|parallel|legacy).*solve", combined, flags=re.IGNORECASE)
