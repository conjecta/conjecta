"""FastAPI web app with NDJSON streaming for the math agent.

Route handlers live in the ``*_routes`` modules; shared services and runtime
state live in ``math_agent.web.agent_factory``. This module only wires the app
object, middleware, lifespan, and static mounts together.
"""
from __future__ import annotations

import os
from dotenv import load_dotenv

# Load environment variables from .env in production, but not during pytest runs
# so tests remain isolated from local secrets.
if not os.environ.get("PYTEST_CURRENT_TEST"):
    load_dotenv()

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse

from math_agent.config import load_config
from math_agent.log_config import setup_logging
from math_agent.math_news.refresh import MathNewsRefresher
from math_agent.math_news.store import MathNewsStore
from math_agent.web import agent_factory
from math_agent.web import hitl_auto_resolve
from math_agent.web.security import (
    InMemoryRateLimiter,
    optional_auth_user,
    request_rate_key,
    require_http_app_access,
)
from math_agent.web.operations import (
    reset_usage_context,
    set_usage_context,
)
from math_agent.web.phone_auth import router as phone_auth_router
from math_agent.web.billing_routes import router as billing_router
from math_agent.web.friends_routes import router as friends_router
from math_agent.web.user_memory_routes import router as user_memory_router
from math_agent.web.pages_routes import STATIC_DIR, WEB_DIR, router as pages_router
from math_agent.web.projects_routes import router as projects_router
from math_agent.web.knowledge_routes import router as knowledge_router
from math_agent.web.solve_routes import router as solve_router
from math_agent.web.research_routes import router as research_router
from math_agent.web.admin_routes import router as admin_router

# Re-exports kept for backwards compatibility (tests and integrations import
# these helpers from ``math_agent.web.app``).
from math_agent.web.agent_factory import (  # noqa: F401
    _PROJECT_STORE_CACHE,
    _platform_api_key,
    _solve_usage,
    default_model_string,
    persist_pending_turn,
    persist_turn,
    prefix_history,
)

web_log = logging.getLogger("math_agent.web")

rate_limiter = InMemoryRateLimiter.from_env()

# Default thread pool size for asyncio.to_thread. Sized for I/O-bound work
# (storage, Supabase, decoding), not CPU parallelism.
DEFAULT_THREAD_POOL_WORKERS = 32
_default_executor: ThreadPoolExecutor | None = None


def _fastapi_docs_kwargs() -> dict[str, str | None]:
    docs_enabled = os.getenv("CONJECTA_ENABLE_DOCS", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    return {
        "docs_url": "/docs" if docs_enabled else None,
        "redoc_url": "/redoc" if docs_enabled else None,
        "openapi_url": "/openapi.json" if docs_enabled else None,
    }


def _default_executor_workers() -> int:
    raw = os.getenv("CONJECTA_THREAD_POOL_WORKERS", "").strip()
    try:
        workers = int(raw) if raw else DEFAULT_THREAD_POOL_WORKERS
    except ValueError:
        workers = DEFAULT_THREAD_POOL_WORKERS
    return max(4, min(workers, 128))


def _install_default_executor() -> None:
    """Size the thread pool that backs every ``asyncio.to_thread`` call.

    Storage reads/writes, Supabase calls, attachment decoding, and quota checks
    all run there. Python's default is ``min(32, cpu_count + 4)`` — 8 threads on
    a 4-core host — which caps concurrent solves well below what the event loop
    and memory can support.
    """
    global _default_executor
    workers = _default_executor_workers()
    _default_executor = ThreadPoolExecutor(
        max_workers=workers, thread_name_prefix="conjecta-worker"
    )
    asyncio.get_running_loop().set_default_executor(_default_executor)
    web_log.info("Default thread pool sized to %d workers", workers)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    from math_agent.web.jwt_auth import jwt_secret
    from math_agent.agent.mcp_client import McpClient

    # Validate security-critical configuration before accepting traffic.
    jwt_secret()
    _install_default_executor()
    config = load_config()
    agent_factory._shared_mcp_client = McpClient(config.mcp_servers)
    await agent_factory._shared_mcp_client.initialize()
    seed_path = Path("data/math_news.jsonl")
    agent_factory.math_news_store = MathNewsStore(
        path=Path(os.getenv("CONJECTA_MATH_NEWS_STORE") or config.logging.dir or "logs") / "math_news.jsonl",
        seed_path=seed_path if seed_path.exists() else None,
    )
    agent_factory.math_news_refresher = MathNewsRefresher(config.math_news, agent_factory.math_news_store)
    agent_factory.math_news_refresher.start()
    prefetch_task = asyncio.create_task(agent_factory._prefetch_lean_workspace())
    from math_agent.web.run_recovery import recover_interrupted_runs

    recovery_task = asyncio.create_task(recover_interrupted_runs())
    try:
        yield
    finally:
        await agent_factory.math_news_refresher.stop()
        prefetch_task.cancel()
        recovery_task.cancel()
        await asyncio.gather(prefetch_task, recovery_task, return_exceptions=True)
        await hitl_auto_resolve.shutdown_auto_resolve_timers()
        await agent_factory.lean_jobs.shutdown()
        await agent_factory.post_solve_tasks.shutdown()
        if agent_factory._shared_mcp_client is not None:
            await agent_factory._shared_mcp_client.close()
            agent_factory._shared_mcp_client = None
        global _default_executor
        if _default_executor is not None:
            # Do not block shutdown on in-flight store writes; they are all
            # idempotent appends that a restart replays or discards. Queued
            # work is left to drain rather than cancelled — cancellation buys
            # nothing here and only risks tearing down teardown-time work.
            _default_executor.shutdown(wait=False)
            _default_executor = None


app = FastAPI(title="Conjecta", lifespan=lifespan, **_fastapi_docs_kwargs())
app.include_router(phone_auth_router)
app.include_router(billing_router)
app.include_router(user_memory_router)
app.include_router(friends_router)
app.include_router(pages_router)
app.include_router(projects_router)
app.include_router(knowledge_router)
app.include_router(solve_router)
app.include_router(research_router)
app.include_router(admin_router)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR), check_dir=False), name="static")
app.mount("/assets", StaticFiles(directory=str(WEB_DIR / "assets"), check_dir=False), name="project-assets")


