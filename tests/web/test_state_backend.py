"""State-backend tests: in-memory semantics, quota race/idempotency, Redis stubs."""
from __future__ import annotations

import asyncio
import importlib.util
from typing import Any

import pytest
from fastapi.testclient import TestClient

from math_agent.billing.models import LLMResponse
from math_agent.config import default_config, load_config
from math_agent.web import state_backend
from math_agent.web.app import app
from math_agent.web.security import LOCAL_DEV_USER_ID
from math_agent.web.state_backend import (
    InMemoryQuotaBackend,
    InMemoryRateLimitBackend,
    InMemorySolveCapacityBackend,
    InMemoryThrottleBackend,
    RedisQuotaBackend,
    RedisRateLimitBackend,
    RedisSolveCapacityBackend,
    RedisThrottleBackend,
    StateBackend,
    build_state_backend,
    set_state_backend,
)


def _memory_backend() -> StateBackend:
    return StateBackend(
        rate_limit=InMemoryRateLimitBackend(),
        throttle=InMemoryThrottleBackend(),
        capacity=InMemorySolveCapacityBackend(),
        quota=InMemoryQuotaBackend(),
    )


@pytest.fixture
def memory_backend():
    backend = _memory_backend()
    set_state_backend(backend)
    yield backend
    set_state_backend(None)


# ---------------------------------------------------------------------------
# In-memory backends
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_memory_rate_limit_allows_then_blocks():
    backend = InMemoryRateLimitBackend()
    assert await backend.check_and_record("k", limit=2, window_seconds=60.0, now=100.0)
    assert await backend.check_and_record("k", limit=2, window_seconds=60.0, now=101.0)
    assert not await backend.check_and_record("k", limit=2, window_seconds=60.0, now=102.0)
    # Window slides: the first hit expires 60s later.
    assert await backend.check_and_record("k", limit=2, window_seconds=60.0, now=161.0)


@pytest.mark.asyncio
async def test_memory_rate_limit_disabled_with_nonpositive_limit():
    backend = InMemoryRateLimitBackend()
    for _ in range(5):
        assert await backend.check_and_record("k", limit=0, window_seconds=60.0)


@pytest.mark.asyncio
async def test_memory_throttle_cooldown_and_window():
    backend = InMemoryThrottleBackend()
    assert await backend.check_and_record_cooldown("phone", cooldown_seconds=60, now=100.0)
    assert not await backend.check_and_record_cooldown("phone", cooldown_seconds=60, now=130.0)
    assert await backend.check_and_record_cooldown("phone", cooldown_seconds=60, now=161.0)

    for i in range(3):
        assert await backend.check_and_record_window(
            "ip", limit=3, window_seconds=60.0, now=200.0 + i
        )
    assert not await backend.check_and_record_window("ip", limit=3, window_seconds=60.0, now=203.0)


@pytest.mark.asyncio
async def test_memory_throttle_lockout_and_recovery():
    backend = InMemoryThrottleBackend()
    for _ in range(4):
        await backend.record_failure("p", max_attempts=5, lockout_seconds=900, now=100.0)
    assert not await backend.is_locked("p", max_attempts=5, lockout_seconds=900, now=100.0)
    await backend.record_failure("p", max_attempts=5, lockout_seconds=900, now=100.0)
    assert await backend.is_locked("p", max_attempts=5, lockout_seconds=900, now=100.0)
    assert await backend.is_locked("p", max_attempts=5, lockout_seconds=900, now=999.0)
    # Expired lock re-arms while failures are still on record.
    assert await backend.is_locked("p", max_attempts=5, lockout_seconds=900, now=1001.0)
    await backend.record_success("p")
    assert not await backend.is_locked("p", max_attempts=5, lockout_seconds=900, now=1002.0)


@pytest.mark.asyncio
async def test_memory_capacity_acquire_release(monkeypatch):
    monkeypatch.setenv("CONJECTA_MAX_CONCURRENT_SOLVES", "2")
    backend = InMemorySolveCapacityBackend()
    assert await backend.try_acquire()
    assert await backend.try_acquire()
    assert not await backend.try_acquire()
    assert await backend.in_flight() == 2
    await backend.release()
    assert await backend.in_flight() == 1
    assert await backend.try_acquire()


