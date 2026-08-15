"""Tests for the bwrap isolation layer and the sandbox launch rewiring."""
from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path

import pytest

import math_agent.tools.plot_sandbox as plot_sandbox
import math_agent.tools.python_sandbox as python_sandbox
from math_agent.tools import isolated_runner
from math_agent.tools.isolated_runner import (
    bwrap_available,
    build_bwrap_argv,
    isolation_mode,
)

# ---------------------------------------------------------------------------
# isolation_mode / bwrap_available
# ---------------------------------------------------------------------------


def test_isolation_mode_defaults_to_auto(monkeypatch):
    monkeypatch.delenv(isolated_runner.ISOLATION_ENV_VAR, raising=False)
    assert isolation_mode() == "auto"


@pytest.mark.parametrize("value", ["auto", "bwrap", "none", " BWRAP ", "None"])
def test_isolation_mode_parses_valid_values(monkeypatch, value):
    monkeypatch.setenv(isolated_runner.ISOLATION_ENV_VAR, value)
    assert isolation_mode() == value.strip().lower()


def test_isolation_mode_invalid_falls_back_to_auto(monkeypatch, caplog):
    monkeypatch.setenv(isolated_runner.ISOLATION_ENV_VAR, "docker")
    with caplog.at_level(logging.WARNING, logger=isolated_runner.__name__):
        assert isolation_mode() == "auto"
    assert any("auto|bwrap|none" in rec.message for rec in caplog.records)


def test_bwrap_available_caches_lookup(monkeypatch):
    monkeypatch.setattr(isolated_runner, "_bwrap_checked", False)
    monkeypatch.setattr(
        isolated_runner.shutil, "which", lambda name: "/usr/bin/bwrap"
    )
    assert bwrap_available() is True
    # A later PATH change does not matter: the result is cached.
    monkeypatch.setattr(isolated_runner.shutil, "which", lambda name: None)
    assert bwrap_available() is True
    monkeypatch.setattr(isolated_runner, "_bwrap_checked", False)
    assert bwrap_available() is False


# ---------------------------------------------------------------------------
# build_bwrap_argv
# ---------------------------------------------------------------------------


def _ro_bind_pairs(cmd):
    return [
        (cmd[i + 1], cmd[i + 2]) for i, arg in enumerate(cmd) if arg == "--ro-bind"
    ]


def test_build_bwrap_argv_core_flags(tmp_path):
    cmd = build_bwrap_argv(
        argv=["/bin/echo", "hi"], writable_dirs=[tmp_path], workdir=tmp_path
    )
    assert cmd[0].endswith("bwrap")
    for flag in (
        "--unshare-all",
        "--die-with-parent",
        "--new-session",
        "--clearenv",
        "--unshare-net",
        "--cap-drop",
        "--chdir",
        "--proc",
        "--tmpfs",
        "--setenv",
    ):
        assert flag in cmd
    assert cmd[cmd.index("--cap-drop") + 1] == "ALL"
    assert cmd[cmd.index("--proc") + 1] == "/proc"
    assert cmd[cmd.index("--tmpfs") + 1] == "/tmp"
    assert cmd[cmd.index("--chdir") + 1] == str(tmp_path.resolve())
    # The payload follows a literal "--" separator.
    sep = cmd.index("--")
    assert cmd[sep + 1 :] == ["/bin/echo", "hi"]


def test_build_bwrap_argv_network_off_by_default(tmp_path):
    cmd = build_bwrap_argv(argv=["/bin/true"], writable_dirs=[], workdir=tmp_path)
    assert "--unshare-net" in cmd


def test_build_bwrap_argv_network_enabled_omits_unshare_net(tmp_path):
    cmd = build_bwrap_argv(
        argv=["/bin/true"], writable_dirs=[], workdir=tmp_path, network=True
    )
    assert "--unshare-net" not in cmd


def test_build_bwrap_argv_binds_writable_dirs_rw(tmp_path):
    one = tmp_path / "one"
    two = tmp_path / "two"
    cmd = build_bwrap_argv(
        argv=["/bin/true"], writable_dirs=[one, two], workdir=tmp_path
    )
    rw_pairs = [
        (cmd[i + 1], cmd[i + 2]) for i, arg in enumerate(cmd) if arg == "--bind"
    ]
    assert (str(one.resolve()), str(one.resolve())) in rw_pairs
    assert (str(two.resolve()), str(two.resolve())) in rw_pairs


def test_build_bwrap_argv_binds_python_runtime_readonly(tmp_path):
    cmd = build_bwrap_argv(argv=["/bin/true"], writable_dirs=[], workdir=tmp_path)
    pairs = _ro_bind_pairs(cmd)
    # System runtime essentials.
    assert ("/usr", "/usr") in pairs
    # The venv holding sys.executable (and its site-packages) is ro-bound.
    assert (str(Path(sys.prefix).resolve()),) * 2 in pairs
    # No duplicate bind sources.
    sources = [src for src, _ in pairs]
    assert len(sources) == len(set(sources))


