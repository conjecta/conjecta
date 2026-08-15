"""Shared service layer for the web app: agent construction, tenant stores,
quota/usage accounting, and process-wide runtime singletons.

Route modules and ``solve_session`` import from here (instead of from
``math_agent.web.app``) so the dependency graph stays acyclic: this module
must never import ``math_agent.web.app`` or any ``*_routes`` module.
"""
from __future__ import annotations

import asyncio
import contextvars
import logging
import os
import weakref
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import HTTPException, Request

from math_agent.config import load_config
from math_agent.llm.factory import (
    create_backend,
    create_backend_for_user,
    create_backend_from_model_string,
    create_prover_backend,
    normalize_model_string,
)
from math_agent.llm.tracking import (
    UsageAccumulator as _UsageAccumulator,
    UsageTrackingBackend as _UsageTrackingBackend,
)
from math_agent.agent.react_state import ProjectContext
from math_agent.agent.plan_memory import PlanMemory
from math_agent.agent.materials import MaterialStore
from math_agent.agent.knowledge.graph import KnowledgeGraph
from math_agent.agent.tools import ToolRegistry
from math_agent.lean.runner import LeanRunner
from math_agent.lean.codegen import LeanCodegen
from math_agent.lean.premise_retriever import PremiseRetriever
from math_agent.web.lean_jobs import LeanJobManager
from math_agent.web.knowledge_selection import (
    normalize_conversation_history as _normalize_conversation_history,
)
from math_agent.web.project_store import (
    _STORE_CACHE,
    ProjectStore,
    project_store_for_root,
    project_store_root_for_user,
    sanitize_user_id,
)
from math_agent.web.security import require_auth_user
from math_agent.web.project_access import resolve_project_access
from math_agent.web.user_memory_routes import user_memory_store_for_user
from math_agent.web.user_memory_store import UserMemoryStore
from math_agent.web.post_solve import PostSolveTaskManager
from math_agent.knowledge.supabase_client import service_role_configured
from math_agent.billing.api_keys import (
    USER_API_MODEL,
    USER_API_PROVIDER,
    decrypt_api_key,
)
from math_agent.billing.models import StoredApiKey, UsageRecord
from math_agent.net_safety import UnsafeFetchURL, validate_public_https_url
from math_agent.billing.pricing import cost_for
from math_agent.billing.quota import (
    free_tokens_per_day,
    is_allowed,
    is_quota_unlimited,
    quota_disabled,
)
from math_agent.billing.usage_store import UsageStore
from math_agent.web.state_backend import get_state_backend

web_log = logging.getLogger("math_agent.web")

_QUOTA_LOCKS: weakref.WeakValueDictionary[str, asyncio.Lock] = weakref.WeakValueDictionary()
lean_jobs = LeanJobManager()
post_solve_tasks = PostSolveTaskManager()
# Backwards-compatible alias for the one process-wide store cache that now
# lives in ``project_store``. Tests clear this to isolate tenant state, so it
# must stay bound to the real dict rather than a private copy.
_PROJECT_STORE_CACHE = _STORE_CACHE
_shared_premise_retriever: PremiseRetriever | None = None
_shared_premise_retriever_task: asyncio.Task[PremiseRetriever | None] | None = None
_shared_mcp_client = None
math_news_store: Any | None = None
math_news_refresher: Any | None = None


async def _initialize_premise_retriever(
    config, runner: LeanRunner
) -> PremiseRetriever:
    if config.lean.mathlib_dep:
        result = await runner.ensure_dependencies()
        if result is not None:
            raise RuntimeError("; ".join(result.errors))
    retriever = PremiseRetriever()
    await asyncio.to_thread(retriever.build_index)
    return retriever


