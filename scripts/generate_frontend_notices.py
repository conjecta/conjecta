#!/usr/bin/env python3
"""Generate notices for production packages bundled into the frontend."""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
FRONTEND = ROOT / "math_agent" / "web" / "frontend"
OUTPUT = ROOT / "THIRD_PARTY_FRONTEND_LICENSES.txt"
LICENSE_NAMES = ("LICENSE", "LICENSE.md", "LICENSE.txt", "license", "license.md")


def _package_name(package_path: str) -> str:
    return package_path.rsplit("node_modules/", 1)[-1]


def _license_text(package_path: str) -> str:
    package_dir = FRONTEND / package_path
    for name in LICENSE_NAMES:
        candidate = package_dir / name
        if candidate.is_file():
            return candidate.read_text(encoding="utf-8", errors="replace").strip()
    return "License text not included by the package; see its package metadata."


def main() -> int:
    lock = json.loads((FRONTEND / "package-lock.json").read_text(encoding="utf-8"))
    records: list[tuple[str, str, str, str]] = []
    for package_path, metadata in lock["packages"].items():
        if not package_path.startswith("node_modules/") or metadata.get("dev") is True:
            continue
        records.append(
            (
                _package_name(package_path),
                metadata.get("version", "unknown"),
                metadata.get("license", "unknown"),
                _license_text(package_path),
            )
        )

    lines = [
        "Conjecta frontend third-party licenses",
        "======================================",
        "",
        "Generated from math_agent/web/frontend/package-lock.json.",
        "Only production dependency entries are included.",
        "",
    ]
    for name, version, license_id, license_text in sorted(set(records)):
        lines.extend(
            [
                f"--- {name} {version} ({license_id}) ---",
                license_text,
                "",
            ]
        )
    OUTPUT.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    print(f"wrote {OUTPUT} with {len(records)} package entries")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
