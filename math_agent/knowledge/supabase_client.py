"""Shared Supabase client helpers (prefer service role for server writes)."""
from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, TypeVar

log = logging.getLogger("math_agent.knowledge.supabase_client")

T = TypeVar("T")


@dataclass(frozen=True)
class SupabaseConfig:
    url: str
    key: str
    using_service_role: bool = False

    @classmethod
    def from_env(cls, *, prefer_service_role: bool = True) -> "SupabaseConfig | None":
        url = (
            os.getenv("SUPABASE_URL")
            or os.getenv("VITE_SUPABASE_URL")
            or os.getenv("NEXT_PUBLIC_SUPABASE_URL")
            or ""
        ).strip()
        service_key = (os.getenv("SUPABASE_SERVICE_ROLE_KEY") or "").strip()
        anon_key = (
            os.getenv("SUPABASE_ANON_KEY")
            or os.getenv("VITE_SUPABASE_ANON_KEY")
            or os.getenv("NEXT_PUBLIC_SUPABASE_ANON_KEY")
            or ""
        ).strip()
        if prefer_service_role and url and service_key:
            return cls(url=url, key=service_key, using_service_role=True)
        if url and anon_key:
            return cls(url=url, key=anon_key, using_service_role=False)
        return None


def service_role_configured() -> bool:
    cfg = SupabaseConfig.from_env(prefer_service_role=True)
    return bool(cfg and cfg.using_service_role)


# Per-thread client cache. httpx sessions inside supabase-py are not safe to
# share across concurrent asyncio.to_thread workers; thread-local clients avoid
# RemoteProtocolError ("Server disconnected") under parallel friends/profile
# fetches.
_thread_local = threading.local()


def _thread_client_cache() -> dict[tuple[str, str], Any]:
    cache = getattr(_thread_local, "clients", None)
    if cache is None:
        cache = {}
        _thread_local.clients = cache
    return cache


def clear_supabase_client_cache() -> None:
    """Drop cached clients for the current thread (tests / credential rotation)."""
    cache = getattr(_thread_local, "clients", None)
    if cache is None:
        return
    for client in list(cache.values()):
        _close_supabase_client(client)
    cache.clear()


def _close_supabase_client(client: Any) -> None:
    """Best-effort close of the underlying httpx session."""
    for attr in ("postgrest", "auth", "storage", "functions"):
        sub = getattr(client, attr, None)
        session = getattr(sub, "session", None) if sub is not None else None
        if session is None:
            continue
        close = getattr(session, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                pass


def _build_httpx_client() -> Any:
    """Prefer HTTP/1.1 — HTTP/2 multiplexed sessions commonly disconnect under load."""
    import httpx

    return httpx.Client(
        http2=False,
        timeout=httpx.Timeout(120.0, connect=10.0),
        # Ignore ambient HTTP(S)_PROXY / ALL_PROXY so server-side Supabase
        # calls do not require optional socks extras from the host env.
        trust_env=False,
    )


def create_supabase_client(
    *,
    prefer_service_role: bool = True,
    require_service_role: bool = False,
) -> Any:
    cfg = SupabaseConfig.from_env(
        prefer_service_role=prefer_service_role or require_service_role
    )
    if require_service_role and not (cfg and cfg.using_service_role):
        raise RuntimeError(
            "Server-side Supabase access requires SUPABASE_URL and "
            "SUPABASE_SERVICE_ROLE_KEY; an anon key cannot access the "
            "server-only Conjecta tables."
        )
    if cfg is None:
        raise RuntimeError(
            "Supabase is not configured. Set SUPABASE_URL (or VITE_SUPABASE_URL) "
            "and SUPABASE_SERVICE_ROLE_KEY (preferred) or an anon key."
        )
    cache_key = (cfg.url, cfg.key)
    cache = _thread_client_cache()
    client = cache.get(cache_key)
    if client is None:
        try:
            from supabase import create_client
        except ImportError as exc:
            raise RuntimeError(
                "Supabase client requires the optional 'supabase' package."
            ) from exc
        try:
            from supabase.lib.client_options import SyncClientOptions

            options = SyncClientOptions(httpx_client=_build_httpx_client())
            client = create_client(cfg.url, cfg.key, options=options)
        except TypeError:
            # Older / test fakes that only accept (url, key).
            client = create_client(cfg.url, cfg.key)
        cache[cache_key] = client
    return client


def is_transient_supabase_error(exc: BaseException) -> bool:
    name = type(exc).__name__
    if name in {
        "RemoteProtocolError",
        "ConnectError",
        "ReadTimeout",
        "WriteTimeout",
        "ConnectTimeout",
        "PoolTimeout",
        "NetworkError",
    }:
        return True
    module = type(exc).__module__ or ""
    if "httpx" in module or "httpcore" in module:
        text = str(exc).lower()
        return any(
            token in text
            for token in (
                "server disconnected",
                "connection reset",
                "timed out",
                "temporarily",
                "try again",
            )
        )
    return False


def run_supabase(
    fn: Callable[[], T],
    *,
    attempts: int = 3,
    base_delay_seconds: float = 0.1,
) -> T:
    """Run a sync Supabase call with retries for transient transport failures.

    On retry, drop the thread-local httpx client so the next
    ``create_supabase_client()`` opens a fresh connection — stale HTTP/2
    sessions are the usual cause of ``RemoteProtocolError: Server disconnected``.
    Callers that cache a client on ``self`` should resolve it via
    ``create_supabase_client()`` (or a property that does) so retries see the
    new session.
    """
    last: BaseException | None = None
    for attempt in range(max(1, attempts)):
        try:
            return fn()
        except BaseException as exc:
            last = exc
            if not is_transient_supabase_error(exc) or attempt >= attempts - 1:
                raise
            delay = base_delay_seconds * (2**attempt)
            log.warning(
                "Transient Supabase error (%s); retrying in %.2fs (%s/%s)",
                type(exc).__name__,
                delay,
                attempt + 1,
                attempts,
            )
            clear_supabase_client_cache()
            time.sleep(delay)
    assert last is not None
    raise last


async def run_supabase_async(
    fn: Callable[[], T],
    *,
    attempts: int = 3,
    base_delay_seconds: float = 0.1,
) -> T:
    """Async counterpart of ``run_supabase``: run each attempt in a worker
    thread and back off with ``asyncio.sleep``.

    The sync version sleeps inside the worker thread, holding a slot in the
    shared ``to_thread`` pool for the whole backoff. Awaiting the delay here
    releases that slot between attempts, so a Supabase blip cannot consume
    pool capacity that unrelated work needs.
    """
    import asyncio

    last: BaseException | None = None
    for attempt in range(max(1, attempts)):
        try:
            # The cache is thread-local, so the reset must run in the same
            # worker thread as the call whose client went stale.
            def _attempt(reset: bool = attempt > 0) -> T:
                if reset:
                    clear_supabase_client_cache()
                return fn()

            return await asyncio.to_thread(_attempt)
        except BaseException as exc:
            last = exc
            if not is_transient_supabase_error(exc) or attempt >= attempts - 1:
                raise
            delay = base_delay_seconds * (2**attempt)
            log.warning(
                "Transient Supabase error (%s); retrying in %.2fs (%s/%s)",
                type(exc).__name__,
                delay,
                attempt + 1,
                attempts,
            )
            await asyncio.sleep(delay)
    assert last is not None
    raise last