async def _get_shared_premise_retriever(
    config, runner: LeanRunner
) -> PremiseRetriever | None:
    """Return the process-wide immutable premise index, initializing it once."""
    global _shared_premise_retriever, _shared_premise_retriever_task

    if not config.lean.premise_index_enabled:
        return None
    if _shared_premise_retriever is not None:
        return _shared_premise_retriever

    task = _shared_premise_retriever_task
    if task is None:
        task = asyncio.create_task(_initialize_premise_retriever(config, runner))
        _shared_premise_retriever_task = task
    try:
        retriever = await task
    except asyncio.CancelledError:
        if _shared_premise_retriever_task is task:
            _shared_premise_retriever_task = None
        raise
    except Exception as exc:
        if _shared_premise_retriever_task is task:
            _shared_premise_retriever_task = None
        web_log.warning("Premise retriever unavailable: %s", exc)
        return None

    _shared_premise_retriever = retriever
    return retriever


async def _prefetch_lean_workspace() -> None:
    try:
        config = load_config()
        if not config.lean.enabled:
            return
        runner = LeanRunner(config.lean)
        if config.lean.premise_index_enabled:
            retriever = await _get_shared_premise_retriever(config, runner)
            if retriever is not None:
                web_log.info("Lean workspace and premise index are ready")
            return
        if config.lean.mathlib_dep:
            result = await runner.ensure_dependencies()
            if result is not None:
                web_log.warning(
                    "Lean workspace setup failed: %s", "; ".join(result.errors)
                )
            else:
                web_log.info("Lean workspace ready at %s", config.lean.workspace_dir)
    except Exception:
        web_log.exception("Lean workspace prefetch failed")


def _project_store(user_id: str | None = None) -> ProjectStore:
    if user_id:
        return project_store_for_root(project_store_root_for_user(user_id))
    return project_store_for_root()


def _tenant_project_store(request: Request) -> tuple[Any, ProjectStore]:
    user = require_auth_user(request)
    return user, _project_store(user.user_id)


def _project_access_from_request(
    request: Request,
    project_id: str,
    *,
    owner_user_id: str | None = None,
    create_if_missing: bool = False,
) -> tuple[Any, Any]:
    """Return (auth user, ProjectAccess) for a project the actor can use."""
    user = require_auth_user(request)
    owner = owner_user_id
    if owner is None:
        owner = request.query_params.get("owner_user_id") or None
    access = resolve_project_access(
        user.user_id,
        project_id,
        owner_user_id=owner,
        create_if_missing=create_if_missing,
    )
    return user, access


def _attachment_meta(files: Any) -> list[dict[str, Any]]:
    return [{"kind": f.get("kind"), "name": f.get("name")} for f in (files or [])]


def persist_pending_turn(
    store: ProjectStore,
    project_id: str,
    problem: str,
    files: Any,
    *,
    conversation_id: str = "",
) -> dict[str, Any]:
    """Register a conversation turn as soon as a solve starts (answer filled in later)."""
    return store.add_turn(
        project_id,
        {
            "problem": problem,
            "answer": "",
            "attachments": _attachment_meta(files),
            "conversation_id": conversation_id,
        },
    )


def persist_turn(
    store: ProjectStore,
    project_id: str,
    problem: str,
    final_answer: str,
    files: Any,
    *,
    conversation_id: str = "",
    turn_id: str = "",
    verification_status: str | None = None,
    strategy: str | None = None,
    session_id: str | None = None,
    lean_proofs: list[Any] | None = None,
    verification_issues: list[Any] | None = None,
    tool_evidence: list[Any] | None = None,
) -> dict[str, Any]:
    """Archive a completed turn, storing attachment metadata only (never base64 payloads).

    When ``turn_id`` is set, updates the pending turn created at solve start instead of
    appending a duplicate.
    """
    if turn_id:
        try:
            return store.update_turn(
                project_id,
                turn_id,
                answer=final_answer,
                problem=problem,
                verification_status=verification_status,
                strategy=strategy,
                session_id=session_id,
                lean_proofs=lean_proofs,
                verification_issues=verification_issues,
                tool_evidence=tool_evidence,
            )
        except Exception:
            # Fall through to append if the pending turn was deleted mid-solve.
            pass
    return store.add_turn(
        project_id,
        {
            "problem": problem,
            "answer": final_answer,
            "attachments": _attachment_meta(files),
            "conversation_id": conversation_id,
            "verification_status": verification_status,
            "strategy": strategy,
            "session_id": session_id,
            "lean_proofs": lean_proofs,
            "verification_issues": verification_issues,
            "tool_evidence": tool_evidence,
        },
    )

