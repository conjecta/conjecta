from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
import signal
import tempfile
import uuid
import weakref
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from threading import RLock

from math_agent.config import LeanConfig
from math_agent.lean.project import PROOF_MODULE, config_fingerprint, render_lakefile
from math_agent.lean.result import LeanResult
from math_agent.lean.workspace import LeanWorkspace
from math_agent.lean.verifier import LeanVerifier, _strip_lean_comments_and_strings

log = logging.getLogger("math_agent.lean.runner")

__all__ = ["LeanRunner", "LeanResult"]


@dataclass
class _LoopRuntime:
    semaphore: asyncio.Semaphore
    deps_lock: asyncio.Lock
    deps_ready: bool = False


_RUNTIME_GUARD = RLock()
_LOOP_RUNTIMES: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()
_RESULT_CACHE: OrderedDict[str, LeanResult] = OrderedDict()
_LEAN_ENV_ALLOWLIST = frozenset(
    {
        "ELAN_HOME",
        "HOME",
        "LANG",
        "LC_ALL",
        "LAKE_HOME",
        "PATH",
        "TMPDIR",
        "USER",
        "XDG_CACHE_HOME",
    }
)


def _lean_subprocess_env() -> dict[str, str]:
    """Return a minimal environment that never exposes provider/app secrets."""
    env = {
        key: value
        for key, value in os.environ.items()
        if key in _LEAN_ENV_ALLOWLIST and value
    }
    env["CONJECTA_LEAN_RESTRICTED"] = "1"
    return env


async def _terminate_process(proc: asyncio.subprocess.Process) -> None:
    if proc.returncode is not None:
        return
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (AttributeError, ProcessLookupError, PermissionError):
        proc.kill()
    await proc.wait()


def _runtime_for(config: LeanConfig) -> _LoopRuntime:
    loop = asyncio.get_running_loop()
    key = f"{Path(config.workspace_dir).resolve()}|{config_fingerprint(config)}"
    with _RUNTIME_GUARD:
        by_key = _LOOP_RUNTIMES.setdefault(loop, {})
        runtime = by_key.get(key)
        if runtime is None:
            runtime = _LoopRuntime(
                semaphore=asyncio.Semaphore(
                    max(1, int(config.max_concurrent_checks))
                ),
                deps_lock=asyncio.Lock(),
            )
            by_key[key] = runtime
        return runtime


_AXIOMS_LIST_RE = re.compile(r"depends on axioms:\s*\[([^\]]*)\]")
_NO_AXIOMS_RE = re.compile(r"does not depend on any axioms")


def parse_axioms_output(output: str) -> list[str] | None:
    """Parse `#print axioms` output from a Lean run.

    Returns the axiom list ([] for "does not depend on any axioms"), or None
    when the output contains no recognizable axioms report.
    """
    match = _AXIOMS_LIST_RE.search(output or "")
    if match:
        return [item.strip() for item in match.group(1).split(",") if item.strip()]
    if _NO_AXIOMS_RE.search(output or ""):
        return []
    return None


