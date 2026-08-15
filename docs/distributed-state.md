# Distributed state backend

The web layer keeps four kinds of admission-control state: request rate
limiting, SMS send throttling + verify-failure lockout, concurrent-solve
capacity, and free-tier quota reservations. All of them are
check-and-record operations that are only correct if every serving process
sees the same state.

## Configuration

```toml
[web]
state_backend = "memory"                  # default; "redis" for multi-replica
redis_url = "redis://localhost:6379/0"    # required when state_backend = "redis"
```

- `memory` (default): everything lives in the process. Correct for a single
  replica; with N replicas every budget is effectively multiplied by N, and
  the quota check-then-record race reopens across processes.
- `redis`: state lives in Redis and every mutation is a single Lua script
  (`EVAL`), so check-and-record is atomic across replicas. Requires the
  optional dependency: `pip install math-agent[redis]`.

The bundle is built once per process by
`math_agent.web.state_backend.build_state_backend(config)` (in lifespan,
falling back to lazy init) and shared via `get_state_backend()`.

## Quota data model and the atomic reservation

The durable billing ledger stays the Supabase `UsageStore`; the state
backend only tracks **in-flight reservations**, closing the race where two
concurrent solves both passed the daily-quota check before either recorded
usage.

At solve start the route reads `daily_usage(user_id)` and calls
`quota.reserve(reservation_id, user_id, remaining, consumed=used, limit=...)`
with `remaining = free_daily_limit - used`. The backend atomically admits
the solve only when `consumed + outstanding(user) + amount <= limit`. Since
each solve holds the user's whole remaining budget, a second concurrent
solve for the same free-tier user is rejected with 429 instead of racing.
At stream end the reservation is `settle`d with the real token usage (after
the durable usage record is written), or `release`d when nothing was
consumed. `reservation_id` is the idempotency key: re-reserve is a no-op
and a double settle counts once.

Redis keys (prefix `conjecta:`):

- `quota:reservations:{user_id}` — hash, field = reservation id, value =
  amount. TTL 2h, refreshed on every mutation.
- `quota:session:{reservation_id}` — marker holding the user id, so settle
  / release can locate the user's hash; same TTL. Its absence makes a
  double settle a no-op.
- `quota:consumed:{user_id}:{utc_date}` — settled-usage mirror for
  observability (TTL 48h); the authoritative record remains the UsageStore.

TTL recovery: if a replica crashes mid-solve, it stops mutating its
reservation keys and they expire within 2h, returning the held budget
automatically. Until then the user may see 429s — deliberate (fail closed).

Other backends: rate limit and the per-IP SMS window are sliding-window
sorted sets (`rate:{key}`, `throttle:{key}`); the per-phone SMS cooldown is
`SET NX EX`; verify lockout is a lock key plus a failure counter; solve
capacity is a single `capacity:in_flight` counter. Note the capacity
counter has no TTL — a crashed replica leaks its slots until the key is
deleted (`DEL conjecta:capacity:in_flight`), which only relaxes admission
control temporarily.

## Deployment note

Run one replica, or a load balancer with sticky sessions, and the default
`memory` backend is fine — that is the historical behavior. For two or more
replicas behind round-robin, set `state_backend = "redis"` and point every
replica at the same `redis_url`, or each replica enforces its own rate
limits, SMS throttles, and quota checks.