def default_model_string(config) -> str:
    """Web model string (provider/model) used when the client omits `model`."""
    return f"{config.llm.provider}/{config.llm.model}"


def prefix_history(problem: str, conversation_history: Any) -> str:
    turns = _normalize_conversation_history(conversation_history)
    if not turns:
        return problem
    lines = [f"{t['role'].capitalize()}: {t['text']}" for t in turns]
    return "Conversation so far:\n" + "\n".join(lines) + f"\n\nCurrent question:\n{problem}"


def _material_store(user_id: str | None = None) -> MaterialStore:
    base = Path(os.getenv("CONJECTA_MATERIAL_STORE_DIR") or "data/materials").resolve()
    if user_id is not None:
        return MaterialStore(root=base / sanitize_user_id(user_id))
    return MaterialStore(root=base)


def _user_memory_store(user_id: str | None = None) -> UserMemoryStore | None:
    if not user_id:
        return None
    try:
        return user_memory_store_for_user(user_id)
    except Exception as exc:
        web_log.warning("User memory store init failed: %s", exc)
        return None


class KnowledgeStoreUnavailable(RuntimeError):
    """Supabase is configured as the knowledge backend but cannot be opened."""


def _maybe_knowledge_store(user_id: str | None = None) -> Any | None:
    """Return the sole authoritative knowledge store for this tenant.

    Backend is fixed by configuration: service-role Supabase → cloud only;
    otherwise local JSONL. Never silently switches backends after a cloud failure.
    """
    try:
        cloud = _cloud_knowledge_store(user_id)
    except KnowledgeStoreUnavailable as exc:
        raise HTTPException(
            status_code=503,
            detail="Knowledge cloud storage unavailable",
        ) from exc
    if cloud is not None:
        return cloud
    try:
        return _project_store(user_id)
    except Exception as exc:
        web_log.warning("JSONL knowledge store initialization failed: %s", exc)
        return None


def _cloud_knowledge_store(
    user_id: str | None = None, knowledge_config: object | None = None
):
    """Return the cloud knowledge store, or None when running in local mode.

    When Supabase service role is configured, initialization failures raise
    ``KnowledgeStoreUnavailable`` instead of returning None (which would allow
    callers to fall back to JSONL and fork tenant data).
    """
    if not service_role_configured():
        return None
    if not (user_id or "").strip():
        # Fail closed: a cloud store without a tenant would let
        # ``KnowledgeStore`` queries widen to every user's rows.
        raise KnowledgeStoreUnavailable(
            "Supabase knowledge store requires an authenticated user"
        )
    try:
        from math_agent.knowledge.supabase import KnowledgeStore

        if knowledge_config is None:
            from math_agent.config import load_config

            knowledge_config = load_config().knowledge
        return KnowledgeStore(user_id=user_id, knowledge_config=knowledge_config)
    except Exception as exc:
        web_log.exception("Supabase knowledge store unavailable for user=%s", user_id)
        raise KnowledgeStoreUnavailable(
            "Supabase knowledge store is configured but unavailable"
        ) from exc


def _tenant_runtime_root(env_name: str, default: str, user_id: str | None) -> Path:
    return Path(os.getenv(env_name) or default).resolve() / sanitize_user_id(user_id)