class LeanRunner:
    def __init__(self, config: LeanConfig) -> None:
        self.config = config
        self._workspace = LeanWorkspace(config) if config.mathlib_dep else None

    async def ensure_dependencies(self, *, force: bool = False) -> LeanResult | None:
        """Download/update Lean workspace dependencies. None means ready."""
        if self._workspace is None:
            return None

        runtime = _runtime_for(self.config)
        async with runtime.deps_lock:
            if runtime.deps_ready and not force:
                # Guard against configuration/toolchain changes that happened after
                # the workspace was first marked ready.  A stale toolchain causes
                # elan to try downloading an unavailable toolchain and hang.
                toolchain_file = self._workspace.root / "lean-toolchain"
                expected = self.config.lean_toolchain
                if toolchain_file.exists() and toolchain_file.read_text(encoding="utf-8").strip() != expected:
                    log.warning(
                        "Workspace toolchain %s does not match config %s; re-initializing workspace",
                        toolchain_file.read_text(encoding="utf-8").strip(),
                        expected,
                    )
                    runtime.deps_ready = False
                else:
                    return None
            result = await self._workspace.ensure_ready(force=force)
            if result is None:
                runtime.deps_ready = True
            else:
                runtime.deps_ready = False
            return result

    async def check_proof(self, lean_code: str, *, draft: bool = False) -> LeanResult:
        """Type-check Lean 4 code.

        With ``draft=True`` the static gate tolerates ``sorry``/``admit``
        holes so an incomplete proof skeleton can be type-checked cheaply
        and often. Draft results are cached separately from strict results
        and are never complete proofs.
        """
        log.info("Lean check started code_chars=%d draft=%s", len(lean_code), draft)
        stripped = lean_code.strip()
        if not stripped:
            return LeanResult(
                success=False,
                errors=["Generated Lean code is empty"],
                failure_kind="empty_code",
                draft=draft,
            )
        if not re.search(r"\b(theorem|lemma|def|instance|example)\b", stripped):
            return LeanResult(
                success=False,
                errors=["Generated Lean code contains no theorem/lemma/definition"],
                failure_kind="no_declaration",
                draft=draft,
            )

        code_hash = hashlib.sha256(stripped.encode("utf-8")).hexdigest()
        cache_key = (
            f"{config_fingerprint(self.config)}|"
            f"safe={self.config.reject_unsafe_source}|draft={draft}|{code_hash}"
        )
        with _RUNTIME_GUARD:
            cached = _RESULT_CACHE.get(cache_key)
            if cached is not None:
                _RESULT_CACHE.move_to_end(cache_key)
        if cached is not None:
            log.info("Lean check cache hit code_hash=%s", code_hash)
            return cached.with_note("(cached result)")

        result: LeanResult
        if self._workspace is not None:
            dep_result = await self.ensure_dependencies()
            if dep_result is not None:
                return dep_result
            result = await self._check_in_workspace(lean_code, draft=draft)
        else:
            result = await self._check_in_temp(lean_code, draft=draft)

        with _RUNTIME_GUARD:
            # Do not cache transient failures; a retry may succeed once Lean or
            # the network is available again.
            if result.failure_kind not in {"timeout", "lean_unavailable"}:
                _RESULT_CACHE[cache_key] = result
                _RESULT_CACHE.move_to_end(cache_key)
                limit = max(0, int(self.config.result_cache_size))
                while len(_RESULT_CACHE) > limit:
                    _RESULT_CACHE.popitem(last=False)
        return result

    async def _check_in_workspace(self, lean_code: str, *, draft: bool = False) -> LeanResult:
        assert self._workspace is not None
        runtime = _runtime_for(self.config)
        async with runtime.semaphore:
            jobs_dir = self._workspace.root / ".conjecta_jobs"
            jobs_dir.mkdir(parents=True, exist_ok=True)
            proof_file = jobs_dir / f"ProofCheck_{uuid.uuid4().hex}.lean"
            proof_file.write_text(lean_code, encoding="utf-8")
            try:
                return await self._run_command(
                    project_dir=self._workspace.root,
                    proof_file=proof_file,
                    lean_code=lean_code,
                    draft=draft,
                    command=(
                        self.config.lake_path,
                        "env",
                        self.config.lean_path,
                        str(proof_file.resolve()),
                    ),
                )
            finally:
                proof_file.unlink(missing_ok=True)

    async def _check_in_temp(self, lean_code: str, *, draft: bool = False) -> LeanResult:
        with tempfile.TemporaryDirectory(prefix="math_agent_lean_") as tmpdir:
            project_dir = Path(tmpdir)
            self._write_temp_project(project_dir, lean_code)
            return await self._run_build(project_dir, lean_code, draft=draft)

    async def print_axioms(
        self, lean_code: str, declaration: str
    ) -> list[str] | None:
        """Best-effort `#print axioms` probe for a verified declaration.

        Returns the axiom list ([] when the declaration depends on no axioms),
        or None when the probe could not run or its output was not recognized.
        Never raises: axiom reporting must not fail an already-verified proof.
        """
        declaration = declaration.strip()
        if not declaration or not re.fullmatch(
            r"[A-Za-z_][A-Za-z0-9_'.]*", declaration
        ):
            return None
        probe = f"{lean_code.rstrip()}\n\n#print axioms {declaration}\n"
        try:
            if self._workspace is not None:
                dep_result = await self.ensure_dependencies()
                if dep_result is not None:
                    return None
                result = await self._check_in_workspace(probe)
            else:
                result = await self._check_in_temp(probe)
        except Exception:
            log.debug("print_axioms probe failed", exc_info=True)
            return None
        return parse_axioms_output(result.output)

    def _write_temp_project(self, project_dir: Path, lean_code: str) -> None:
        (project_dir / "lakefile.lean").write_text(
            render_lakefile(self.config),
            encoding="utf-8",
        )
        (project_dir / "lean-toolchain").write_text(
            f"{self.config.lean_toolchain}\n",
            encoding="utf-8",
        )
        (project_dir / f"{PROOF_MODULE}.lean").write_text(lean_code, encoding="utf-8")

    async def _run_build(self, project_dir: Path, lean_code: str, *, draft: bool = False) -> LeanResult:
        proof_file = project_dir / f"{PROOF_MODULE}.lean"
        return await self._run_command(
            project_dir=project_dir,
            proof_file=proof_file,
            lean_code=lean_code,
            draft=draft,
            command=(self.config.lake_path, "build"),
        )

    async def _run_command(
        self,
        *,
        project_dir: Path,
        proof_file: Path,
        lean_code: str,
        command: tuple[str, ...],
        draft: bool = False,
    ) -> LeanResult:
        verifier = LeanVerifier(
            lean_executable=self.config.lean_path,
            lake_executable=self.config.lake_path,
            cwd=project_dir,
            reject_unsafe_source=self.config.reject_unsafe_source,
            allow_sorry=draft,
        )
        preflight = verifier.check_static(proof_file)
        if not preflight.static_ok:
            return LeanResult(
                success=False,
                errors=[preflight.diagnostics[0].message],
                uses_sorry=any(
                    token in {"sorry", "admit", "axiom", "constant"}
                    for token in preflight.blocked_tokens
                ),
                lean_available=preflight.lean_available,
                static_ok=False,
                blocked_tokens=list(preflight.blocked_tokens),
                failure_kind=preflight.failure_kind,
                diagnostics=[item.to_dict() for item in preflight.diagnostics],
                scanned_files=list(preflight.scanned_files),
                draft=draft,
            )

        proc: asyncio.subprocess.Process | None = None
        try:
            log.debug("Running Lean verification cwd=%s", project_dir)
            proc = await asyncio.create_subprocess_exec(
                *command,
                cwd=str(project_dir),
                env=_lean_subprocess_env(),
                start_new_session=True,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(),
                timeout=self.config.build_timeout_seconds,
            )
        except asyncio.TimeoutError:
            if proc:
                await _terminate_process(proc)
            log.error("Lean verification timed out after %ss", self.config.build_timeout_seconds)
            return LeanResult(
                success=False,
                errors=[f"Lean verification timed out ({self.config.build_timeout_seconds}s)"],
                failure_kind="timeout",
                draft=draft,
            )
        except asyncio.CancelledError:
            if proc:
                await _terminate_process(proc)
            log.warning("lake build cancelled by caller")
            raise
        except FileNotFoundError:
            log.exception("Lean verification executable not found: %s", command[0])
            return LeanResult(
                success=False,
                errors=[
                    f"verification executable not found at '{command[0]}'. "
                    "Install Lean 4: https://leanprover.github.io/lean4/doc/setup.html"
                ],
                lean_available=False,
                failure_kind="lean_unavailable",
                draft=draft,
            )

        output = (stdout.decode(errors="replace") + "\n" + stderr.decode(errors="replace")).strip()
        errors = self._parse_errors(output)
        warnings = self._parse_warnings(output)
        stripped_code = _strip_lean_comments_and_strings(lean_code)
        uses_sorry = (
            re.search(
                r"(?<![A-Za-z0-9_])(?:sorry|admit)(?![A-Za-z0-9_])",
                stripped_code,
            )
            is not None
        )
        verification_ok = proc.returncode == 0 and not errors

        check = verifier.evaluate(
            lean_file=proof_file,
            verification_ok=verification_ok,
            returncode=proc.returncode,
            stdout=output,
            stderr="",
        )

        # Merge static-gate blockers into the human-readable error list.
        display_errors = list(errors)
        if not check.static_ok:
            display_errors = [check.diagnostics[0].message] + display_errors
        elif not verification_ok and check.diagnostics:
            display_errors = [check.diagnostics[0].message] + display_errors

        log.debug(
            "lake build finished rc=%s errors=%d warnings=%d static_ok=%s accepted=%s",
            proc.returncode,
            len(errors),
            len(warnings),
            check.static_ok,
            check.accepted,
        )

        return LeanResult(
            success=check.accepted,
            errors=display_errors,
            warnings=warnings,
            uses_sorry=uses_sorry or not check.static_ok,
            output=output,
            lean_available=check.lean_available,
            static_ok=check.static_ok,
            blocked_tokens=list(check.blocked_tokens),
            failure_kind=check.failure_kind,
            diagnostics=[d.to_dict() for d in check.diagnostics],
            scanned_files=list(check.scanned_files),
            draft=draft,
        )

    def _parse_errors(self, output: str) -> list[str]:
        errors = []
        for line in output.split("\n"):
            if ": error:" in line.lower() or "error:" in line.lower():
                errors.append(line.strip())
        return errors

    def _parse_warnings(self, output: str) -> list[str]:
        warnings = []
        for line in output.split("\n"):
            if ": warning:" in line.lower():
                warnings.append(line.strip())
        return warnings
