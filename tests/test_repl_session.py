"""Unit tests for the Lean REPL session layer (fake REPL over stdio)."""
from __future__ import annotations

import sys

import pytest

from math_agent.config import LeanConfig
from math_agent.lean.repl_session import (
    LeanReplPool,
    LeanReplSession,
    ReplProtocolError,
)

# Minimal REPL protocol stub: reads JSON requests terminated by a blank line,
# answers with one JSON line + blank line, mirroring repl v4.30.0 shapes.
FAKE_REPL = r"""
import json, sys

state = {"env": 0, "ps": 0}

def reply(payload):
    sys.stdout.write(json.dumps(payload) + "\n\n")
    sys.stdout.flush()

def handle(req):
    if "tactic" in req:
        tactic = req["tactic"]
        if req.get("proofState") != state["ps"]:
            return {"message": "Unknown proof state."}
        if tactic.startswith("bad"):
            return {"message": "Lean error:\ntactic failed"}
        if tactic == "exact 0":
            state["ps"] += 1
            return {
                "proofState": state["ps"],
                "goals": [],
                "messages": [],
                "traces": [],
                "proofStatus": "Completed",
            }
        state["ps"] += 1
        return {
            "proofState": state["ps"],
            "goals": ["n : Nat\n⊢ Nat"],
            "messages": [],
            "traces": [],
            "proofStatus": "Incomplete: 1 goal",
        }
    cmd = req.get("cmd", "")
    if "DIE" in cmd:
        sys.exit(1)
    if "SORRYLESS" in cmd:
        state["env"] += 1
        return {"env": state["env"], "messages": []}
    if "ERRCMD" in cmd:
        state["env"] += 1
        return {
            "env": state["env"],
            "messages": [
                {"severity": "error", "pos": {"line": 1, "column": 0}, "data": "boom"}
            ],
        }
    state["env"] += 1
    state["ps"] += 1
    return {
        "env": state["env"],
        "messages": [
            {
                "severity": "warning",
                "pos": {"line": 1, "column": 4},
                "data": "declaration uses 'sorry'",
            }
        ],
        "sorries": [
            {
                "goal": "⊢ Nat",
                "proofState": state["ps"],
                "pos": {"line": 1, "column": 29},
                "endPos": {"line": 1, "column": 34},
            }
        ],
    }

buf = []
for line in sys.stdin:
    if not line.strip():
        if buf:
            reply(handle(json.loads("".join(buf))))
            buf = []
    else:
        buf.append(line)
"""


@pytest.fixture
def repl_config(tmp_path):
    return LeanConfig(
        workspace_dir=str(tmp_path),
        repl_enabled=True,
        repl_step_timeout_seconds=10.0,
        repl_init_timeout_seconds=10.0,
    )


@pytest.fixture
def fake_repl_command(tmp_path):
    stub = tmp_path / "fake_repl.py"
    stub.write_text(FAKE_REPL, encoding="utf-8")
    return (sys.executable, str(stub))


@pytest.mark.asyncio
async def test_command_returns_structured_sorries(repl_config, fake_repl_command):
    session = LeanReplSession(repl_config, command=fake_repl_command)
    try:
        result = await session.run_command("theorem t : Nat := by sorry")
        assert result.env == 1
        assert not result.errors
        assert len(result.sorries) == 1
        assert result.sorries[0].goal == "⊢ Nat"
        assert result.sorries[0].proof_state == 1
    finally:
        await session.aclose()


@pytest.mark.asyncio
async def test_tactic_step_success_and_completion(repl_config, fake_repl_command):
    session = LeanReplSession(repl_config, command=fake_repl_command)
    try:
        opened = await session.run_command("theorem t : Nat := by sorry")
        ps = opened.sorries[0].proof_state

        step = await session.run_tactic("apply Nat.succ", ps)
        assert not step.failed
        assert not step.completed
        assert step.goals == ["n : Nat\n⊢ Nat"]
        assert step.status.startswith("Incomplete")

        done = await session.run_tactic("exact 0", step.proof_state)
        assert done.completed
        assert done.goals == []
    finally:
        await session.aclose()


@pytest.mark.asyncio
async def test_tactic_error_does_not_kill_session(repl_config, fake_repl_command):
    session = LeanReplSession(repl_config, command=fake_repl_command)
    try:
        opened = await session.run_command("theorem t : Nat := by sorry")
        ps = opened.sorries[0].proof_state
        failed = await session.run_tactic("bad_tactic", ps)
        assert failed.failed
        assert "Lean error" in failed.error
        # Session survives tactic-level errors.
        ok = await session.run_tactic("exact 0", ps)
        assert ok.completed
    finally:
        await session.aclose()


@pytest.mark.asyncio
async def test_command_error_messages_surface(repl_config, fake_repl_command):
    session = LeanReplSession(repl_config, command=fake_repl_command)
    try:
        result = await session.run_command("ERRCMD")
        assert result.errors == ["boom"]
    finally:
        await session.aclose()