@pytest.mark.asyncio
async def test_memory_quota_reserve_settle_release():
    backend = InMemoryQuotaBackend()
    assert await backend.reserve("s1", "u1", 300, consumed=0, limit=500)
    assert backend.outstanding("u1") == 300
    await backend.settle("s1", 120)
    assert backend.outstanding("u1") == 0
    assert backend.settled_consumed("u1") == 120

    assert await backend.reserve("s2", "u1", 100, consumed=120, limit=500)
    await backend.release("s2")
    assert backend.outstanding("u1") == 0
    assert backend.settled_consumed("u1") == 120


@pytest.mark.asyncio
async def test_memory_quota_reserve_is_idempotent_per_session():
    backend = InMemoryQuotaBackend()
    assert await backend.reserve("s1", "u1", 300, consumed=0, limit=500)
    assert await backend.reserve("s1", "u1", 300, consumed=0, limit=500)
    assert backend.outstanding("u1") == 300


@pytest.mark.asyncio
async def test_memory_quota_double_settle_counts_once():
    backend = InMemoryQuotaBackend()
    assert await backend.reserve("s1", "u1", 300, consumed=0, limit=500)
    await backend.settle("s1", 120)
    await backend.settle("s1", 120)
    assert backend.settled_consumed("u1") == 120


@pytest.mark.asyncio
async def test_memory_quota_concurrent_reserve_only_one_wins():
    backend = InMemoryQuotaBackend()
    results = await asyncio.gather(
        backend.reserve("s1", "u1", 300, consumed=0, limit=500),
        backend.reserve("s2", "u1", 300, consumed=0, limit=500),
    )
    assert sorted(results) == [False, True]
    assert backend.outstanding("u1") == 300


# ---------------------------------------------------------------------------
# HTTP flow: reserve at start, settle at end, release when nothing was used
# ---------------------------------------------------------------------------


class _FakeUsageStore:
    def __init__(self, daily_total=0):
        self.daily_total = daily_total
        self.recorded = []

    def daily_usage(self, _user_id, _target_date=None):
        return {
            "prompt_tokens": self.daily_total,
            "completion_tokens": 0,
            "total_tokens": self.daily_total,
            "cost_usd": 0.0,
        }

    def record(self, user_id, usage, source="platform"):
        self.recorded.append({"user_id": user_id, "usage": usage, "source": source})


async def _no_user_api_key(_uid):
    return None


def _fake_stream_with_usage(tokens: int):
    from math_agent.web.agent_factory import _solve_usage

    async def stream(_msg, *, user_id=None):
        usage = _solve_usage.get()
        if usage is not None and tokens > 0:
            usage.add(
                LLMResponse(
                    text="answer",
                    prompt_tokens=tokens,
                    completion_tokens=0,
                    total_tokens=tokens,
                )
            )
        yield {"type": "done", "final_answer": "2"}

    return stream


def _enable_quota_flow(monkeypatch, memory_backend, daily_total=0, stream_tokens=15):
    monkeypatch.delenv("CONJECTA_JWT_SECRET", raising=False)
    monkeypatch.setenv("CONJECTA_ALLOW_UNAUTHENTICATED", "1")
    monkeypatch.delenv("CONJECTA_DISABLE_QUOTA", raising=False)
    fake_store = _FakeUsageStore(daily_total=daily_total)
    monkeypatch.setattr("math_agent.web.agent_factory.UsageStore", lambda: fake_store)
    monkeypatch.setattr("math_agent.web.agent_factory._load_user_api_key", _no_user_api_key)
    monkeypatch.setattr(
        "math_agent.web.solve_routes.stream_solve_events",
        _fake_stream_with_usage(stream_tokens),
    )
    return fake_store


