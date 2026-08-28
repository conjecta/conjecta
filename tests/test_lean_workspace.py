from __future__ import annotations

import asyncio
import os
import signal
import time
from unittest.mock import AsyncMock, patch

import pytest

from math_agent.config import LeanConfig
from math_agent.lean.runner import LeanRunner, _lean_subprocess_env
from math_agent.lean.workspace import LeanWorkspace


def _process_exists(pid: int) -> bool:
    """Return True if a process with ``pid`` is still alive."""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError, OSError):
        return False


@pytest.mark.asyncio
async def test_workspace_ensure_ready_runs_update_and_cache(tmp_path):
    config = LeanConfig(
        workspace_dir=str(tmp_path / "ws"),
        mathlib_dep=True,
        prefetch_cache=True,
    )
    workspace = LeanWorkspace(config)
    calls: list[tuple[str, ...]] = []

    async def fake_run_lake(self, *args: str, timeout: int):
        calls.append(args)
        if args and args[0] == "update":
            self.root.mkdir(parents=True, exist_ok=True)
            (self.root / "lake-manifest.json").write_text("{}", encoding="utf-8")
            lib_dir = (
                self.root / ".lake" / "packages" / "mathlib" / ".lake" / "build" / "lib" / "lean"
            )
            lib_dir.mkdir(parents=True, exist_ok=True)
            (lib_dir / "Mathlib.olean").write_text("olean", encoding="utf-8")
        return 0, "ok"

    with patch.object(LeanWorkspace, "_run_lake", fake_run_lake):
        result = await workspace.ensure_ready()

    assert result is None
    assert calls[0] == ("update",)
    assert calls[1] == ("exe", "cache", "get")
    assert workspace.is_ready()


@pytest.mark.asyncio
async def test_workspace_ensure_ready_fails_when_dependency_oleans_missing(tmp_path):
    """A workspace whose dependency packages produced no oleans must not be
    marked ready; the failure surfaces as infra (lean_unavailable)."""
    config = LeanConfig(
        workspace_dir=str(tmp_path / "ws"),
        mathlib_dep=True,
        prefetch_cache=True,
    )
    workspace = LeanWorkspace(config)

    async def fake_run_lake(self, *args: str, timeout: int):
        if args and args[0] == "update":
            self.root.mkdir(parents=True, exist_ok=True)
            (self.root / "lake-manifest.json").write_text("{}", encoding="utf-8")
            (self.root / ".lake" / "packages" / "mathlib").mkdir(parents=True, exist_ok=True)
        # `lake exe cache get` "succeeds" but no oleans materialize.
        return 0, "ok"

    with patch.object(LeanWorkspace, "_run_lake", fake_run_lake):
        result = await workspace.ensure_ready()

    assert result is not None
    assert result.success is False
    assert result.failure_kind == "lean_unavailable"
    assert not workspace.is_ready()


@pytest.mark.asyncio
async def test_workspace_ensure_ready_builds_cache_straggler_packages(tmp_path):
    """`lake exe cache get` covers mathlib but not app-side packages (e.g.
    Cli, used only by mathlib's executables); ensure_ready must build those
    stragglers instead of declaring the workspace unavailable."""
    config = LeanConfig(
        workspace_dir=str(tmp_path / "ws"),
        mathlib_dep=True,
        prefetch_cache=True,
    )
    workspace = LeanWorkspace(config)
    calls: list[tuple[str, ...]] = []

    def _add_olean(root, package):
        lib_dir = root / ".lake" / "packages" / package / ".lake" / "build" / "lib" / "lean"
        lib_dir.mkdir(parents=True, exist_ok=True)
        (lib_dir / f"{package}.olean").write_text("olean", encoding="utf-8")

    async def fake_run_lake(self, *args: str, timeout: int):
        calls.append(args)
        if args and args[0] == "update":
            self.root.mkdir(parents=True, exist_ok=True)
            (self.root / "lake-manifest.json").write_text("{}", encoding="utf-8")
            # Cli exists as a package dir but the cache never covers it.
            (self.root / ".lake" / "packages" / "Cli").mkdir(parents=True, exist_ok=True)
        if args[:2] == ("exe", "cache"):
            _add_olean(self.root, "mathlib")
        if args and args[0] == "build":
            _add_olean(self.root, args[1])
        return 0, "ok"

    with patch.object(LeanWorkspace, "_run_lake", fake_run_lake):
        result = await workspace.ensure_ready()

    assert result is None
    assert ("build", "Cli") in calls
    assert workspace.is_ready()


