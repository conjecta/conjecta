"""Pluggable state backends for web-layer admission control.

Rate limiting, SMS throttling/lockout, solve capacity, and quota
reservations are all check-and-record operations that must be atomic across
every process serving traffic. The default in-memory implementations keep
the historical single-process behavior (correct only for a single replica);
the Redis implementations move the same semantics into shared state via Lua
scripts so multi-replica deployments enforce one global budget. Select with
``web.state_backend`` ("memory" | "redis") and ``web.redis_url`` in
config.toml; see docs/distributed-state.md.
"""
from __future__ import annotations

import threading
import time
from collections import OrderedDict, deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol
from uuid import uuid4

from math_agent.web.security import MAX_RATE_LIMIT_KEYS

# Reservations outlive any solve; a crashed process stops mutating its
# reservation keys, so they disappear once this TTL expires.
RESERVATION_TTL_SECONDS = 2 * 3600
# Settled-consumed mirror keys are observability only; expire after 2 days.
CONSUMED_TTL_SECONDS = 48 * 3600
# Verify-failure counters clear on success; this expiry just bounds memory.
VERIFY_FAILURE_TTL_SECONDS = 24 * 3600
DEFAULT_REDIS_PREFIX = "conjecta:"


class RateLimitBackend(Protocol):
    """Sliding-window check-and-record: one hit per call for ``key``."""

    async def check_and_record(
        self,
        key: str,
        *,
        limit: int,
        window_seconds: float,
        now: float | None = None,
    ) -> bool:
        """Record a hit; return True while under ``limit`` hits per window.

        ``limit`` <= 0 disables limiting. ``now`` is a testing hook for the
        in-memory backend (monotonic clock); the Redis backend uses wall time
        because the window is shared across processes.
        """
        ...


class ThrottleBackend(Protocol):
    """SMS send-frequency throttling and verify-failure lockout."""

    async def check_and_record_cooldown(
        self,
        key: str,
        *,
        cooldown_seconds: float,
        now: float | None = None,
    ) -> bool:
        """Record an action; False when the previous one is still cooling down."""
        ...

    async def check_and_record_window(
        self,
        key: str,
        *,
        limit: int,
        window_seconds: float,
        now: float | None = None,
    ) -> bool:
        """Sliding-window variant of the rate limiter, for per-IP budgets."""
        ...

    async def is_locked(
        self,
        key: str,
        *,
        max_attempts: int,
        lockout_seconds: float,
        now: float | None = None,
    ) -> bool:
        """True while locked out; an expired lock re-arms when failures persist."""
        ...

    async def record_failure(
        self,
        key: str,
        *,
        max_attempts: int,
        lockout_seconds: float,
        now: float | None = None,
    ) -> None:
        """Count a failure; reaching ``max_attempts`` starts the lockout."""
        ...

    async def record_success(self, key: str) -> None:
        """Clear failure count and lockout after a successful verify."""
        ...


class SolveCapacityBackend(Protocol):
    """Admission control for concurrent solves: acquire/release a slot."""

    async def try_acquire(self) -> bool:
        """Take a slot without waiting. False means at capacity."""
        ...

    async def release(self) -> None:
        ...

    async def in_flight(self) -> int:
        ...


class QuotaBackend(Protocol):
    """Atomic quota reservation layered over the durable usage ledger.

    ``consumed`` is the durable usage the caller already read (UsageStore);
    the backend owns only the in-flight reservations and atomically enforces
    ``consumed + outstanding(user_id) + amount <= limit``. ``session_id`` is
    the idempotency key: re-reserving is a no-op, and a double settle counts
    once.
    """

    async def reserve(
        self,
        session_id: str,
        user_id: str,
        amount: int,
        *,
        consumed: int,
        limit: int,
    ) -> bool:
        ...

    async def settle(self, session_id: str, actual_amount: int) -> None:
        """Close the reservation, charging the real usage."""
        ...

    async def release(self, session_id: str) -> None:
        """Close the reservation without charging (nothing was consumed)."""
        ...


# ---------------------------------------------------------------------------
# In-memory implementations (default; single replica only)
# ---------------------------------------------------------------------------


