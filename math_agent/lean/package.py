from __future__ import annotations

from pathlib import Path

from math_agent.config import LeanConfig
from math_agent.lean.project import render_package_lakefile


class LeanPackage:
    """Packages verified Lean proofs as a standalone Lean 4 project."""

    def __init__(self, config: LeanConfig) -> None:
        self.config = config

    def create(self, output_dir: Path, name: str, proofs: list[str]) -> Path:
        """Create a standalone Lean 4 package with the given proofs."""
        output_dir.mkdir(parents=True, exist_ok=True)

        (output_dir / "lakefile.lean").write_text(
            render_package_lakefile(name, self.config),
            encoding="utf-8",
        )
        (output_dir / "lean-toolchain").write_text(
            f"{self.config.lean_toolchain}\n",
            encoding="utf-8",
        )
        self._write_proofs(output_dir, name, proofs)
        return output_dir

    def _write_proofs(self, output_dir: Path, name: str, proofs: list[str]) -> None:
        src_dir = output_dir / "src"
        src_dir.mkdir(exist_ok=True)

        safe_name = name.replace(" ", "").replace("-", "")
        imports = []
        for i, proof in enumerate(proofs):
            module_name = f"Proof{i + 1:03d}"
            (src_dir / f"{module_name}.lean").write_text(proof, encoding="utf-8")
            imports.append(f"import {safe_name}.{module_name}")

        root = "\n".join(imports) + "\n"
        (src_dir / f"{safe_name}.lean").write_text(root, encoding="utf-8")
