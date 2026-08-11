"""CLI to prefetch Lean workspace dependencies (Mathlib, etc.)."""
from __future__ import annotations

import asyncio
import sys

from math_agent.config import load_config
from math_agent.lean.runner import LeanRunner
from math_agent.log_config import setup_logging


async def run(*, force: bool = False) -> int:
    config = load_config()
    if config.logging.enabled:
        setup_logging(level=config.logging.level, log_dir=config.logging.dir)

    if not config.lean.enabled:
        print("Lean is disabled in config ([lean] enabled = false).")
        return 1
    if not config.lean.mathlib_dep:
        print("No external Lean dependencies configured (mathlib_dep = false).")
        return 0

    runner = LeanRunner(config.lean)
    print(f"Setting up Lean workspace at {config.lean.workspace_dir} ...")
    print("This may take several minutes on first run (lake update + cache get).")
    result = await runner.ensure_dependencies(force=force)
    if result is not None:
        print("Lean dependency setup failed:")
        for err in result.errors:
            print(f"  - {err}")
        return 1

    print(f"Lean workspace ready at {config.lean.workspace_dir}")
    return 0


def cli() -> None:
    force = "--force" in sys.argv[1:]
    raise SystemExit(asyncio.run(run(force=force)))


if __name__ == "__main__":
    cli()
