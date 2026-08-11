from __future__ import annotations

import sys
from types import ModuleType

import pytest

from math_agent.knowledge.supabase import KnowledgeStore
from math_agent.knowledge.supabase_client import (
    clear_supabase_client_cache,
    create_supabase_client,
    is_transient_supabase_error,
    run_supabase,
)


def _configure_anon_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "anon-key")
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
    for name in (
        "VITE_SUPABASE_URL",
        "NEXT_PUBLIC_SUPABASE_URL",
        "VITE_SUPABASE_ANON_KEY",
        "NEXT_PUBLIC_SUPABASE_ANON_KEY",
    ):
        monkeypatch.delenv(name, raising=False)


def _install_fake_supabase(monkeypatch: pytest.MonkeyPatch) -> None:
    module = ModuleType("supabase")
    module.create_client = lambda url, key: {  # type: ignore[attr-defined]
        "url": url,
        "key": key,
    }
    monkeypatch.setitem(sys.modules, "supabase", module)


def test_build_httpx_client_disables_http2():
    from math_agent.knowledge.supabase_client import _build_httpx_client

    client = _build_httpx_client()
    try:
        # httpx stores the flag on the transport; constructing with http2=False
        # must not enable h2.
        assert getattr(client, "_transport", None) is not None
        assert "HTTP2" not in type(client._transport).__name__.upper().replace(" ", "")
    finally:
        client.close()


def test_is_transient_supabase_error_detects_httpx_disconnect():
    class RemoteProtocolError(Exception):
        pass

    RemoteProtocolError.__module__ = "httpx"
    assert is_transient_supabase_error(RemoteProtocolError("Server disconnected"))
    assert not is_transient_supabase_error(ValueError("bad row"))


def test_run_supabase_retries_and_clears_client_cache(monkeypatch):
    clears: list[int] = []
    monkeypatch.setattr(
        "math_agent.knowledge.supabase_client.clear_supabase_client_cache",
        lambda: clears.append(1),
    )
    monkeypatch.setattr("math_agent.knowledge.supabase_client.time.sleep", lambda _s: None)

    class RemoteProtocolError(Exception):
        pass

    RemoteProtocolError.__module__ = "httpx"
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise RemoteProtocolError("Server disconnected")
        return "ok"

    assert run_supabase(flaky, attempts=3, base_delay_seconds=0.01) == "ok"
    assert calls["n"] == 3
    assert len(clears) == 2


def test_run_supabase_raises_after_exhausted_retries(monkeypatch):
    monkeypatch.setattr(
        "math_agent.knowledge.supabase_client.clear_supabase_client_cache",
        clear_supabase_client_cache,
    )
    monkeypatch.setattr("math_agent.knowledge.supabase_client.time.sleep", lambda _s: None)

    class RemoteProtocolError(Exception):
        pass

    RemoteProtocolError.__module__ = "httpx"

    with pytest.raises(RemoteProtocolError):
        run_supabase(
            lambda: (_ for _ in ()).throw(RemoteProtocolError("Server disconnected")),
            attempts=2,
            base_delay_seconds=0.01,
        )


def test_add_member_maps_exhausted_transport_to_503():
    from fastapi import HTTPException

    from math_agent.web.project_access import ProjectAccess, ProjectAccessService

    class BoomUsers:
        def get_profile(self, _uid):
            exc_type = type("RemoteProtocolError", (Exception,), {})
            exc_type.__module__ = "httpx"
            raise exc_type("Server disconnected")

    svc = ProjectAccessService.__new__(ProjectAccessService)
    svc.users = BoomUsers()
    access = ProjectAccess(
        actor_user_id="u_lead",
        owner_user_id="u_lead",
        project_id="research-1",
        role="lead",
    )
    with pytest.raises(HTTPException) as excinfo:
        svc.add_member(access, "u_collab")
    assert excinfo.value.status_code == 503
    assert "稍后重试" in str(excinfo.value.detail)


def test_client_helper_rejects_anon_when_service_role_is_required(monkeypatch):
    _configure_anon_only(monkeypatch)
    _install_fake_supabase(monkeypatch)

    with pytest.raises(RuntimeError, match="SUPABASE_SERVICE_ROLE_KEY"):
        create_supabase_client(
            prefer_service_role=True,
            require_service_role=True,
        )


def test_implicit_knowledge_store_rejects_anon_only_configuration(monkeypatch):
    _configure_anon_only(monkeypatch)
    _install_fake_supabase(monkeypatch)

    with pytest.raises(RuntimeError, match="SUPABASE_SERVICE_ROLE_KEY"):
        KnowledgeStore(user_id="tenant-1")


def test_anon_only_cloud_store_failure_preserves_local_app_fallback(monkeypatch):
    """Anon-only env means local mode: cloud is not the configured backend."""
    _configure_anon_only(monkeypatch)
    _install_fake_supabase(monkeypatch)

    from math_agent.web import agent_factory as web_app

    local_store = object()
    monkeypatch.setattr(web_app, "_project_store", lambda user_id=None: local_store)

    assert web_app._cloud_knowledge_store("tenant-1") is None
    assert web_app._maybe_knowledge_store("tenant-1") is local_store


def test_configured_supabase_does_not_silently_fall_back_to_jsonl(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "service-role-key")
    monkeypatch.delenv("SUPABASE_ANON_KEY", raising=False)

    from fastapi import HTTPException

    from math_agent.web import agent_factory as web_app

    local_store = object()
    monkeypatch.setattr(web_app, "_project_store", lambda user_id=None: local_store)

    class _BrokenStore:
        def __init__(self, *args, **kwargs):
            raise RuntimeError("network down")

    monkeypatch.setattr("math_agent.knowledge.supabase.KnowledgeStore", _BrokenStore)

    with pytest.raises(web_app.KnowledgeStoreUnavailable):
        web_app._cloud_knowledge_store("tenant-1")

    with pytest.raises(HTTPException) as exc_info:
        web_app._maybe_knowledge_store("tenant-1")
    assert exc_info.value.status_code == 503