class InMemoryRateLimitBackend:
    """Process-local sliding-window limiter mirroring security.InMemoryRateLimiter."""

    def __init__(self, *, max_keys: int = MAX_RATE_LIMIT_KEYS) -> None:
        self._max_keys = max_keys
        self._hits: OrderedDict[str, deque[float]] = OrderedDict()
        self._lock = threading.Lock()

    def _evict(self, window_start: float) -> None:
        """Drop keys whose window fully expired, then cap the total size."""
        while self._hits:
            key, hits = next(iter(self._hits.items()))
            if hits and hits[-1] >= window_start:
                break
            del self._hits[key]
        while len(self._hits) > self._max_keys:
            self._hits.popitem(last=False)

    async def check_and_record(
        self,
        key: str,
        *,
        limit: int,
        window_seconds: float,
        now: float | None = None,
    ) -> bool:
        if limit <= 0:
            return True
        now = time.monotonic() if now is None else now
        window_start = now - window_seconds
        with self._lock:
            self._evict(window_start)
            hits = self._hits.get(key)
            if hits is None:
                hits = deque()
                self._hits[key] = hits
            else:
                self._hits.move_to_end(key)
            while hits and hits[0] < window_start:
                hits.popleft()
            if len(hits) >= limit:
                return False
            hits.append(now)
            return True


@dataclass
class _VerifyState:
    failures: int = 0
    locked_until: float = 0.0


class InMemoryThrottleBackend:
    """Process-local SMS throttle: per-key cooldown, per-key window, lockout."""

    def __init__(self) -> None:
        self._cooldowns: dict[str, float] = {}
        self._windows: dict[str, list[float]] = {}
        self._verify: dict[str, _VerifyState] = {}
        self._lock = threading.Lock()

    async def check_and_record_cooldown(
        self,
        key: str,
        *,
        cooldown_seconds: float,
        now: float | None = None,
    ) -> bool:
        now = time.monotonic() if now is None else now
        with self._lock:
            last = self._cooldowns.get(key, 0.0)
            if last > now - cooldown_seconds:
                return False
            self._cooldowns[key] = now
            stale = [k for k, ts in self._cooldowns.items() if ts <= now - cooldown_seconds * 2]
            for stale_key in stale:
                del self._cooldowns[stale_key]
            return True

    async def check_and_record_window(
        self,
        key: str,
        *,
        limit: int,
        window_seconds: float,
        now: float | None = None,
    ) -> bool:
        now = time.monotonic() if now is None else now
        with self._lock:
            hits = [ts for ts in self._windows.get(key, []) if ts > now - window_seconds]
            if len(hits) >= limit:
                return False
            hits.append(now)
            self._windows[key] = hits
            stale = [
                k
                for k, values in self._windows.items()
                if not values or values[-1] <= now - window_seconds * 2
            ]
            for stale_key in stale:
                del self._windows[stale_key]
            return True

    async def is_locked(
        self,
        key: str,
        *,
        max_attempts: int,
        lockout_seconds: float,
        now: float | None = None,
    ) -> bool:
        now = time.monotonic() if now is None else now
        with self._lock:
            state = self._verify.get(key)
            if state is None:
                return False
            if state.locked_until > now:
                return True
            if state.failures >= max_attempts:
                # Auto-renew lockout on expiry until a success clears it.
                state.locked_until = now + lockout_seconds
                return True
            return False

    async def record_failure(
        self,
        key: str,
        *,
        max_attempts: int,
        lockout_seconds: float,
        now: float | None = None,
    ) -> None:
        now = time.monotonic() if now is None else now
        with self._lock:
            state = self._verify.setdefault(key, _VerifyState())
            state.failures += 1
            if state.failures >= max_attempts:
                state.locked_until = now + lockout_seconds

    async def record_success(self, key: str) -> None:
        with self._lock:
            self._verify.pop(key, None)


class InMemorySolveCapacityBackend:
    """Async facade over a process-local ``SolveCapacity`` counter."""

    def __init__(self, capacity: Any | None = None) -> None:
        if capacity is None:
            from math_agent.web.active_solves import SolveCapacity

            capacity = SolveCapacity()
        self._capacity = capacity

    async def try_acquire(self) -> bool:
        return self._capacity.try_acquire()

    async def release(self) -> None:
        self._capacity.release()

    async def in_flight(self) -> int:
        return self._capacity.in_flight