def test_solve_settles_quota_reservation(monkeypatch, memory_backend):
    fake_store = _enable_quota_flow(monkeypatch, memory_backend, stream_tokens=15)
    client = TestClient(app)

    resp = client.post("/api/solve/stream", json={"problem": "1+1"})

    assert resp.status_code == 200
    assert len(fake_store.recorded) == 1
    quota = memory_backend.quota
    assert quota.outstanding(LOCAL_DEV_USER_ID) == 0
    assert quota.settled_consumed(LOCAL_DEV_USER_ID) == 15


def test_solve_releases_reservation_when_no_usage(monkeypatch, memory_backend):
    fake_store = _enable_quota_flow(monkeypatch, memory_backend, stream_tokens=0)
    client = TestClient(app)

    resp = client.post("/api/solve/stream", json={"problem": "1+1"})

    assert resp.status_code == 200
    assert fake_store.recorded == []
    quota = memory_backend.quota
    assert quota.outstanding(LOCAL_DEV_USER_ID) == 0
    assert quota.settled_consumed(LOCAL_DEV_USER_ID) == 0


def test_second_solve_rejected_while_reservation_held(monkeypatch, memory_backend):
    """A held reservation closes the check-then-record race at the HTTP layer."""
    _enable_quota_flow(monkeypatch, memory_backend, stream_tokens=15)
    # The in-memory backend uses threading.Lock only, so this is loop-agnostic.
    held = asyncio.run(
        memory_backend.quota.reserve("held", LOCAL_DEV_USER_ID, 500_000, consumed=0, limit=500_000)
    )
    assert held
    client = TestClient(app)

    resp = client.post("/api/solve/stream", json={"problem": "1+1"})

    assert resp.status_code == 429
    assert "DAILY_QUOTA_EXCEEDED" in resp.text


# ---------------------------------------------------------------------------
# Redis backends against a stub client (no server, no fakeredis)
# ---------------------------------------------------------------------------


class StubRedis:
    """Async Redis stand-in that emulates the state_backend Lua scripts."""

    def __init__(self) -> None:
        self.store: dict[str, Any] = {}
        self.eval_calls: list[tuple[Any, int, tuple]] = []

    async def eval(self, script, numkeys, *args):
        self.eval_calls.append((script, numkeys, args))
        keys = list(args[:numkeys])
        rest = list(args[numkeys:])
        if script is state_backend._SLIDING_WINDOW_SCRIPT:
            return self._sliding_window(keys[0], *rest)
        if script is state_backend._VERIFY_IS_LOCKED_SCRIPT:
            return self._is_locked(keys[0], keys[1], *rest)
        if script is state_backend._VERIFY_RECORD_FAILURE_SCRIPT:
            return self._record_failure(keys[0], keys[1], *rest)
        if script is state_backend._CAPACITY_ACQUIRE_SCRIPT:
            return self._capacity_acquire(keys[0], *rest)
        if script is state_backend._CAPACITY_RELEASE_SCRIPT:
            return self._capacity_release(keys[0])
        if script is state_backend._QUOTA_RESERVE_SCRIPT:
            return self._quota_reserve(keys[0], keys[1], *rest)
        if script is state_backend._QUOTA_SETTLE_SCRIPT:
            return self._quota_settle(keys[0], keys[1], keys[2], *rest)
        if script is state_backend._QUOTA_RELEASE_SCRIPT:
            return self._quota_release(keys[0], keys[1], *rest)
        raise AssertionError("unknown script")

    async def set(self, key, value, nx=False, ex=None):
        if nx and key in self.store:
            return None
        self.store[key] = value
        return True

    async def get(self, key):
        return self.store.get(key)

    async def delete(self, *keys):
        removed = 0
        for key in keys:
            if self.store.pop(key, None) is not None:
                removed += 1
        return removed

    def _sliding_window(self, key, now_ms, window_ms, limit, member):
        hits = [(s, m) for s, m in self.store.get(key, []) if s > now_ms - window_ms]
        if len(hits) >= limit:
            self.store[key] = hits
            return 0
        hits.append((now_ms, member))
        self.store[key] = hits
        return 1

    def _is_locked(self, lock_key, fails_key, max_attempts, lockout_seconds):
        if lock_key in self.store:
            return 1
        if self.store.get(fails_key, 0) >= max_attempts:
            self.store[lock_key] = "1"
            return 1
        return 0

    def _record_failure(self, lock_key, fails_key, max_attempts, lockout_seconds, _ttl):
        fails = self.store.get(fails_key, 0) + 1
        self.store[fails_key] = fails
        if fails >= max_attempts:
            self.store[lock_key] = "1"
        return fails

    def _capacity_acquire(self, key, max_slots):
        current = self.store.get(key, 0)
        if current >= max_slots:
            return 0
        self.store[key] = current + 1
        return 1

    def _capacity_release(self, key):
        current = self.store.get(key, 0)
        if current <= 0:
            return 0
        self.store[key] = current - 1
        return 1

    def _quota_reserve(self, resv_key, session_key, session_id, amount, consumed, limit, _ttl, user_id):
        reservations = self.store.setdefault(resv_key, {})
        if session_id in reservations:
            return 1
        if consumed + sum(reservations.values()) + amount > limit:
            return 0
        reservations[session_id] = amount
        self.store[session_key] = user_id
        return 1

    def _quota_settle(self, resv_key, consumed_key, session_key, session_id, actual, _ttl, _consumed_ttl):
        reservations = self.store.get(resv_key, {})
        current = reservations.get(session_id)
        self.store.pop(session_key, None)
        if current is None:
            return 0
        del reservations[session_id]
        self.store[consumed_key] = self.store.get(consumed_key, 0) + actual
        if not reservations:
            self.store.pop(resv_key, None)
        return 1

    def _quota_release(self, resv_key, session_key, session_id, _ttl):
        reservations = self.store.get(resv_key, {})
        current = reservations.pop(session_id, None)
        self.store.pop(session_key, None)
        if not reservations:
            self.store.pop(resv_key, None)
        return 1 if current is not None else 0