@pytest.mark.asyncio
async def test_workspace_ensure_ready_fails_when_straggler_build_fails(tmp_path):
    """If the straggler build itself fails, the workspace still surfaces an
    infra failure rather than a bogus proof error later."""
    config = LeanConfig(
        workspace_dir=str(tmp_path / "ws"),
        mathlib_dep=True,
        prefetch_cache=True,
    )
    workspace = LeanWorkspace(config)

    async def fake_run_lake(self, *args: str, timeout: int):
        if args and args[0] == "update":
            self.root.mkdir(parents=True, exist_ok=True)
            (self.root / "lake-manifest.json").write_text("{}", encoding="utf-8")
            (self.root / ".lake" / "packages" / "Cli").mkdir(parents=True, exist_ok=True)
        if args[:2] == ("exe", "cache"):
            lib_dir = (
                self.root / ".lake" / "packages" / "mathlib" / ".lake" / "build" / "lib" / "lean"
            )
            lib_dir.mkdir(parents=True, exist_ok=True)
            (lib_dir / "Mathlib.olean").write_text("olean", encoding="utf-8")
        if args and args[0] == "build":
            return 1, "lake error: build failed"
        return 0, "ok"

    with patch.object(LeanWorkspace, "_run_lake", fake_run_lake):
        result = await workspace.ensure_ready()

    assert result is not None
    assert result.success is False
    assert result.failure_kind == "lean_unavailable"
    assert not workspace.is_ready()


def test_workspace_is_ready_requires_dependency_oleans(tmp_path):
    """Direct is_ready check: marker/manifest/fingerprint present, but the
    olean health gate decides readiness."""
    config = LeanConfig(workspace_dir=str(tmp_path / "ws"), mathlib_dep=True)
    workspace = LeanWorkspace(config)
    workspace.root.mkdir(parents=True, exist_ok=True)
    (workspace.root / "lake-manifest.json").write_text("{}", encoding="utf-8")
    lib_dir = (
        workspace.root / ".lake" / "packages" / "mathlib" / ".lake" / "build" / "lib" / "lean"
    )
    lib_dir.mkdir(parents=True, exist_ok=True)
    workspace.mark_ready()

    # No oleans yet: not ready even though the marker exists.
    assert not workspace.is_ready()

    (lib_dir / "Mathlib.olean").write_text("olean", encoding="utf-8")
    assert workspace.is_ready()


@pytest.mark.asyncio
async def test_runner_ensure_dependencies_skips_when_no_mathlib(tmp_path):
    config = LeanConfig(workspace_dir=str(tmp_path / "ws"), mathlib_dep=False)
    runner = LeanRunner(config)
    assert await runner.ensure_dependencies() is None


@pytest.mark.asyncio
async def test_runner_check_proof_uses_workspace(tmp_path):
    config = LeanConfig(workspace_dir=str(tmp_path / "ws"), mathlib_dep=True)
    runner = LeanRunner(config)

    async def fake_ensure(*, force: bool = False):
        return None

    async def fake_check(lean_code: str, *, draft: bool = False):
        assert lean_code == "theorem t : True := trivial"
        from math_agent.lean.result import LeanResult

        return LeanResult(success=True)

    runner.ensure_dependencies = fake_ensure  # type: ignore[method-assign]
    runner._check_in_workspace = fake_check  # type: ignore[method-assign]

    result = await runner.check_proof("theorem t : True := trivial")
    assert result.success is True


@pytest.mark.asyncio
async def test_runner_uses_unique_job_files_across_instances(tmp_path):
    config = LeanConfig(
        workspace_dir=str(tmp_path / "ws"),
        mathlib_dep=True,
        max_concurrent_checks=2,
    )
    first = LeanRunner(config)
    second = LeanRunner(config)
    seen = []

    async def fake_run_command(*, project_dir, proof_file, lean_code, command, draft=False):
        assert proof_file.exists()
        assert proof_file.read_text(encoding="utf-8") == lean_code
        seen.append(proof_file)
        await __import__("asyncio").sleep(0)
        from math_agent.lean.result import LeanResult

        return LeanResult(success=True)

    first._run_command = fake_run_command  # type: ignore[method-assign]
    second._run_command = fake_run_command  # type: ignore[method-assign]

    await __import__("asyncio").gather(
        first._check_in_workspace("theorem first : True := trivial"),
        second._check_in_workspace("theorem second : True := trivial"),
    )

    assert len(seen) == 2
    assert seen[0] != seen[1]
    assert not any(path.exists() for path in seen)