@dataclass
class _QuotaReservation:
    user_id: str
    amount: int


class InMemoryQuotaBackend:
    """Process-local quota reservations; check-and-hold runs under one lock.

    ``settle`` additionally keeps a per-user consumed tally, but only for
    observability — the durable ledger is the UsageStore the caller reads,
    so the reserve check intentionally uses the caller-supplied ``consumed``
    and never this tally (that would double-count settled usage).
    """

    def __init__(self) -> None:
        self._reservations: dict[str, _QuotaReservation] = {}
        self._settled: dict[str, int] = {}
        self._lock = threading.Lock()

    async def reserve(
        self,
        session_id: str,
        user_id: str,
        amount: int,
        *,
        consumed: int,
        limit: int,
    ) -> bool:
        with self._lock:
            if session_id in self._reservations:
                return True  # idempotent re-reserve
            outstanding = sum(
                r.amount for r in self._reservations.values() if r.user_id == user_id
            )
            if consumed + outstanding + amount > limit:
                return False
            self._reservations[session_id] = _QuotaReservation(user_id=user_id, amount=amount)
            return True

    async def settle(self, session_id: str, actual_amount: int) -> None:
        with self._lock:
            reservation = self._reservations.pop(session_id, None)
            if reservation is None:
                return  # idempotent: a double settle must not double-count
            self._settled[reservation.user_id] = (
                self._settled.get(reservation.user_id, 0) + max(0, actual_amount)
            )

    async def release(self, session_id: str) -> None:
        with self._lock:
            self._reservations.pop(session_id, None)

    def outstanding(self, user_id: str) -> int:
        """Total tokens currently reserved by ``user_id`` (observability/tests)."""
        with self._lock:
            return sum(
                r.amount for r in self._reservations.values() if r.user_id == user_id
            )

    def settled_consumed(self, user_id: str) -> int:
        """Total settled (actual) tokens for ``user_id`` (observability/tests)."""
        with self._lock:
            return self._settled.get(user_id, 0)


# ---------------------------------------------------------------------------
# Redis implementations (multi-replica; Lua scripts keep each op atomic)
# ---------------------------------------------------------------------------

# KEYS[1] = window zset; ARGV = now_ms, window_ms, limit, unique member.
_SLIDING_WINDOW_SCRIPT = """
redis.call('ZREMRANGEBYSCORE', KEYS[1], 0, tonumber(ARGV[1]) - tonumber(ARGV[2]))
if redis.call('ZCARD', KEYS[1]) >= tonumber(ARGV[3]) then
  return 0
end
redis.call('ZADD', KEYS[1], tonumber(ARGV[1]), ARGV[4])
redis.call('PEXPIRE', KEYS[1], tonumber(ARGV[2]))
return 1
"""

# KEYS[1] = lock key, KEYS[2] = failure counter; ARGV = max_attempts, lockout_seconds.
_VERIFY_IS_LOCKED_SCRIPT = """
if redis.call('EXISTS', KEYS[1]) == 1 then
  return 1
end
local fails = tonumber(redis.call('GET', KEYS[2]) or '0')
if fails >= tonumber(ARGV[1]) then
  redis.call('SET', KEYS[1], '1', 'EX', tonumber(ARGV[2]))
  return 1
end
return 0
"""

# KEYS[1] = lock key, KEYS[2] = failure counter;
# ARGV = max_attempts, lockout_seconds, failure_ttl_seconds.
_VERIFY_RECORD_FAILURE_SCRIPT = """
local fails = redis.call('INCR', KEYS[2])
redis.call('EXPIRE', KEYS[2], tonumber(ARGV[3]))
if fails >= tonumber(ARGV[1]) then
  redis.call('SET', KEYS[1], '1', 'EX', tonumber(ARGV[2]))
end
return fails
"""

