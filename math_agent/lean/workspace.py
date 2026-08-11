"""Persistent Lean workspace with dependency fetching and caching."""

from __future__ import annotations

import asyncio
import logging
import os
import signal
from pathlib import Path

from math_agent.config import LeanConfig
from math_agent.lean.project import (
    FINGERPRINT_FILE,
    READY_MARKER,
    config_fingerprint,
    render_lakefile,
)
from math_agent.lean.result import LeanResult

log = logging.getLogger("math_agent.lean.workspace")


class LeanWorkspace:
    """Reusable Lean project directory with Mathlib and other Lake deps."""

    def __init__(self, config: LeanConfig) -> None:
        self.config = config
        self.root = Path(config.workspace_dir)
        self._lock = asyncio.Lock()

    def write_scaffold(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        lakefile = render_lakefile(self.config)
        (self.root / "lakefile.lean").write_text(lakefile, encoding="utf-8")
        (self.root / "lean-toolchain").write_text(
            f"{self.config.lean_toolchain}\n",
            encoding="utf-8",
        )

    def fingerprint_matches(self) -> bool:
        expected = config_fingerprint(self.config)
        path = self.root / FINGERPRINT_FILE
        return path.exists() and path.read_text(encoding="utf-8").strip() == expected

    def is_ready(self) -> bool:
        if not (self.root / READY_MARKER).exists():
            return False
        if not (self.root / "lake-manifest.json").exists():
            return False
        if self.config.mathlib_dep and not self._mathlib_package_exists():
            return False
        if self.config.mathlib_dep and not self._dependency_oleans_healthy():
            return False
        return self.fingerprint_matches()

    def _mathlib_package_exists(self) -> bool:
        packages = self.root / ".lake" / "packages"
        return any((packages / name).exists() for name in ("mathlib", "mathlib4"))

    def _dependency_oleans_healthy(self) -> bool:
        """Every Lake dependency package must have compiled oleans.

        A package with zero oleans under ``.lake/build/lib/lean`` means the
        precompiled cache never materialized (e.g. ``lake exe cache get``
        failed); every ``import Mathlib`` check would then fail and surface
        as a bogus proof error instead of an infra problem.
        """
        packages = self.root / ".lake" / "packages"
        if not packages.is_dir():
            return False
        for package in sorted(packages.iterdir()):
            if not package.is_dir():
                continue
            lib_dir = package / ".lake" / "build" / "lib" / "lean"
            if not lib_dir.is_dir() or not any(lib_dir.rglob("*.olean")):
                return False
        return True

    def mark_ready(self) -> None:
        (self.root / FINGERPRINT_FILE).write_text(
            config_fingerprint(self.config),
            encoding="utf-8",
        )
        (self.root / READY_MARKER).write_text("ok\n", encoding="utf-8")

    def clear_ready(self) -> None:
        for name in (READY_MARKER, FINGERPRINT_FILE):
            path = self.root / name
            if path.exists():
                path.unlink()

    async def ensure_ready(self, *, force: bool = False) -> LeanResult | None:
        """Fetch/update Lake dependencies. Returns LeanResult on failure, else None."""
        async with self._lock:
            if not force and self.is_ready():
                log.debug("Lean workspace already ready at %s", self.root)
                return None

            self.write_scaffold()
            if force or not self.fingerprint_matches():
                self.clear_ready()

            update_result = await self._lake_update()
            if not update_result.success:
                return update_result

            if self.config.prefetch_cache and self.config.mathlib_dep:
                cache_result = await self._prefetch_cache()
                if not cache_result.success:
                    log.warning(
                        "Lean cache prefetch failed; continuing without precompiled oleans: %s",
                        cache_result.errors,
                    )

            if self.config.mathlib_dep and not self._dependency_oleans_healthy():
                log.error(
                    "Lean dependency oleans missing in %s; treating workspace as unavailable",
                    self.root.resolve(),
                )
                return LeanResult(
                    success=False,
                    errors=[
                        "Lean dependency packages have no compiled oleans; "
                        "`lake exe cache get` likely failed. Re-run the cache "
                        "prefetch or `lake build` in the workspace."
                    ],
                    lean_available=False,
                    failure_kind="lean_unavailable",
                )

            self.mark_ready()
            log.info("Lean workspace ready at %s", self.root.resolve())
            return None

    async def build(self) -> tuple[int, str]:
        return await self._run_lake(
            "build",
            timeout=self.config.build_timeout_seconds,
        )

    async def _lake_update(self) -> LeanResult:
        log.info("Fetching Lean dependencies in %s", self.root.resolve())
        code, output = await self._run_lake(
            "update",
            timeout=self.config.update_timeout_seconds,
        )
        if code == 0:
            return LeanResult(success=True, warnings=self._parse_warnings(output))
        return LeanResult(
            success=False,
            errors=self._parse_errors(output) or [f"lake update failed (exit {code})"],
            warnings=self._parse_warnings(output),
        )

    async def _prefetch_cache(self) -> LeanResult:
        log.info("Prefetching Lean cache (lake exe cache get)")
        code, output = await self._run_lake(
            "exe",
            "cache",
            "get",
            timeout=self.config.update_timeout_seconds,
        )
        if code == 0:
            return LeanResult(success=True, warnings=self._parse_warnings(output))
        return LeanResult(
            success=False,
            errors=self._parse_errors(output)
            or [f"lake exe cache get failed (exit {code})"],
            warnings=self._parse_warnings(output),
        )

    async def _run_lake(self, *args: str, timeout: int) -> tuple[int, str]:
        proc: asyncio.subprocess.Process | None = None
        cmd = (self.config.lake_path, *args)
        try:
            log.debug("Running %s cwd=%s", " ".join(cmd), self.root)
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=str(self.root),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=True,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            if proc and proc.returncode is None:
                await self._terminate_process_group(proc)
            return -1, f"{' '.join(cmd)} timed out ({timeout}s)"
        except FileNotFoundError:
            return -1, (
                f"lake not found at '{self.config.lake_path}'. "
                "Install Lean 4: https://leanprover.github.io/lean4/doc/setup.html"
            )
        except asyncio.CancelledError:
            if proc and proc.returncode is None:
                await self._terminate_process_group(proc)
            raise

        output = (
            stdout.decode(errors="replace") + "\n" + stderr.decode(errors="replace")
        ).strip()
        return proc.returncode or 0, output

    async def _terminate_process_group(self, proc: asyncio.subprocess.Process) -> None:
        """Kill the whole process group so child git processes are not orphaned."""
        if proc.returncode is not None:
            return
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            try:
                proc.kill()
            except (ProcessLookupError, PermissionError):
                pass
        try:
            await proc.wait()
        except (ProcessLookupError, PermissionError):
            pass

    def _parse_errors(self, output: str) -> list[str]:
        errors: list[str] = []
        for line in output.split("\n"):
            if ": error:" in line.lower() or line.lower().startswith("error:"):
                errors.append(line.strip())
        return errors

    def _parse_warnings(self, output: str) -> list[str]:
        warnings: list[str] = []
        for line in output.split("\n"):
            if ": warning:" in line.lower():
                warnings.append(line.strip())
        return warnings