def test_build_bwrap_argv_minimal_env(tmp_path):
    cmd = build_bwrap_argv(
        argv=["/bin/true"],
        writable_dirs=[],
        workdir=tmp_path,
        extra_env={"MPLCONFIGDIR": "/some/dir"},
    )
    setenv = {}
    for i, arg in enumerate(cmd):
        if arg == "--setenv":
            setenv[cmd[i + 1]] = cmd[i + 2]
    assert setenv["HOME"] == "/tmp"
    assert "PATH" in setenv
    assert setenv["MPLCONFIGDIR"] == "/some/dir"
    # The child environment is cleared first, so nothing else leaks in.
    assert "--clearenv" in cmd


# ---------------------------------------------------------------------------
# Sandbox launch rewiring
# ---------------------------------------------------------------------------


class _FakeProc:
    def __init__(self, stdout: bytes = b"", stderr: bytes = b""):
        self._stdout = stdout
        self._stderr = stderr
        self.returncode = 0

    async def communicate(self):
        return self._stdout, self._stderr


@pytest.mark.asyncio
async def test_python_sandbox_wraps_command_when_isolated(monkeypatch):
    monkeypatch.setenv(isolated_runner.ISOLATION_ENV_VAR, "bwrap")
    monkeypatch.setattr(isolated_runner, "bwrap_available", lambda: True)
    captured = {}

    async def fake_create(*args, **kwargs):
        captured["args"] = list(args)
        captured["kwargs"] = kwargs
        return _FakeProc(stdout=b"4\n")

    monkeypatch.setattr(
        python_sandbox.asyncio, "create_subprocess_exec", fake_create
    )
    result = await python_sandbox.run_python("print(2 + 2)")
    assert result.success
    assert "4" in result.output
    argv = captured["args"]
    assert argv[0].endswith("bwrap")
    assert "--unshare-net" in argv
    sep = argv.index("--")
    assert argv[sep + 1] == sys.executable
    assert argv[sep + 2] == "-c"
    # bwrap manages the child environment itself.
    assert captured["kwargs"]["env"] is None


@pytest.mark.asyncio
async def test_python_sandbox_falls_back_and_warns_once(monkeypatch, caplog):
    monkeypatch.setenv(isolated_runner.ISOLATION_ENV_VAR, "auto")
    monkeypatch.setattr(isolated_runner, "bwrap_available", lambda: False)
    isolated_runner._warned_no_isolation.clear()
    captured = {}

    async def fake_create(*args, **kwargs):
        captured["args"] = list(args)
        captured["kwargs"] = kwargs
        return _FakeProc(stdout=b"4\n")

    monkeypatch.setattr(
        python_sandbox.asyncio, "create_subprocess_exec", fake_create
    )
    with caplog.at_level(logging.WARNING, logger=isolated_runner.__name__):
        result = await python_sandbox.run_python("print(2 + 2)")
    assert result.success
    # Fallback launches the interpreter directly with the legacy env.
    assert captured["args"][0] == sys.executable
    assert captured["kwargs"]["env"]["PYTHONPATH"] == ""
    warnings = [
        rec for rec in caplog.records if "not a container boundary" in rec.message
    ]
    assert len(warnings) == 1
    # The warning is emitted once, not per call.
    caplog.clear()
    await python_sandbox.run_python("print(3)")
    assert not caplog.records


@pytest.mark.asyncio
async def test_python_sandbox_mode_none_never_wraps(monkeypatch):
    monkeypatch.setenv(isolated_runner.ISOLATION_ENV_VAR, "none")
    monkeypatch.setattr(isolated_runner, "bwrap_available", lambda: True)
    isolated_runner._warned_no_isolation.clear()
    captured = {}

    async def fake_create(*args, **kwargs):
        captured["args"] = list(args)
        captured["kwargs"] = kwargs
        return _FakeProc(stdout=b"ok\n")

    monkeypatch.setattr(
        python_sandbox.asyncio, "create_subprocess_exec", fake_create
    )
    result = await python_sandbox.run_python("print('ok')")
    assert result.success
    assert captured["args"][0] == sys.executable
    assert captured["kwargs"]["env"] is not None