# KEYS[1] = in-flight counter; ARGV = max slots.
_CAPACITY_ACQUIRE_SCRIPT = """
local current = tonumber(redis.call('GET', KEYS[1]) or '0')
if current >= tonumber(ARGV[1]) then
  return 0
end
redis.call('INCR', KEYS[1])
return 1
"""

_CAPACITY_RELEASE_SCRIPT = """
local current = tonumber(redis.call('GET', KEYS[1]) or '0')
if current <= 0 then
  return 0
end
redis.call('DECR', KEYS[1])
return 1
"""

# KEYS[1] = per-user reservations hash (field=session_id, value=amount),
# KEYS[2] = per-session marker (value=user_id, lets settle/release find the hash).
# ARGV = session_id, amount, consumed, limit, ttl_seconds, user_id.
_QUOTA_RESERVE_SCRIPT = """
if redis.call('HEXISTS', KEYS[1], ARGV[1]) == 1 then
  return 1
end
local total = tonumber(ARGV[3]) + tonumber(ARGV[2])
local amounts = redis.call('HVALS', KEYS[1])
for i = 1, #amounts do
  total = total + tonumber(amounts[i])
end
if total > tonumber(ARGV[4]) then
  return 0
end
redis.call('HSET', KEYS[1], ARGV[1], ARGV[2])
redis.call('EXPIRE', KEYS[1], tonumber(ARGV[5]))
redis.call('SET', KEYS[2], ARGV[6], 'EX', tonumber(ARGV[5]))
return 1
"""

# KEYS[1] = reservations hash, KEYS[2] = consumed counter, KEYS[3] = session marker.
# ARGV = session_id, actual_amount, reservation_ttl_seconds, consumed_ttl_seconds.
_QUOTA_SETTLE_SCRIPT = """
local current = redis.call('HGET', KEYS[1], ARGV[1])
redis.call('DEL', KEYS[3])
if not current then
  return 0
end
redis.call('HDEL', KEYS[1], ARGV[1])
redis.call('INCRBY', KEYS[2], tonumber(ARGV[2]))
redis.call('EXPIRE', KEYS[2], tonumber(ARGV[4]))
if redis.call('HLEN', KEYS[1]) == 0 then
  redis.call('DEL', KEYS[1])
else
  redis.call('EXPIRE', KEYS[1], tonumber(ARGV[3]))
end
return 1
"""

# KEYS[1] = reservations hash, KEYS[2] = session marker.
# ARGV = session_id, reservation_ttl_seconds.
_QUOTA_RELEASE_SCRIPT = """
local current = redis.call('HGET', KEYS[1], ARGV[1])
redis.call('DEL', KEYS[2])
if not current then
  return 0
end
redis.call('HDEL', KEYS[1], ARGV[1])
if redis.call('HLEN', KEYS[1]) == 0 then
  redis.call('DEL', KEYS[1])
else
  redis.call('EXPIRE', KEYS[1], tonumber(ARGV[2]))
end
return 1
"""


class _RedisBase:
    def __init__(self, client: Any, *, prefix: str = DEFAULT_REDIS_PREFIX) -> None:
        self._client = client
        self._prefix = prefix

    def _key(self, *parts: str) -> str:
        return self._prefix + ":".join(parts)


class RedisRateLimitBackend(_RedisBase):
    """Shared sliding-window limiter backed by a per-key sorted set."""

    async def check_and_record(
        self,
        key: str,
        *,
        limit: int,
        window_seconds: float,
        now: float | None = None,
    ) -> bool:
        if limit <= 0:
            return True
        now_ms = int((time.time() if now is None else now) * 1000)
        member = f"{now_ms}-{uuid4().hex}"
        result = await self._client.eval(
            _SLIDING_WINDOW_SCRIPT,
            1,
            self._key("rate", key),
            now_ms,
            int(window_seconds * 1000),
            limit,
            member,
        )
        return bool(result)