@pytest.mark.asyncio
async def test_redis_rate_limit_invokes_sliding_window_script():
    client = StubRedis()
    backend = RedisRateLimitBackend(client)

    assert await backend.check_and_record("client", limit=2, window_seconds=60.0, now=100.0)
    assert await backend.check_and_record("client", limit=2, window_seconds=60.0, now=101.0)
    assert not await backend.check_and_record("client", limit=2, window_seconds=60.0, now=102.0)

    script, numkeys, args = client.eval_calls[0]
    assert script is state_backend._SLIDING_WINDOW_SCRIPT
    assert numkeys == 1
    assert args[0] == "conjecta:rate:client"
    assert args[1] == 100_000  # now in ms
    assert args[2] == 60_000  # window in ms
    assert args[3] == 2  # limit


@pytest.mark.asyncio
async def test_redis_throttle_cooldown_window_and_lockout():
    client = StubRedis()
    backend = RedisThrottleBackend(client)

    assert await backend.check_and_record_cooldown("sms:phone:p", cooldown_seconds=60)
    assert not await backend.check_and_record_cooldown("sms:phone:p", cooldown_seconds=60)

    assert await backend.check_and_record_window(
        "sms:ip:h", limit=1, window_seconds=60.0, now=100.0
    )
    assert not await backend.check_and_record_window(
        "sms:ip:h", limit=1, window_seconds=60.0, now=101.0
    )

    for _ in range(5):
        await backend.record_failure("verify:p", max_attempts=5, lockout_seconds=900)
    assert await backend.is_locked("verify:p", max_attempts=5, lockout_seconds=900)
    await backend.record_success("verify:p")
    assert not await backend.is_locked("verify:p", max_attempts=5, lockout_seconds=900)

    failure_call = next(
        call
        for call in client.eval_calls
        if call[0] is state_backend._VERIFY_RECORD_FAILURE_SCRIPT
    )
    assert failure_call[1] == 2
    assert failure_call[2][0] == "conjecta:verify-lock:verify:p"
    assert failure_call[2][1] == "conjecta:verify-fails:verify:p"