_AUTH_PUBLIC_PREFIXES = (
    "/api/auth/send-code",
    "/api/auth/verify-code",
    "/api/auth/config",
    "/api/auth/logout",
    "/api/admin/usage",
    "/api/version",
    "/api/contact",
    "/api/share/",
    "/api/knowledge-cards/public",
)


def _is_public_knowledge_card_path(path: str, method: str) -> bool:
    """Return True for read-only public card endpoints that bypass the app-access gate."""
    if method != "GET":
        return False
    if path == "/api/knowledge-cards/public":
        return True
    parts = path.split("/")
    # /api/knowledge-cards/{card_id}
    if len(parts) == 4 and parts[1] == "api" and parts[2] == "knowledge-cards":
        return True
    # /api/knowledge-cards/{card_id}/export/{format}
    if (
        len(parts) == 6
        and parts[1] == "api"
        and parts[2] == "knowledge-cards"
        and parts[4] == "export"
    ):
        return True
    # /api/knowledge-cards/{card_id}/comments
    if (
        len(parts) == 5
        and parts[1] == "api"
        and parts[2] == "knowledge-cards"
        and parts[4] == "comments"
    ):
        return True
    return False


@app.middleware("http")
async def app_security_middleware(request: Request, call_next):
    usage_token = None
    try:
        if request.url.path.startswith("/api/"):
            rate_limiter.check(request_rate_key(request))
            public_prefix = any(request.url.path.startswith(p) for p in _AUTH_PUBLIC_PREFIXES)
            public_card = _is_public_knowledge_card_path(request.url.path, request.method)
            if not public_prefix and not public_card:
                require_http_app_access(request)
            user = optional_auth_user(request)
            if user is not None:
                operation = request.url.path.removeprefix("/api/").replace("/", "-")[:64]
                usage_token = set_usage_context(
                    user_id=user.user_id,
                    session_id=None,
                    operation=operation or "api",
                )
    except HTTPException as exc:
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
    try:
        return await call_next(request)
    finally:
        if usage_token is not None:
            reset_usage_context(usage_token)


def _init_logging() -> None:
    config = load_config()
    if config.logging.enabled:
        setup_logging(level=config.logging.level, log_dir=config.logging.dir)


_init_logging()


def main():
    uvicorn.run(
        app,
        host="127.0.0.1",
        port=8000,
    )


if __name__ == "__main__":
    main()