class RedisThrottleBackend(_RedisBase):
    """Shared SMS throttle: SET NX EX cooldowns, zset windows, lockout keys."""

    async def check_and_record_cooldown(
        self,
        key: str,
        *,
        cooldown_seconds: float,
        now: float | None = None,
    ) -> bool:
        result = await self._client.set(
            self._key("cooldown", key),
            1,
            nx=True,
            ex=max(1, int(cooldown_seconds)),
        )
        return bool(result)

    async def check_and_record_window(
        self,
        key: str,
        *,
        limit: int,
        window_seconds: float,
        now: float | None = None,
    ) -> bool:
        now_ms = int((time.time() if now is None else now) * 1000)
        member = f"{now_ms}-{uuid4().hex}"
        result = await self._client.eval(
            _SLIDING_WINDOW_SCRIPT,
            1,
            self._key("throttle", key),
            now_ms,
            int(window_seconds * 1000),
            limit,
            member,
        )
        return bool(result)

    async def is_locked(
        self,
        key: str,
        *,
        max_attempts: int,
        lockout_seconds: float,
        now: float | None = None,
    ) -> bool:
        result = await self._client.eval(
            _VERIFY_IS_LOCKED_SCRIPT,
            2,
            self._key("verify-lock", key),
            self._key("verify-fails", key),
            max_attempts,
            int(lockout_seconds),
        )
        return bool(result)

    async def record_failure(
        self,
        key: str,
        *,
        max_attempts: int,
        lockout_seconds: float,
        now: float | None = None,
    ) -> None:
        await self._client.eval(
            _VERIFY_RECORD_FAILURE_SCRIPT,
            2,
            self._key("verify-lock", key),
            self._key("verify-fails", key),
            max_attempts,
            int(lockout_seconds),
            VERIFY_FAILURE_TTL_SECONDS,
        )

    async def record_success(self, key: str) -> None:
        await self._client.delete(
            self._key("verify-lock", key), self._key("verify-fails", key)
        )


class RedisSolveCapacityBackend(_RedisBase):
    """Shared solve-slot counter; a crashed replica's slots need a key reset."""

    def __init__(
        self,
        client: Any,
        *,
        prefix: str = DEFAULT_REDIS_PREFIX,
        max_in_flight: Callable[[], int],
    ) -> None:
        super().__init__(client, prefix=prefix)
        self._max_in_flight = max_in_flight

    async def try_acquire(self) -> bool:
        result = await self._client.eval(
            _CAPACITY_ACQUIRE_SCRIPT,
            1,
            self._key("capacity", "in_flight"),
            max(1, self._max_in_flight()),
        )
        return bool(result)

    async def release(self) -> None:
        await self._client.eval(
            _CAPACITY_RELEASE_SCRIPT, 1, self._key("capacity", "in_flight")
        )

    async def in_flight(self) -> int:
        return int(await self._client.get(self._key("capacity", "in_flight")) or 0)


class RedisQuotaBackend(_RedisBase):
    """Shared quota reservations.

    Per user, outstanding reservations live in one hash
    (``quota:reservations:{user_id}``, field=session_id, value=amount) with a
    TTL refreshed on every mutation: once a crashed replica stops mutating,
    its reservations expire and the budget self-heals. A per-session marker
    key (``quota:session:{session_id}`` -> user_id, same TTL) lets settle and
    release locate the user's hash and makes a double settle a no-op.
    ``settle`` also increments ``quota:consumed:{user_id}:{utc_date}`` as an
    observability mirror; the durable billing ledger stays the UsageStore.
    """

    def __init__(
        self,
        client: Any,
        *,
        prefix: str = DEFAULT_REDIS_PREFIX,
        reservation_ttl_seconds: int = RESERVATION_TTL_SECONDS,
    ) -> None:
        super().__init__(client, prefix=prefix)
        self._reservation_ttl_seconds = reservation_ttl_seconds

    def _reservations_key(self, user_id: str) -> str:
        return self._key("quota", "reservations", user_id)

    def _session_key(self, session_id: str) -> str:
        return self._key("quota", "session", session_id)

    def _consumed_key(self, user_id: str) -> str:
        day = datetime.now(timezone.utc).date().isoformat()
        return self._key("quota", "consumed", user_id, day)

    async def reserve(
        self,
        session_id: str,
        user_id: str,
        amount: int,
        *,
        consumed: int,
        limit: int,
    ) -> bool:
        result = await self._client.eval(
            _QUOTA_RESERVE_SCRIPT,
            2,
            self._reservations_key(user_id),
            self._session_key(session_id),
            session_id,
            int(amount),
            int(consumed),
            int(limit),
            self._reservation_ttl_seconds,
            user_id,
        )
        return bool(result)

    async def settle(self, session_id: str, actual_amount: int) -> None:
        user_id = await self._client.get(self._session_key(session_id))
        if not user_id:
            return  # expired or already settled: never double-count
        await self._client.eval(
            _QUOTA_SETTLE_SCRIPT,
            3,
            self._reservations_key(user_id),
            self._consumed_key(user_id),
            self._session_key(session_id),
            session_id,
            int(actual_amount),
            self._reservation_ttl_seconds,
            CONSUMED_TTL_SECONDS,
        )

    async def release(self, session_id: str) -> None:
        user_id = await self._client.get(self._session_key(session_id))
        if not user_id:
            return
        await self._client.eval(
            _QUOTA_RELEASE_SCRIPT,
            2,
            self._reservations_key(user_id),
            self._session_key(session_id),
            session_id,
            self._reservation_ttl_seconds,
        )


