"""Long-running Lean REPL sessions for structured tactic proof search.

Wraps the leanprover-community ``repl`` binary (JSON-over-stdio) so tactic
search can step through proof states without paying a full ``lake env lean``
batch compile per candidate, and so goal states arrive as structured strings
instead of being regex-scraped from compiler errors.

Protocol (repl v4.30.0, see REPL/JSON.lean upstream):

- input: one JSON object per request, terminated by a blank line;
- command: ``{"cmd": "...", "env": n?}`` ->
  ``{"env": n, "messages": [...], "sorries": [{"goal", "proofState", ...}]}``;
- tactic: ``{"tactic": "...", "proofState": n}`` ->
  ``{"proofState": m, "goals": [...], "proofStatus": "Completed"|...}`` or
  ``{"message": "Lean error:\\n..."}`` on tactic failure (session survives);
- output: one JSON line followed by a blank line.

The batch-compile path in ``runner.py`` remains the source of truth for final
verification; the REPL only accelerates search.
"""
from __future__ import annotations

import asyncio
import json
import logging
import shutil
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator

from math_agent.config import LeanConfig
from math_agent.lean.runner import _lean_subprocess_env, _terminate_process
from math_agent.lean.verifier import LeanVerifier

log = logging.getLogger("math_agent.lean.repl_session")

__all__ = [
    "LeanReplPool",
    "LeanReplSession",
    "ReplCommandResult",
    "ReplMessage",
    "ReplProtocolError",
    "ReplSorry",
    "ReplTacticResult",
]

_REPL_BINARY_RELPATH = Path(".lake/packages/repl/.lake/build/bin/repl")


def _memory_limited_argv(argv: tuple[str, ...], limit_mb: int) -> tuple[str, ...]:
    """Wrap ``argv`` in a cgroup scope that caps resident memory.

    A runaway elaboration can otherwise grow one REPL to tens of GB and take
    the whole host down. RLIMIT_AS is the wrong tool here: Lean reserves a huge
    *virtual* address space (24GB virt against 9GB resident was observed), so
    an address-space cap kills healthy sessions during ``import Mathlib``.
    cgroup v2 ``MemoryMax`` bounds resident memory instead, so the kernel only
    kills the session once it genuinely consumes that much. ``MemorySwapMax=0``
    is required alongside it: without it the cgroup spills over its limit into
    swap and never triggers the kill, which is the same slow host-wide crawl
    the cap exists to prevent.

    Returns ``argv`` unchanged when no limit is set or systemd-run is missing.
    """
    if limit_mb <= 0:
        return argv
    systemd_run = shutil.which("systemd-run")
    if systemd_run is None:
        log.warning(
            "systemd-run not found; starting Lean REPL without a memory cap."
        )
        return argv
    return (
        systemd_run,
        "--scope",
        "--quiet",
        "--collect",
        f"--property=MemoryMax={limit_mb}M",
        "--property=MemorySwapMax=0",
        *argv,
    )


class ReplProtocolError(RuntimeError):
    """The REPL process died, timed out, or spoke unintelligible JSON."""


@dataclass
class ReplMessage:
    severity: str
    data: str
    line: int = 0
    column: int = 0


@dataclass
class ReplSorry:
    goal: str
    proof_state: int | None


@dataclass
class ReplCommandResult:
    env: int | None = None
    messages: list[ReplMessage] = field(default_factory=list)
    sorries: list[ReplSorry] = field(default_factory=list)

    @property
    def errors(self) -> list[str]:
        return [m.data for m in self.messages if m.severity == "error"]


@dataclass
class ReplTacticResult:
    proof_state: int | None = None
    goals: list[str] = field(default_factory=list)
    messages: list[ReplMessage] = field(default_factory=list)
    status: str = ""
    error: str = ""

    @property
    def failed(self) -> bool:
        return bool(self.error) or any(m.severity == "error" for m in self.messages)

    @property
    def completed(self) -> bool:
        """True when the proof closed (no goals, no sorry/mvars, no errors)."""
        return (
            not self.failed
            and not self.goals
            and self.status.startswith("Completed")
        )


def _parse_messages(raw: Any) -> list[ReplMessage]:
    messages: list[ReplMessage] = []
    for item in raw or []:
        pos = item.get("pos") or {}
        messages.append(
            ReplMessage(
                severity=str(item.get("severity", "")),
                data=str(item.get("data", "")),
                line=int(pos.get("line", 0) or 0),
                column=int(pos.get("column", 0) or 0),
            )
        )
    return messages