@pytest.mark.asyncio
async def test_runner_result_cache_is_shared_across_instances(tmp_path):
    config = LeanConfig(
        workspace_dir=str(tmp_path / "shared-cache"),
        mathlib_dep=True,
    )
    first = LeanRunner(config)
    second = LeanRunner(config)
    calls = 0

    async def fake_ensure(*, force: bool = False):
        return None

    async def fake_check(lean_code: str, *, draft: bool = False):
        nonlocal calls
        calls += 1
        from math_agent.lean.result import LeanResult

        return LeanResult(success=True, output="checked")

    first.ensure_dependencies = fake_ensure  # type: ignore[method-assign]
    second.ensure_dependencies = fake_ensure  # type: ignore[method-assign]
    first._check_in_workspace = fake_check  # type: ignore[method-assign]
    second._check_in_workspace = fake_check  # type: ignore[method-assign]
    code = "theorem shared_cache_result : True := trivial"

    assert (await first.check_proof(code)).success is True
    cached = await second.check_proof(code)

    assert cached.success is True
    assert "cached result" in cached.output
    assert calls == 1


def test_lean_subprocess_environment_does_not_expose_secrets(monkeypatch):
    monkeypatch.setenv("PATH", "/usr/bin")
    monkeypatch.setenv("OPENAI_API_KEY", "secret")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "secret")

    env = _lean_subprocess_env()

    assert env["PATH"] == "/usr/bin"
    assert env["CONJECTA_LEAN_RESTRICTED"] == "1"
    assert "OPENAI_API_KEY" not in env
    assert "SUPABASE_SERVICE_ROLE_KEY" not in env


@pytest.mark.asyncio
async def test_run_lake_uses_process_group_and_killpg_on_timeout(tmp_path):
    config = LeanConfig(workspace_dir=str(tmp_path / "ws"))
    workspace = LeanWorkspace(config)

    fake_proc = AsyncMock()
    fake_proc.pid = 12345
    fake_proc.returncode = None
    fake_proc.communicate.side_effect = asyncio.TimeoutError

    with patch(
        "math_agent.lean.workspace.asyncio.create_subprocess_exec",
        new=AsyncMock(return_value=fake_proc),
    ) as mock_exec, patch("math_agent.lean.workspace.os.killpg") as mock_killpg:
        code, output = await workspace._run_lake("update", timeout=1)

    assert code == -1
    assert "timed out" in output
    mock_exec.assert_awaited_once()
    assert mock_exec.call_args.kwargs.get("start_new_session") is True
    mock_killpg.assert_called_once_with(12345, signal.SIGKILL)
    fake_proc.wait.assert_awaited()


@pytest.mark.asyncio
async def test_run_lake_uses_process_group_and_killpg_on_cancel(tmp_path):
    config = LeanConfig(workspace_dir=str(tmp_path / "ws"))
    workspace = LeanWorkspace(config)

    fake_proc = AsyncMock()
    fake_proc.pid = 12345
    fake_proc.returncode = None
    fake_proc.communicate.side_effect = asyncio.CancelledError

    with patch(
        "math_agent.lean.workspace.asyncio.create_subprocess_exec",
        new=AsyncMock(return_value=fake_proc),
    ) as mock_exec, patch("math_agent.lean.workspace.os.killpg") as mock_killpg:
        with pytest.raises(asyncio.CancelledError):
            await workspace._run_lake("update", timeout=1)

    mock_exec.assert_awaited_once()
    assert mock_exec.call_args.kwargs.get("start_new_session") is True
    mock_killpg.assert_called_once_with(12345, signal.SIGKILL)
    fake_proc.wait.assert_awaited()


@pytest.mark.asyncio
async def test_run_lake_timeout_cleans_up_real_child_process(tmp_path):
    """A real helper that spawns a child ``sleep`` must not leave orphans."""
    pid_file = tmp_path / "pids.txt"
    helper = tmp_path / "fake_lake.sh"
    helper.write_text(
        "#!/usr/bin/env bash\n"
        "sleep 3600 &\n"
        "child=$!\n"
        f"{{ echo \"leader_pid=$$\"; echo \"child_pid=$child\"; }} > {pid_file}\n"
        "sleep 10\n",
        encoding="utf-8",
    )
    helper.chmod(0o755)

    ws_root = tmp_path / "ws"
    ws_root.mkdir(parents=True, exist_ok=True)
    config = LeanConfig(workspace_dir=str(ws_root), lake_path=str(helper))
    workspace = LeanWorkspace(config)

    code, output = await workspace._run_lake("update", timeout=1)

    assert code == -1
    assert "timed out" in output

    pid_text = pid_file.read_text(encoding="utf-8")
    leader_pid = int(pid_text.split("leader_pid=")[1].split()[0])
    child_pid = int(pid_text.split("child_pid=")[1].split()[0])

    # Give the kernel a moment to reap the processes.
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and (
        _process_exists(leader_pid) or _process_exists(child_pid)
    ):
        await asyncio.sleep(0.05)

    assert not _process_exists(leader_pid), f"leader process {leader_pid} survived"
    assert not _process_exists(child_pid), f"child process {child_pid} survived"