# ---------------------------------------------------------------------------
# Bundle, factory, and the process-wide singleton
# ---------------------------------------------------------------------------


@dataclass
class StateBackend:
    """The four admission-control backends plus shared resources to close."""

    rate_limit: RateLimitBackend
    throttle: ThrottleBackend
    capacity: SolveCapacityBackend
    quota: QuotaBackend
    _resources: list[Any] = field(default_factory=list)

    async def aclose(self) -> None:
        """Close underlying connections (the Redis client); no-op for memory."""
        for resource in self._resources:
            aclose = getattr(resource, "aclose", None)
            if aclose is not None:
                await aclose()


def build_state_backend(config: Any) -> StateBackend:
    """Build the configured backend bundle.

    Accepts the full ``Config``, its ``web`` section, or a mapping with
    ``state_backend`` ("memory" | "redis") and ``redis_url``.
    """
    if isinstance(config, Mapping):
        kind = str(config.get("state_backend", "memory") or "memory")
        redis_url = config.get("redis_url")
    else:
        web = getattr(config, "web", config)
        kind = str(getattr(web, "state_backend", "memory") or "memory")
        redis_url = getattr(web, "redis_url", None)
    kind = kind.strip().lower()
    if kind == "memory":
        from math_agent.web.active_solves import solve_capacity

        return StateBackend(
            rate_limit=InMemoryRateLimitBackend(),
            throttle=InMemoryThrottleBackend(),
            capacity=InMemorySolveCapacityBackend(solve_capacity),
            quota=InMemoryQuotaBackend(),
        )
    if kind == "redis":
        if not redis_url or not str(redis_url).strip():
            raise ValueError("state_backend='redis' requires web.redis_url to be set.")
        try:
            from redis import asyncio as redis_asyncio
        except ImportError as exc:
            raise RuntimeError(
                "state_backend='redis' requires the redis package; "
                "install it with `pip install math-agent[redis]`."
            ) from exc
        from math_agent.web.active_solves import max_concurrent_solves

        client = redis_asyncio.Redis.from_url(str(redis_url).strip(), decode_responses=True)
        return StateBackend(
            rate_limit=RedisRateLimitBackend(client),
            throttle=RedisThrottleBackend(client),
            capacity=RedisSolveCapacityBackend(client, max_in_flight=max_concurrent_solves),
            quota=RedisQuotaBackend(client),
            _resources=[client],
        )
    raise ValueError(f"Unknown state_backend {kind!r}; expected 'memory' or 'redis'.")


_backend: StateBackend | None = None
_backend_lock = threading.Lock()


def get_state_backend() -> StateBackend:
    """Process-wide backend bundle, built lazily from the loaded config."""
    global _backend
    if _backend is None:
        with _backend_lock:
            if _backend is None:
                from math_agent.config import load_config

                _backend = build_state_backend(load_config())
    return _backend


def set_state_backend(backend: StateBackend | None) -> None:
    """Install the process-wide bundle (None restores lazy default); for tests."""
    global _backend
    with _backend_lock:
        _backend = backend