async def _load_user_api_key(user_id: str) -> StoredApiKey | None:
    from math_agent.knowledge.supabase_client import (
        SupabaseConfig,
        create_supabase_client,
    )

    if SupabaseConfig.from_env(prefer_service_role=True) is None:
        # Local/dev without Supabase: fall through to platform/env keys.
        return None

    def _fetch() -> dict | None:
        client = create_supabase_client(prefer_service_role=True)
        resp = (
            client.table("conjecta_users")
            .select("api_keys_encrypted")
            .eq("id", user_id)
            .limit(1)
            .execute()
        )
        return (resp.data or [None])[0]

    try:
        data = await asyncio.to_thread(_fetch)
    except Exception as exc:
        # Missing api_keys_* columns before billing migration — treat as unbound.
        msg = str(exc)
        if (
            "PGRST204" in msg
            or "api_keys_encrypted" in msg
            or "Could not find the '" in msg
        ):
            web_log.error(
                "Billing user-key columns missing for %s "
                "(apply docs/supabase_billing_schema.sql); treating as unbound",
                user_id,
            )
            return None
        web_log.error("Failed to load user API key for %s: %s", user_id, exc)
        raise HTTPException(status_code=503, detail="API key configuration error") from exc

    if not data or not data.get("api_keys_encrypted"):
        return None

    def _decrypt() -> StoredApiKey:
        return decrypt_api_key(data["api_keys_encrypted"])

    try:
        stored = await asyncio.to_thread(_decrypt)
    except Exception as exc:
        web_log.error("Failed to decrypt user API key for %s: %s", user_id, exc)
        raise HTTPException(
            status_code=503, detail="API key configuration error"
        ) from exc
    if stored.legacy_provider:
        raise HTTPException(status_code=409, detail="API_ENDPOINT_REBIND_REQUIRED")
    try:
        await validate_public_https_url(stored.base_url)
    except UnsafeFetchURL as exc:
        web_log.warning("Rejected unsafe user Base URL for %s: %s", user_id, exc)
        raise HTTPException(status_code=409, detail="API_ENDPOINT_REBIND_REQUIRED") from exc
    return stored


# Per-solve context used to thread the user API key and usage accumulator from
# the HTTP entry points through ``stream_solve_events`` to ``_build_agent``
# without changing the public generator signature (existing tests patch it).
_solve_user_api_key: contextvars.ContextVar[StoredApiKey | None] = contextvars.ContextVar(
    "_solve_user_api_key", default=None
)
_solve_usage: contextvars.ContextVar[_UsageAccumulator | None] = contextvars.ContextVar(
    "_solve_usage", default=None
)


def _parse_model_string(model_string: str) -> tuple[str, str]:
    normalized = normalize_model_string(model_string)
    if "/" not in normalized:
        return normalized, ""
    provider, model = normalized.split("/", 1)
    return provider, model


# Cap for non-solve JSON bodies to keep cheap endpoints cheap and bounded.
MAX_API_REQUEST_BYTES = 2 * 1024 * 1024

_DEFAULT_PLATFORM_MODEL_ALLOWLIST = frozenset({
    "openai/gpt-5.6-sol",
    "openai/gpt-5.6",
    "shengsuanyun/openai/gpt-5.5",
    "shengsuanyun/openai/gpt-5.4-mini",
    "shengsuanyun/openai/gpt-5.4",
    "shengsuanyun/deepseek/deepseek-v4-pro",
    "deepseek/deepseek-v4-pro",
    "deepseek/deepseek-chat",
    "kimi/k3",
})


def _platform_model_allowlist() -> frozenset[str]:
    raw = os.getenv("CONJECTA_PLATFORM_MODEL_ALLOWLIST", "").strip()
    if raw:
        return frozenset(m.strip() for m in raw.split(",") if m.strip())
    return _DEFAULT_PLATFORM_MODEL_ALLOWLIST


