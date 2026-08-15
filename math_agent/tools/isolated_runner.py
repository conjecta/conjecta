"""bubblewrap (bwrap) isolation layer for the code-execution sandboxes.

The sandboxes (``python_sandbox``, ``plot_sandbox``) run model-generated code.
Their AST validation is only a first-layer UX / early-error check — it is not
a security boundary, because allowed native extensions (numpy, sympy,
matplotlib) make static checks bypassable in principle.  This module provides
the real boundary: wrapping the child command in ``bwrap`` so it runs with
all namespaces unshared, no network, a read-only view of the Python runtime,
and only the sandbox output directory writable.

Isolation is controlled by the ``CONJECTA_SANDBOX_ISOLATION`` environment
variable (``auto`` | ``bwrap`` | ``none``, default ``auto``).  In ``auto``
mode isolation is used when a bwrap binary is available; otherwise the
sandboxes fall back to rlimits + AST checks and log a warning, because that
fallback is not a container boundary.  See SECURITY.md.
"""
from __future__ import annotations

import logging
import os
import shutil
import sys
from pathlib import Path

_log = logging.getLogger(__name__)

ISOLATION_ENV_VAR = "CONJECTA_SANDBOX_ISOLATION"
_VALID_MODES = frozenset({"auto", "bwrap", "none"})

# Cached result of the bwrap binary lookup (see bwrap_available).
_bwrap_path: str | None = None
_bwrap_checked = False

# Module-level "warned once" flags: sandboxes that already logged the
# no-isolation warning, so the fallback does not spam the logs per call.
_warned_no_isolation: set[str] = set()


def bwrap_available() -> bool:
    """Return True when a ``bwrap`` binary is on PATH (result is cached)."""
    global _bwrap_path, _bwrap_checked
    if not _bwrap_checked:
        _bwrap_path = shutil.which("bwrap")
        _bwrap_checked = True
    return _bwrap_path is not None


def isolation_mode() -> str:
    """Return the configured isolation mode: ``auto``, ``bwrap``, or ``none``.

    Reads ``CONJECTA_SANDBOX_ISOLATION``; unknown values fall back to
    ``auto`` with a warning.
    """
    raw = os.environ.get(ISOLATION_ENV_VAR, "auto").strip().lower()
    if raw not in _VALID_MODES:
        _log.warning(
            "Unknown %s value %r (expected auto|bwrap|none); using 'auto'.",
            ISOLATION_ENV_VAR,
            raw,
        )
        return "auto"
    return raw


def isolation_active() -> bool:
    """Return True when sandboxed children should be wrapped in bwrap."""
    if isolation_mode() == "none":
        return False
    return bwrap_available()


def warn_no_isolation_once(sandbox: str) -> None:
    """Log (once per sandbox) that execution is not a container boundary."""
    if sandbox in _warned_no_isolation:
        return
    _warned_no_isolation.add(sandbox)
    _log.warning(
        "%s is running WITHOUT bwrap isolation (CONJECTA_SANDBOX_ISOLATION "
        "resolves to no isolation, or bwrap is not installed). AST checks and "
        "rlimits are still applied, but this is not a container boundary; "
        "install bubblewrap or set %s=bwrap for isolation. See SECURITY.md.",
        sandbox,
        ISOLATION_ENV_VAR,
    )


def _python_runtime_dirs() -> list[Path]:
    """Directories the child interpreter needs, bound read-only.

    Covers the base system prefixes plus the (possibly venv) prefix holding
    ``sys.executable`` and its site-packages, so the child can import the
    allowed third-party modules without seeing the project source tree.
    """
    candidates: list[Path] = []
    for raw in ("/usr", "/lib", "/lib64"):
        path = Path(raw)
        if path.is_dir():
            candidates.append(path)
    prefixes = {sys.prefix, sys.exec_prefix, sys.base_prefix, sys.base_exec_prefix}
    for prefix in prefixes:
        path = Path(prefix).resolve()
        if path.is_dir():
            candidates.append(path)
    # Drop duplicates and paths already covered by another candidate.
    kept: list[Path] = []
    for path in sorted(set(candidates)):
        if any(path.is_relative_to(parent) for parent in kept):
            continue
        kept.append(path)
    return kept


def build_bwrap_argv(
    *,
    argv: list[str],
    writable_dirs: list[Path],
    workdir: Path,
    network: bool = False,
    extra_env: dict[str, str] | None = None,
) -> list[str]:
    """Wrap ``argv`` in a bwrap command line.

    The child runs with all namespaces unshared, dies with the parent, gets a
    fresh session, an empty environment (only the variables set below), a
    read-only view of the Python runtime, a private ``/tmp``, and each of
    ``writable_dirs`` bound read-write.  The network namespace is unshared
    unless ``network`` is True.  All capabilities are dropped.
    """
    bwrap = _bwrap_path or shutil.which("bwrap") or "bwrap"
    cmd = [
        bwrap,
        "--unshare-all",
        "--die-with-parent",
        "--new-session",
        "--clearenv",
    ]
    if not network:
        cmd.append("--unshare-net")
    for ro_dir in _python_runtime_dirs():
        cmd += ["--ro-bind", str(ro_dir), str(ro_dir)]
    cmd += ["--proc", "/proc", "--tmpfs", "/tmp"]
    for writable in writable_dirs:
        path = str(Path(writable).resolve())
        cmd += ["--bind", path, path]
    cmd += ["--chdir", str(Path(workdir).resolve())]
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": "/tmp",
        "LANG": os.environ.get("LANG", "C.UTF-8"),
    }
    env.update(extra_env or {})
    for key, value in env.items():
        cmd += ["--setenv", key, value]
    cmd += ["--cap-drop", "ALL"]
    cmd += ["--", *argv]
    return cmd
