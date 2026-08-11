from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from math_agent.config import LeanConfig
from math_agent.lean.result import LeanResult
from math_agent.lean.runner import LeanRunner, _RESULT_CACHE


@pytest.fixture(autouse=True)
def _clear_result_cache():
    _RESULT_CACHE.clear()
    yield
    _RESULT_CACHE.clear()


@pytest.mark.asyncio
async def test_run_command_uses_sorry_detects_bare_sorry(tmp_path):
    config = LeanConfig(workspace_dir=str(tmp_path / "ws"), mathlib_dep=False)
    runner = LeanRunner(config)
    source = "theorem t : True := by sorry"
    proof_file = tmp_path / "Proof.lean"
    proof_file.write_text(source, encoding="utf-8")

    result = await runner._run_command(
        project_dir=tmp_path,
        proof_file=proof_file,
        lean_code=source,
        command=("lake", "env", "lean", str(proof_file)),
    )

    assert result.uses_sorry is True


@pytest.mark.asyncio
async def test_run_command_uses_sorry_ignores_comment_and_string(tmp_path):
    config = LeanConfig(workspace_dir=str(tmp_path / "ws"), mathlib_dep=False)
    runner = LeanRunner(config)
    source = 'theorem t : True := by trivial\n-- sorry\ndef s := "admit"'
    proof_file = tmp_path / "Proof.lean"
    proof_file.write_text(source, encoding="utf-8")

    fake_proc = AsyncMock()
    fake_proc.returncode = 0
    fake_proc.communicate.return_value = (b"", b"")

    with patch(
        "math_agent.lean.runner.asyncio.create_subprocess_exec",
        new=AsyncMock(return_value=fake_proc),
    ):
        result = await runner._run_command(
            project_dir=tmp_path,
            proof_file=proof_file,
            lean_code=source,
            command=("lake", "env", "lean", str(proof_file)),
        )

    assert result.static_ok is True
    assert result.uses_sorry is False


@pytest.mark.asyncio
async def test_run_command_uses_sorry_ignores_char_literal_quote(tmp_path):
    """`def q : Char := '"'` must not cause a false positive for `sorry`."""
    config = LeanConfig(workspace_dir=str(tmp_path / "ws"), mathlib_dep=False)
    runner = LeanRunner(config)
    source = 'def q : Char := \'"\'\ntheorem t : True := by trivial'
    proof_file = tmp_path / "Proof.lean"
    proof_file.write_text(source, encoding="utf-8")

    fake_proc = AsyncMock()
    fake_proc.returncode = 0
    fake_proc.communicate.return_value = (b"", b"")

    with patch(
        "math_agent.lean.runner.asyncio.create_subprocess_exec",
        new=AsyncMock(return_value=fake_proc),
    ):
        result = await runner._run_command(
            project_dir=tmp_path,
            proof_file=proof_file,
            lean_code=source,
            command=("lake", "env", "lean", str(proof_file)),
        )

    assert result.static_ok is True
    assert result.uses_sorry is False


@pytest.mark.asyncio
async def test_check_proof_does_not_cache_timeout(tmp_path):
    config = LeanConfig(workspace_dir=str(tmp_path / "ws"), mathlib_dep=False)
    runner = LeanRunner(config)
    calls = 0

    async def fake_run_command(**kwargs):
        nonlocal calls
        calls += 1
        return LeanResult(
            success=False,
            failure_kind="timeout",
            output="timed out",
        )

    runner._run_command = fake_run_command  # type: ignore[method-assign]
    code = "theorem t : True := by trivial"

    first = await runner.check_proof(code)
    second = await runner.check_proof(code)

    assert first.failure_kind == "timeout"
    assert second.failure_kind == "timeout"
    assert calls == 2
    assert "(cached result)" not in second.output