class LeanReplSession:
    """One ``repl`` subprocess; requests are serialized through a lock."""

    def __init__(
        self, config: LeanConfig, *, command: tuple[str, ...] | None = None
    ) -> None:
        self.config = config
        # Test hook: replace the default `lake env <repl-binary>` invocation.
        self._command_override = command
        self._proc: asyncio.subprocess.Process | None = None
        self._lock = asyncio.Lock()
        # Commands served by this process; retained proof states make RSS grow
        # roughly with this count, so the pool recycles past a budget.
        self.commands_executed = 0
        self._verifier = LeanVerifier(
            lean_executable=config.lean_path,
            lake_executable=config.lake_path,
            reject_unsafe_source=config.reject_unsafe_source,
        )

    @classmethod
    def binary_path(cls, config: LeanConfig) -> Path:
        return Path(config.workspace_dir) / _REPL_BINARY_RELPATH

    @property
    def alive(self) -> bool:
        return self._proc is not None and self._proc.returncode is None

    @property
    def should_recycle(self) -> bool:
        """True once this session has served its command budget."""
        limit = int(getattr(self.config, "repl_recycle_after_commands", 0) or 0)
        return limit > 0 and self.commands_executed >= limit

    async def start(self) -> None:
        if self.alive:
            return
        await self.aclose()
        binary = self.binary_path(self.config)
        if self._command_override is None and not binary.exists():
            raise ReplProtocolError(
                f"Lean REPL binary not found at {binary}; "
                "build it with `lake build repl` in the workspace."
            )
        log.info(
            "starting Lean REPL: %s (memory limit %s MB)",
            binary,
            self.config.repl_memory_limit_mb or "none",
        )
        argv = self._command_override or _memory_limited_argv(
            (
                self.config.lake_path,
                "env",
                str(binary.resolve()),
            ),
            int(self.config.repl_memory_limit_mb),
        )
        self._proc = await asyncio.create_subprocess_exec(
            *argv,
            cwd=str(Path(self.config.workspace_dir).resolve()),
            env=_lean_subprocess_env(),
            start_new_session=True,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )

    async def aclose(self) -> None:
        proc, self._proc = self._proc, None
        if proc is not None:
            await _terminate_process(proc)

    async def run_command(self, cmd: str, env: int | None = None) -> ReplCommandResult:
        """Run a Lean command; sorries carry proof-state ids for tactic mode."""
        payload: dict[str, Any] = {"cmd": cmd}
        if env is not None:
            payload["env"] = env
        response = await self._request(payload, first=env is None)
        if "message" in response and "env" not in response:
            raise ReplProtocolError(str(response["message"]))
        return ReplCommandResult(
            env=response.get("env"),
            messages=_parse_messages(response.get("messages")),
            sorries=[
                ReplSorry(goal=str(s.get("goal", "")), proof_state=s.get("proofState"))
                for s in response.get("sorries") or []
            ],
        )

    async def run_tactic(self, tactic: str, proof_state: int) -> ReplTacticResult:
        """Run one tactic at a proof state; tactic errors do not kill the REPL."""
        payload = {"tactic": tactic, "proofState": proof_state}
        response = await self._request(payload)
        if "message" in response:
            # Tactic-level failure (unknown state or Lean error): the session
            # itself is still usable.
            return ReplTacticResult(error=str(response["message"]))
        return ReplTacticResult(
            proof_state=response.get("proofState"),
            goals=[str(g) for g in response.get("goals") or []],
            messages=_parse_messages(response.get("messages")),
            status=str(response.get("proofStatus", "")),
        )

    def static_gate(self, code: str, *, label: str) -> list[str]:
        """Blocked tokens for REPL-bound source; empty list means allowed."""
        return list(self._verifier.scan_source(code, label=label).blocked_tokens)

    async def _request(self, payload: dict[str, Any], *, first: bool = False) -> dict:
        timeout = (
            self.config.repl_init_timeout_seconds
            if first
            else self.config.repl_step_timeout_seconds
        )
        async with self._lock:
            for attempt in range(2):
                try:
                    if not self.alive:
                        await self.start()
                    response = await asyncio.wait_for(
                        self._round_trip(payload), timeout=timeout
                    )
                    self.commands_executed += 1
                    return response
                except (ReplProtocolError, asyncio.TimeoutError):
                    log.warning(
                        "REPL request failed (attempt %d); restarting session",
                        attempt + 1,
                        exc_info=True,
                    )
                    await self.aclose()
                    if attempt == 1:
                        raise ReplProtocolError(
                            "Lean REPL session failed twice; giving up"
                        ) from None
        raise ReplProtocolError("unreachable")

    async def _round_trip(self, payload: dict[str, Any]) -> dict:
        assert self._proc is not None
        assert self._proc.stdin is not None and self._proc.stdout is not None
        line = json.dumps(payload, ensure_ascii=False)
        try:
            self._proc.stdin.write(line.encode("utf-8") + b"\n\n")
            await self._proc.stdin.drain()
        except (BrokenPipeError, ConnectionResetError) as exc:
            raise ReplProtocolError(f"REPL stdin broken: {exc}") from exc
        chunks: list[bytes] = []
        while True:
            raw = await self._proc.stdout.readline()
            if not raw:
                raise ReplProtocolError("REPL closed stdout unexpectedly")
            stripped = raw.strip()
            if not stripped:
                break
            chunks.append(raw)
        try:
            return json.loads(b"".join(chunks).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ReplProtocolError(f"unparseable REPL response: {exc}") from exc


class LeanReplPool:
    """Bounded pool of REPL sessions, one workspace per pool."""

    # Circuit breaker: this many consecutive request-time session deaths open
    # the breaker, so callers fall back to batch compilation instead of paying
    # a full mathlib import per doomed restart.
    _BREAKER_CONSECUTIVE_DEATHS = 3
    _BREAKER_COOLDOWN_SECONDS = 300.0

    def __init__(self, config: LeanConfig) -> None:
        self.config = config
        self._semaphore = asyncio.Semaphore(max(1, int(config.repl_max_sessions)))
        self._idle: list[LeanReplSession] = []
        self._spawned = 0
        self._lock = asyncio.Lock()
        self._consecutive_deaths = 0
        self._breaker_open_until = 0.0

    @classmethod
    def available(cls, config: LeanConfig) -> bool:
        return bool(
            config.repl_enabled
            and config.mathlib_dep
            and LeanReplSession.binary_path(config).exists()
        )

    @asynccontextmanager
    async def session(self) -> AsyncIterator[LeanReplSession]:
        if time.monotonic() < self._breaker_open_until:
            raise ReplProtocolError(
                "Lean REPL circuit breaker open after repeated session deaths; "
                "use batch compilation instead"
            )
        async with self._semaphore:
            session = await self._checkout()
            try:
                yield session
            finally:
                await self._checkin(session)

    async def _checkout(self) -> LeanReplSession:
        async with self._lock:
            while self._idle:
                session = self._idle.pop()
                if session.alive:
                    return session
            if self._spawned < max(1, int(self.config.repl_max_sessions)):
                self._spawned += 1
                session = LeanReplSession(self.config)
                try:
                    await session.start()
                except Exception:
                    self._spawned -= 1
                    raise
                return session
        # All spawned sessions are checked out; wait for one to return.
        # (Semaphore sizing above normally prevents reaching this.)
        async with self._lock:
            if self._idle:
                return self._idle.pop()
        raise ReplProtocolError("no REPL session available")

    async def _checkin(self, session: LeanReplSession) -> None:
        if session.alive and not session.should_recycle:
            async with self._lock:
                self._consecutive_deaths = 0
                self._idle.append(session)
            return
        async with self._lock:
            self._spawned = max(0, self._spawned - 1)
            if session.alive:
                # Planned recycle: healthy but over its command budget, so
                # close it between searches instead of letting retained proof
                # states grow into a mid-search OOM kill.
                self._consecutive_deaths = 0
                log.info(
                    "recycling Lean REPL session after %d commands",
                    session.commands_executed,
                )
            else:
                self._consecutive_deaths += 1
                if self._consecutive_deaths >= self._BREAKER_CONSECUTIVE_DEATHS:
                    self._consecutive_deaths = 0
                    self._breaker_open_until = (
                        time.monotonic() + self._BREAKER_COOLDOWN_SECONDS
                    )
                    log.warning(
                        "Lean REPL died %d times in a row; circuit breaker "
                        "open for %.0fs, searches fall back to batch mode",
                        self._BREAKER_CONSECUTIVE_DEATHS,
                        self._BREAKER_COOLDOWN_SECONDS,
                    )
        await session.aclose()

    async def aclose(self) -> None:
        async with self._lock:
            idle, self._idle = self._idle, []
            self._spawned = 0
        for session in idle:
            await session.aclose()