def _resolve_platform_model(
    model: str | None,
    *,
    user_api_key: StoredApiKey | None = None,
    default_to_config: bool = True,
) -> str:
    """Resolve and validate the model string for a platform-paid endpoint.

    When the user has bound their own API key, the provider/model is dictated by
    that key. Otherwise the client may omit the model (and optionally use the
    configured default) or supply one from the platform allowlist.
    """
    if user_api_key is not None:
        if not user_api_key.base_url:
            raise HTTPException(status_code=409, detail="API_ENDPOINT_REBIND_REQUIRED")
        return f"openai/{USER_API_MODEL}"
    if model is None or not str(model).strip():
        if not default_to_config:
            raise HTTPException(status_code=400, detail="Model is required.")
        config = load_config()
        return default_model_string(config)
    normalized = normalize_model_string(str(model).strip())
    if normalized not in _platform_model_allowlist():
        raise HTTPException(status_code=400, detail="Invalid or unsupported model.")
    return normalized


_PLATFORM_PROVIDER_KEY_ENV: dict[str, str] = {
    "shengsuanyun": "SHENGSUANYUN_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
    "openai": "OPENAI_API_KEY",
    "kimi": "KIMI_API_KEY",
}


def _platform_api_key(model: str | None) -> str | None:
    """Resolve the server-side API key matching the platform model's provider."""
    raw = (model or "").strip()
    if raw:
        provider, _ = _parse_model_string(raw)
    else:
        provider = load_config().llm.provider
    env_name = _PLATFORM_PROVIDER_KEY_ENV.get(provider)
    return os.environ.get(env_name) if env_name else None


def _check_request_body_size(request: Request, max_bytes: int = MAX_API_REQUEST_BYTES) -> None:
    """Reject oversized JSON bodies before FastAPI materializes them."""
    headers = getattr(request, "headers", None)
    if headers is None:
        # Tests may call handlers with a mock request object; no headers means
        # no size to validate.
        return
    raw = str(headers.get("content-length", "")).strip()
    if raw:
        try:
            length = int(raw)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Invalid Content-Length.") from exc
        if length < 0:
            raise HTTPException(status_code=400, detail="Invalid Content-Length.")
        if length > max_bytes:
            raise HTTPException(status_code=413, detail="Request body is too large.")


async def _record_usage(
    user_api_key: StoredApiKey | None,
    usage: _UsageAccumulator | None,
    user_id: str,
) -> None:
    if usage is None or usage.total_tokens <= 0:
        return
    record = UsageRecord(
        prompt_tokens=usage.prompt_tokens,
        completion_tokens=usage.completion_tokens,
        total_tokens=usage.total_tokens,
        cost_usd=cost_for(
            usage.prompt_tokens,
            usage.completion_tokens,
            provider=usage.provider or None,
            model=usage.model or None,
        ),
        provider=usage.provider or "unknown",
        model=usage.model or "unknown",
    )
    source = "user_key" if user_api_key is not None else "platform"
    # User-key usage is recorded with source="user_key" for audit only.
    # increment_usage only counts source='platform', so this does not
    # affect the free quota.
    try:
        await asyncio.to_thread(UsageStore().record, user_id, record, source)
    except Exception as exc:
        web_log.error("Failed to record solve usage for %s: %s", user_id, exc)


async def _check_solve_quota(user_id: str) -> StoredApiKey | None:
    """Return the user's bound API key, or raise 429 if the free quota is exceeded.

    Set CONJECTA_DISABLE_QUOTA=1 in a gitignored local .env to skip free-tier
    limits during local development. Production must leave it unset.

    If usage tracking is unavailable, raise 503 so a downstream database
    outage does not silently allow unlimited usage.
    """
    try:
        user_api_key = await _load_user_api_key(user_id)
        if user_api_key is None:
            if quota_disabled() or is_quota_unlimited(user_id=user_id):
                return None
            store = UsageStore()
            today = await asyncio.to_thread(store.daily_usage, user_id)
            if not is_allowed(today.get("total_tokens", 0), user_id=user_id):
                raise HTTPException(status_code=429, detail="DAILY_QUOTA_EXCEEDED")
        return user_api_key
    except HTTPException:
        raise
    except Exception as exc:
        web_log.error("Failed to check solve quota for %s: %s", user_id, exc)
        raise HTTPException(status_code=503, detail="Usage tracking unavailable") from exc