@pytest.mark.asyncio
async def test_plot_sandbox_wraps_command_when_isolated(monkeypatch, tmp_path):
    monkeypatch.setenv(isolated_runner.ISOLATION_ENV_VAR, "bwrap")
    monkeypatch.setattr(isolated_runner, "bwrap_available", lambda: True)
    captured = {}

    async def fake_create(*args, **kwargs):
        captured["args"] = list(args)
        captured["kwargs"] = kwargs
        return _FakeProc(stdout=b"\n__PLOT_FIGURES__[]\n")

    monkeypatch.setattr(plot_sandbox.asyncio, "create_subprocess_exec", fake_create)
    # No figures produced by the fake child -> failure, but the launch is
    # what this test asserts.
    await plot_sandbox.run_plot("print('hi')", out_dir=tmp_path)
    argv = captured["args"]
    assert argv[0].endswith("bwrap")
    assert "--unshare-net" in argv
    rw_pairs = [
        (argv[i + 1], argv[i + 2]) for i, arg in enumerate(argv) if arg == "--bind"
    ]
    assert (str(tmp_path.resolve()),) * 2 in rw_pairs
    assert argv[argv.index("--chdir") + 1] == str(tmp_path.resolve())
    setenv = {}
    for i, arg in enumerate(argv):
        if arg == "--setenv":
            setenv[argv[i + 1]] = argv[i + 2]
    assert setenv["MPLCONFIGDIR"] == str((tmp_path / ".mplconfig").resolve())
    assert captured["kwargs"]["env"] is None


@pytest.mark.asyncio
async def test_plot_sandbox_falls_back_when_bwrap_missing(monkeypatch, tmp_path):
    monkeypatch.setenv(isolated_runner.ISOLATION_ENV_VAR, "auto")
    monkeypatch.setattr(isolated_runner, "bwrap_available", lambda: False)
    isolated_runner._warned_no_isolation.clear()
    captured = {}

    async def fake_create(*args, **kwargs):
        captured["args"] = list(args)
        captured["kwargs"] = kwargs
        return _FakeProc(stdout=b"\n__PLOT_FIGURES__[]\n")

    monkeypatch.setattr(plot_sandbox.asyncio, "create_subprocess_exec", fake_create)
    await plot_sandbox.run_plot("print('hi')", out_dir=tmp_path)
    assert captured["args"][0] == sys.executable
    assert captured["kwargs"]["env"]["MPLCONFIGDIR"] == str(
        (tmp_path / ".mplconfig").resolve()
    )


# ---------------------------------------------------------------------------
# Integration tests (require a real bwrap binary)
# ---------------------------------------------------------------------------

requires_bwrap = pytest.mark.skipif(
    not bwrap_available(), reason="bwrap is not installed"
)


@requires_bwrap
def test_bwrap_cannot_read_host_files(tmp_path):
    # The repo root is not bound into the namespace, so this file — readable
    # on the host — must not exist for the child.
    target = Path(__file__).resolve()
    cmd = build_bwrap_argv(
        argv=["/bin/cat", str(target)], writable_dirs=[tmp_path], workdir=tmp_path
    )
    proc = subprocess.run(cmd, capture_output=True, timeout=30)
    assert proc.returncode != 0


@requires_bwrap
def test_bwrap_cannot_connect_to_loopback(tmp_path):
    code = (
        "import socket\n"
        "socket.create_connection(('127.0.0.1', 9), timeout=2)\n"
    )
    cmd = build_bwrap_argv(
        argv=[sys.executable, "-c", code], writable_dirs=[], workdir=tmp_path
    )
    proc = subprocess.run(cmd, capture_output=True, timeout=30)
    assert proc.returncode != 0


@requires_bwrap
def test_bwrap_write_confined_to_writable_dirs(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    escape = tmp_path / "escape.txt"
    code = (
        f"open({str(out / 'ok.txt')!r}, 'w').write('ok')\n"
        f"open({str(escape)!r}, 'w').write('nope')\n"
    )
    cmd = build_bwrap_argv(
        argv=[sys.executable, "-c", code], writable_dirs=[out], workdir=out
    )
    proc = subprocess.run(cmd, capture_output=True, timeout=30)
    # Writing inside the bound dir worked; writing outside it failed.
    assert proc.returncode != 0
    assert (out / "ok.txt").read_text() == "ok"
    assert not escape.exists()


@requires_bwrap
@pytest.mark.asyncio
async def test_run_python_under_bwrap_end_to_end(monkeypatch):
    monkeypatch.setenv(isolated_runner.ISOLATION_ENV_VAR, "bwrap")
    result = await python_sandbox.run_python("print(2 + 2)")
    assert result.success
    assert "4" in result.output


@requires_bwrap
@pytest.mark.asyncio
async def test_run_python_under_bwrap_has_no_network(monkeypatch):
    monkeypatch.setenv(isolated_runner.ISOLATION_ENV_VAR, "bwrap")
    code = (
        "import urllib.request\n"
        "urllib.request.urlopen('http://example.com/', timeout=5)"
    )
    result = await python_sandbox.run_python(code)
    assert not result.success