@pytest.mark.asyncio
async def test_dead_process_restarts_once(repl_config, fake_repl_command):
    session = LeanReplSession(repl_config, command=fake_repl_command)
    try:
        await session.run_command("theorem t : Nat := by sorry")
        # Kill the process; the next request must transparently restart.
        await session.run_command("DIE")
    except ReplProtocolError:
        pass
    # After the DIE the stub exits; _request should have restarted and then
    # either succeeded (fresh stub answers DIE again -> exits -> raise) —
    # either way a subsequent plain command on a fresh process works.
    result = await session.run_command("theorem t : Nat := by sorry")
    assert result.sorries
    await session.aclose()


@pytest.mark.asyncio
async def test_static_gate_blocks_unsafe_source(repl_config, fake_repl_command):
    session = LeanReplSession(repl_config, command=fake_repl_command)
    blocked = session.static_gate("theorem t : Nat := by exact IO.println 1", label="t")
    assert any(token.startswith("unsafe:IO") for token in blocked)
    assert session.static_gate("theorem t : Nat := by sorry", label="t") == [
        "sorry"
    ]
    assert session.static_gate("theorem t : Nat := by rfl", label="t") == []


@pytest.mark.asyncio
async def test_pool_availability_and_reuse(repl_config, fake_repl_command, tmp_path):
    # available() requires the real binary path inside the workspace.
    assert not LeanReplPool.available(repl_config)
    binary = LeanReplSession.binary_path(repl_config)
    binary.parent.mkdir(parents=True)
    binary.write_text("#!/bin/sh\n", encoding="utf-8")
    assert LeanReplPool.available(repl_config)

    repl_config.repl_max_sessions = 1
    LeanReplPool(repl_config)
    session = LeanReplSession(repl_config, command=fake_repl_command)
    await session.start()
    assert session.alive
    await session.aclose()
    assert not session.alive


@pytest.mark.integration
@pytest.mark.asyncio
async def test_real_repl_end_to_end():
    """Smoke test against the built REPL binary (skipped when absent).

    Uses precise imports: the umbrella `import Mathlib` does not fit in this
    host's RAM budget (that holds for the batch checker too).
    """
    from math_agent.config import load_config

    config = load_config().lean
    if not LeanReplPool.available(config):
        pytest.skip("REPL binary not built")
    session = LeanReplSession(config)
    try:
        opened = await session.run_command(
            "import Mathlib.Tactic.NormNum\n"
            "import Mathlib.Algebra.Order.Ring.Nat\n\n"
            "theorem repl_smoke (a b : Nat) : a + b = b + a := by sorry"
        )
        assert opened.sorries, f"no sorry reported: {opened.errors}"
        ps = opened.sorries[0].proof_state
        assert ps is not None
        assert "a + b = b + a" in opened.sorries[0].goal
        step = await session.run_tactic("omega", ps)
        assert step.completed, f"omega did not close goal: {step.error or step.goals}"
    finally:
        await session.aclose()


@pytest.mark.asyncio
async def test_pool_recycles_session_over_command_budget(
    repl_config, fake_repl_command
):
    repl_config.repl_recycle_after_commands = 2
    pool = LeanReplPool(repl_config)
    session = LeanReplSession(repl_config, command=fake_repl_command)
    await session.start()
    await session.run_command("theorem t : Nat := by sorry")
    await session.run_command("theorem u : Nat := by sorry")
    assert session.commands_executed == 2
    assert session.should_recycle

    await pool._checkin(session)

    # Over-budget sessions are closed between searches, not returned idle.
    assert not session.alive
    assert pool._idle == []


@pytest.mark.asyncio
async def test_pool_keeps_session_under_command_budget(
    repl_config, fake_repl_command
):
    repl_config.repl_recycle_after_commands = 10
    pool = LeanReplPool(repl_config)
    session = LeanReplSession(repl_config, command=fake_repl_command)
    await session.start()
    await session.run_command("theorem t : Nat := by sorry")
    assert not session.should_recycle

    await pool._checkin(session)

    assert session.alive
    assert pool._idle == [session]
    await session.aclose()


@pytest.mark.asyncio
async def test_pool_breaker_opens_after_repeated_deaths(repl_config):
    pool = LeanReplPool(repl_config)
    assert pool._BREAKER_CONSECUTIVE_DEATHS >= 2

    for _ in range(pool._BREAKER_CONSECUTIVE_DEATHS):
        dead = LeanReplSession(repl_config)  # never started: alive is False
        await pool._checkin(dead)

    with pytest.raises(ReplProtocolError, match="circuit breaker"):
        async with pool.session():
            pass

    # After the cooldown the pool half-opens and tries a real checkout again
    # (which fails here only because no REPL binary exists in tmp_path).
    pool._breaker_open_until = 0.0
    with pytest.raises(ReplProtocolError) as excinfo:
        async with pool.session():
            pass
    assert "circuit breaker" not in str(excinfo.value)


@pytest.mark.asyncio
async def test_pool_healthy_checkin_resets_death_count(repl_config):
    pool = LeanReplPool(repl_config)
    dead = LeanReplSession(repl_config)
    await pool._checkin(dead)
    assert pool._consecutive_deaths == 1

    alive = LeanReplSession(repl_config)
    alive._proc = type("P", (), {"returncode": None})()  # minimal live stub
    await pool._checkin(alive)

    assert pool._consecutive_deaths == 0
    assert pool._breaker_open_until == 0.0
