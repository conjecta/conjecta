#!/usr/bin/env python3
"""Reject release wheels with missing assets or local build directories."""

from __future__ import annotations

import sys
import zipfile
from pathlib import Path


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("usage: check_release_wheel.py PATH_TO_WHEEL")

    wheel = Path(sys.argv[1])
    if wheel.stat().st_size > 20 * 1024 * 1024:
        raise SystemExit(f"wheel is unexpectedly large: {wheel.stat().st_size} bytes")

    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()

    blocked = [
        name
        for name in names
        if "/node_modules/" in name or "/web/frontend/" in name
    ]
    if blocked:
        raise SystemExit(f"wheel contains local frontend sources: {blocked[0]}")

    required = (
        "math_agent/web/static/index.html",
        "math_agent/web/static/assets/",
    )
    for path in required:
        if not any(name == path or name.startswith(path) for name in names):
            raise SystemExit(f"wheel is missing required frontend content: {path}")

    if not any(name.endswith(".js") and "/static/assets/" in name for name in names):
        raise SystemExit("wheel has no compiled frontend JavaScript")
    if not any(name.endswith(".css") and "/static/assets/" in name for name in names):
        raise SystemExit("wheel has no compiled frontend CSS")

    print(f"release wheel OK: {wheel.name} ({wheel.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