def _quota_lock(user_id: str) -> asyncio.Lock:
    """Return a per-user lock used to close the quota-check TOCTOU window."""
    lock = _QUOTA_LOCKS.get(user_id)
    if lock is None:
        lock = asyncio.Lock()
        _QUOTA_LOCKS[user_id] = lock
    return lock


async def _begin_solve_quota(user_id: str) -> tuple[StoredApiKey | None, str | None]:
    """Check the free-tier quota and atomically reserve the remaining budget.

    Returns ``(user_api_key, reservation_id)``. ``reservation_id`` is None
    when the platform free-tier quota is not enforced for this user (own API
    key, quota disabled, or unlimited); otherwise it must be passed to
    ``_settle_solve_quota`` exactly once. The reservation holds the user's
    whole remaining budget, so a second concurrent solve for the same user
    is rejected instead of both passing the check before either records
    usage (the state backend makes that check-and-hold atomic, also across
    replicas when ``web.state_backend = "redis"``).
    """
    try:
        user_api_key = await _load_user_api_key(user_id)
        if user_api_key is not None:
            return user_api_key, None
        if quota_disabled() or is_quota_unlimited(user_id=user_id):
            return None, None
        store = UsageStore()
        today = await asyncio.to_thread(store.daily_usage, user_id)
        used = int(today.get("total_tokens", 0) or 0)
        if not is_allowed(used, user_id=user_id):
            raise HTTPException(status_code=429, detail="DAILY_QUOTA_EXCEEDED")
        limit = free_tokens_per_day()
        reservation_id = f"solve-{uuid4().hex}"
        allowed = await get_state_backend().quota.reserve(
            reservation_id,
            user_id,
            max(1, limit - used),
            consumed=used,
            limit=limit,
        )
        if not allowed:
            raise HTTPException(status_code=429, detail="DAILY_QUOTA_EXCEEDED")
        return None, reservation_id
    except HTTPException:
        raise
    except Exception as exc:
        web_log.error("Failed to check solve quota for %s: %s", user_id, exc)
        raise HTTPException(status_code=503, detail="Usage tracking unavailable") from exc


async def _settle_solve_quota(reservation_id: str | None, actual_tokens: int) -> None:
    """Settle a quota reservation with the real usage, or release it unused."""
    if not reservation_id:
        return
    quota = get_state_backend().quota
    try:
        if actual_tokens > 0:
            await quota.settle(reservation_id, actual_tokens)
        else:
            await quota.release(reservation_id)
    except Exception as exc:
        # A lost reservation is bounded by the backend TTL; never fail the solve.
        web_log.error("Failed to settle quota reservation %s: %s", reservation_id, exc)