@pytest.mark.asyncio
async def test_redis_capacity_acquire_release():
    client = StubRedis()
    backend = RedisSolveCapacityBackend(client, max_in_flight=lambda: 2)

    assert await backend.try_acquire()
    assert await backend.try_acquire()
    assert not await backend.try_acquire()
    assert await backend.in_flight() == 2
    await backend.release()
    assert await backend.in_flight() == 1
    await backend.release()
    await backend.release()  # floor at zero
    assert await backend.in_flight() == 0

    script, numkeys, args = client.eval_calls[0]
    assert script is state_backend._CAPACITY_ACQUIRE_SCRIPT
    assert args[0] == "conjecta:capacity:in_flight"
    assert args[1] == 2


@pytest.mark.asyncio
async def test_redis_quota_reserve_settle_release_semantics():
    client = StubRedis()
    backend = RedisQuotaBackend(client)

    assert await backend.reserve("s1", "u1", 300, consumed=0, limit=500)
    script, numkeys, args = client.eval_calls[-1]
    assert script is state_backend._QUOTA_RESERVE_SCRIPT
    assert numkeys == 2
    assert args[0] == "conjecta:quota:reservations:u1"
    assert args[1] == "conjecta:quota:session:s1"
    assert list(args[2:]) == ["s1", 300, 0, 500, state_backend.RESERVATION_TTL_SECONDS, "u1"]

    # A second reservation that together exceeds the budget is rejected.
    assert not await backend.reserve("s2", "u1", 300, consumed=0, limit=500)
    # Re-reserving the same session is idempotent.
    assert await backend.reserve("s1", "u1", 300, consumed=0, limit=500)

    await backend.settle("s1", 120)
    consumed_key = next(k for k in client.store if k.startswith("conjecta:quota:consumed:u1:"))
    assert client.store[consumed_key] == 120
    assert "conjecta:quota:reservations:u1" not in client.store

    # Double settle must not double-count (session marker is gone).
    eval_count = len(client.eval_calls)
    await backend.settle("s1", 120)
    assert len(client.eval_calls) == eval_count
    assert client.store[consumed_key] == 120

    # Release drops the reservation without charging.
    assert await backend.reserve("s3", "u1", 200, consumed=120, limit=500)
    await backend.release("s3")
    assert client.store[consumed_key] == 120
    assert "conjecta:quota:reservations:u1" not in client.store


# ---------------------------------------------------------------------------
# Factory and config
# ---------------------------------------------------------------------------


def test_factory_defaults_to_memory():
    backend = build_state_backend({"state_backend": "memory"})
    assert isinstance(backend.rate_limit, InMemoryRateLimitBackend)
    assert isinstance(backend.throttle, InMemoryThrottleBackend)
    assert isinstance(backend.capacity, InMemorySolveCapacityBackend)
    assert isinstance(backend.quota, InMemoryQuotaBackend)


def test_factory_accepts_full_config():
    config = default_config()
    assert config.web.state_backend == "memory"
    assert config.web.redis_url is None
    backend = build_state_backend(config)
    assert isinstance(backend.quota, InMemoryQuotaBackend)


def test_factory_rejects_unknown_backend():
    with pytest.raises(ValueError, match="Unknown state_backend"):
        build_state_backend({"state_backend": "etcd"})


def test_factory_redis_requires_url():
    with pytest.raises(ValueError, match="redis_url"):
        build_state_backend({"state_backend": "redis"})


def test_factory_redis_requires_package_or_builds():
    config = {"state_backend": "redis", "redis_url": "redis://localhost:6379/0"}
    if importlib.util.find_spec("redis") is None:
        with pytest.raises(RuntimeError, match="redis"):
            build_state_backend(config)
    else:
        backend = build_state_backend(config)
        assert isinstance(backend.quota, RedisQuotaBackend)


def test_config_loads_web_section(tmp_path):
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        '[web]\nstate_backend = "redis"\nredis_url = "redis://localhost:6379/0"\n'
    )
    config = load_config(config_path)
    assert config.web.state_backend == "redis"
    assert config.web.redis_url == "redis://localhost:6379/0"
