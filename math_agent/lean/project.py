"""Shared Lean 4 project scaffolding for proof checks and packages."""
from __future__ import annotations

from math_agent.config import LeanConfig

PROOF_MODULE = "ProofCheck"
READY_MARKER = ".math_agent_ready"
FINGERPRINT_FILE = ".math_agent_fingerprint"


def render_lakefile(config: LeanConfig) -> str:
    lakefile = 'import Lake\nopen Lake DSL\n\npackage «proof_check» where\n'
    lakefile += '  leanOptions := #[\n    ⟨`autoImplicit, false⟩\n  ]\n'
    if config.mathlib_dep:
        lakefile += '\nrequire "leanprover-community" / "mathlib4" from git\n'
        lakefile += f'  "{config.mathlib_repo}" @ "{config.mathlib_rev}"\n'
        if config.repl_enabled:
            lakefile += '\nrequire "leanprover-community" / "repl" from git\n'
            lakefile += f'  "{config.repl_repo}" @ "{config.repl_rev}"\n'
    lakefile += '\n@[default_target]\nlean_lib «ProofCheck» where\n  srcDir := "."\n'
    return lakefile


def render_package_lakefile(name: str, config: LeanConfig) -> str:
    safe_name = name.replace(" ", "").replace("-", "")
    content = f"""import Lake
open Lake DSL

package «{safe_name}» where
  leanOptions := #[
    ⟨`autoImplicit, false⟩
  ]
"""
    if config.mathlib_dep:
        content += f"""
require \"leanprover-community\" / \"mathlib4\" from git
  "{config.mathlib_repo}" @ "{config.mathlib_rev}"
"""
    content += f"""
@[default_target]
lean_lib «{safe_name}» where
  srcDir := "src"
"""
    return content


def config_fingerprint(config: LeanConfig) -> str:
    return "|".join(
        [
            config.lean_toolchain,
            str(config.mathlib_dep),
            config.mathlib_repo,
            config.mathlib_rev,
            PROOF_MODULE,
        ]
    )