async def _build_agent(
    model_string: str | None = None,
    api_key: str | None = None,
    critic_model_string: str | None = None,
    critic_api_key: str | None = None,
    project_context: ProjectContext | None = None,
    user_id: str | None = None,
    user_api_key: StoredApiKey | None = None,
    usage: _UsageAccumulator | None = None,
):
    from math_agent.agent.supervisor import SupervisorAgent
    config = load_config()
    if user_api_key is None:
        user_api_key = _solve_user_api_key.get()
    if usage is None:
        usage = _solve_usage.get()
    if user_api_key is not None:
        llm = create_backend_for_user(config.llm, user_api_key)
        critic_llm = create_backend_for_user(config.critic, user_api_key)
    elif model_string:
        llm = create_backend_from_model_string(
            model_string,
            temperature=config.llm.temperature,
            api_key=api_key,
            timeout_seconds=config.llm.timeout_seconds,
        )
        if critic_model_string:
            critic_llm = create_backend_from_model_string(
                critic_model_string,
                temperature=config.critic.temperature,
                api_key=critic_api_key,
                timeout_seconds=config.critic.timeout_seconds,
            )
        elif critic_api_key:
            critic_llm = create_backend(config.critic, api_key=critic_api_key)
        else:
            critic_llm = create_backend(config.critic)
    else:
        llm = create_backend(config.llm, api_key=api_key)
        if critic_model_string:
            critic_llm = create_backend_from_model_string(
                critic_model_string,
                temperature=config.critic.temperature,
                api_key=critic_api_key,
                timeout_seconds=config.critic.timeout_seconds,
            )
        elif critic_api_key:
            critic_llm = create_backend(config.critic, api_key=critic_api_key)
        else:
            critic_llm = create_backend(config.critic)

    if usage is not None:
        if user_api_key is not None:
            usage.provider = USER_API_PROVIDER
            usage.model = USER_API_MODEL
        elif model_string:
            usage.provider, usage.model = _parse_model_string(model_string)
        else:
            usage.provider = config.llm.provider
            usage.model = config.llm.model
        llm = _UsageTrackingBackend(llm, usage)
        critic_llm = _UsageTrackingBackend(critic_llm, usage)
    lean_runner = LeanRunner(config.lean) if config.lean.enabled else None
    premise_retriever = (
        await _get_shared_premise_retriever(config, lean_runner)
        if lean_runner is not None
        else None
    )
    lean_codegen = (
        LeanCodegen(
            llm=llm,
            runner=lean_runner,
            config=config.lean,
            premise_retriever=premise_retriever,
        )
        if lean_runner is not None
        else None
    )

    plan_memory = None
    project_store = None
    try:
        project_store = _project_store(user_id)
    except Exception:
        pass
    # Cloud when configured; local JSONL otherwise. Do not catch
    # KnowledgeStoreUnavailable — a configured-but-broken cloud must not
    # silently fork writes into the local store.
    knowledge_store = _cloud_knowledge_store(user_id, config.knowledge) or project_store
    if config.agent.memory_consolidation_enabled:
        plan_root = _tenant_runtime_root(
            "CONJECTA_PLAN_MEMORY_DIR", "logs/plan_memory", user_id
        )
        plan_memory = PlanMemory(path=plan_root / "plan_memory.jsonl")

    material_store = _material_store(user_id)
    user_memory_store = _user_memory_store(user_id)
    graph_root = _tenant_runtime_root(
        "CONJECTA_KNOWLEDGE_GRAPH_DIR", "data/knowledge_graphs", user_id
    )
    knowledge_graph = KnowledgeGraph(root=graph_root)
    prover_llm = (
        create_backend_for_user(config.prover, user_api_key)
        if user_api_key is not None and config.prover.model.strip()
        else create_prover_backend(config.prover)
    )

    tool_registry = ToolRegistry(
        enabled_tools=config.agent.tools,
        lean_runner=lean_runner,
        lean_codegen=lean_codegen,
        premise_retriever=premise_retriever,
        llm=llm,
        material_store=material_store,
        knowledge_store=knowledge_store,
        knowledge_graph=knowledge_graph,
        knowledge_config=config.knowledge,
        mcp_client=_shared_mcp_client,
        agent_config=config.agent,
        search_config=config.search,
        prover_llm=prover_llm,
        critic_llm=critic_llm,
    )

    return SupervisorAgent(
        llm=llm,
        critic_llm=critic_llm,
        config=config.agent,
        lean_runner=lean_runner,
        lean_codegen=lean_codegen,
        knowledge_store=knowledge_store,
        plan_memory=plan_memory,
        tool_registry=tool_registry,
        project_context=project_context,
        project_store=project_store,
        user_memory_store=user_memory_store,
        verifier_config=config.verifier,
    )
