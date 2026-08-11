"""Build-time metadata helpers."""

from __future__ import annotations

import subprocess
from pathlib import Path


_PACKAGE_VERSION = "0.1.0"


def package_version() -> str:
    """Return the current package version string."""
    return _PACKAGE_VERSION


def source_commit(fallback: str = "unknown") -> str:
    """Return the current git commit SHA, or *fallback* if not available."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=Path(__file__).resolve().parent,
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
        return result.stdout.strip() or fallback
    except Exception:
        return fallback