@pytest.mark.asyncio
async def test_check_proof_does_not_cache_lean_unavailable(tmp_path):
    config = LeanConfig(workspace_dir=str(tmp_path / "ws"), mathlib_dep=False)
    runner = LeanRunner(config)
    calls = 0

    async def fake_run_command(**kwargs):
        nonlocal calls
        calls += 1
        return LeanResult(
            success=False,
            failure_kind="lean_unavailable",
            output="lean missing",
        )

    runner._run_command = fake_run_command  # type: ignore[method-assign]
    code = "theorem t : True := by trivial"

    first = await runner.check_proof(code)
    second = await runner.check_proof(code)

    assert first.failure_kind == "lean_unavailable"
    assert second.failure_kind == "lean_unavailable"
    assert calls == 2
    assert "(cached result)" not in second.output


@pytest.mark.asyncio
async def test_check_proof_caches_successful_result(tmp_path):
    config = LeanConfig(workspace_dir=str(tmp_path / "ws"), mathlib_dep=False)
    runner = LeanRunner(config)
    calls = 0

    async def fake_run_command(**kwargs):
        nonlocal calls
        calls += 1
        return LeanResult(success=True, output="ok")

    runner._run_command = fake_run_command  # type: ignore[method-assign]
    code = "theorem t : True := by trivial"

    first = await runner.check_proof(code)
    second = await runner.check_proof(code)

    assert first.success is True
    assert second.success is True
    assert calls == 1
    assert "(cached result)" in second.output


@pytest.mark.asyncio
async def test_run_command_draft_mode_accepts_sorry_skeleton(tmp_path):
    """In draft mode a sorry skeleton passes the static gate and compiles."""
    config = LeanConfig(workspace_dir=str(tmp_path / "ws"), mathlib_dep=False)
    runner = LeanRunner(config)
    source = "theorem t : True := by sorry"
    proof_file = tmp_path / "Proof.lean"
    proof_file.write_text(source, encoding="utf-8")

    fake_proc = AsyncMock()
    fake_proc.returncode = 0
    fake_proc.communicate.return_value = (
        b"",
        b"Proof.lean:1:24: warning: declaration uses 'sorry'",
    )

    with patch(
        "math_agent.lean.runner.asyncio.create_subprocess_exec",
        new=AsyncMock(return_value=fake_proc),
    ):
        result = await runner._run_command(
            project_dir=tmp_path,
            proof_file=proof_file,
            lean_code=source,
            command=("lake", "env", "lean", str(proof_file)),
            draft=True,
        )

    assert result.static_ok is True
    assert result.success is True
    assert result.uses_sorry is True
    assert result.draft is True


@pytest.mark.asyncio
async def test_run_command_strict_mode_rejects_sorry_skeleton(tmp_path):
    """Without draft the static gate still blocks sorry before compiling."""
    config = LeanConfig(workspace_dir=str(tmp_path / "ws"), mathlib_dep=False)
    runner = LeanRunner(config)
    source = "theorem t : True := by sorry"
    proof_file = tmp_path / "Proof.lean"
    proof_file.write_text(source, encoding="utf-8")

    result = await runner._run_command(
        project_dir=tmp_path,
        proof_file=proof_file,
        lean_code=source,
        command=("lake", "env", "lean", str(proof_file)),
    )

    assert result.success is False
    assert result.static_ok is False
    assert result.draft is False


@pytest.mark.asyncio
async def test_check_proof_caches_draft_and_strict_separately(tmp_path):
    """A draft pass must never be served as a strict pass from the cache."""
    config = LeanConfig(workspace_dir=str(tmp_path / "ws"), mathlib_dep=False)
    runner = LeanRunner(config)
    calls = 0

    async def fake_run_command(**kwargs):
        nonlocal calls
        calls += 1
        return LeanResult(success=True, output="ok", draft=kwargs.get("draft", False))

    runner._run_command = fake_run_command  # type: ignore[method-assign]
    code = "theorem t : True := by trivial"

    strict = await runner.check_proof(code)
    draft = await runner.check_proof(code, draft=True)
    strict_cached = await runner.check_proof(code)
    draft_cached = await runner.check_proof(code, draft=True)

    assert calls == 2
    assert strict.draft is False
    assert draft.draft is True
    assert "(cached result)" in strict_cached.output
    assert "(cached result)" in draft_cached.output
