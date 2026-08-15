from fastapi.testclient import TestClient

from math_agent.billing.models import LLMResponse, StoredApiKey
from math_agent.web.app import _solve_usage, app

client = TestClient(app)


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
        self.recorded.append({
            "user_id": user_id,
            "usage": usage,
            "source": source,
        })


async def _fake_load_no_user_api_key(_uid):
    return None


async def _fake_load_user_api_key(_uid):
    return StoredApiKey(
        api_key="sk-test", base_url="https://api.example.com/v1"
    )


async def _fake_stream_solve_events(_msg, *, user_id=None):
    usage = _solve_usage.get()
    if usage is not None:
        usage.add(
            LLMResponse(
                text="answer",
                prompt_tokens=10,
                completion_tokens=5,
                total_tokens=15,
            )
        )
        usage.provider = "shengsuanyun"
        usage.model = "openai/gpt-5.5"
    yield {"type": "done", "final_answer": "2"}


def _enable_quota_tracking(monkeypatch):
    """Turn off the local/test quota bypass so free-tier limits are enforced."""
    monkeypatch.delenv("CONJECTA_DISABLE_QUOTA", raising=False)


def test_solve_blocked_when_quota_exceeded(monkeypatch):
    monkeypatch.delenv("CONJECTA_JWT_SECRET", raising=False)
    monkeypatch.setenv("CONJECTA_ALLOW_UNAUTHENTICATED", "1")
    _enable_quota_tracking(monkeypatch)

    fake_store = _FakeUsageStore(daily_total=500_000)
    monkeypatch.setattr("math_agent.web.agent_factory.UsageStore", lambda: fake_store)
    monkeypatch.setattr(
        "math_agent.web.agent_factory._load_user_api_key", _fake_load_no_user_api_key
    )

    resp = client.post("/api/solve/stream", json={"problem": "1+1"})
    assert resp.status_code == 429
    assert "DAILY_QUOTA_EXCEEDED" in resp.text
    assert fake_store.recorded == []


def test_solve_local_disable_quota_skips_limit(monkeypatch):
    monkeypatch.delenv("CONJECTA_JWT_SECRET", raising=False)
    monkeypatch.setenv("CONJECTA_ALLOW_UNAUTHENTICATED", "1")
    monkeypatch.setenv("CONJECTA_DISABLE_QUOTA", "1")

    fake_store = _FakeUsageStore(daily_total=500_000)
    monkeypatch.setattr("math_agent.web.agent_factory.UsageStore", lambda: fake_store)
    monkeypatch.setattr(
        "math_agent.web.agent_factory._load_user_api_key", _fake_load_no_user_api_key
    )
    monkeypatch.setattr(
        "math_agent.web.solve_routes.stream_solve_events", _fake_stream_solve_events
    )

    resp = client.post("/api/solve/stream", json={"problem": "1+1"})
    assert resp.status_code == 200
    assert fake_store.recorded  # usage still recorded; limit not enforced


def test_solve_records_platform_usage(monkeypatch):
    monkeypatch.delenv("CONJECTA_JWT_SECRET", raising=False)
    monkeypatch.setenv("CONJECTA_ALLOW_UNAUTHENTICATED", "1")
    _enable_quota_tracking(monkeypatch)

    fake_store = _FakeUsageStore(daily_total=0)
    monkeypatch.setattr("math_agent.web.agent_factory.UsageStore", lambda: fake_store)
    monkeypatch.setattr(
        "math_agent.web.agent_factory._load_user_api_key", _fake_load_no_user_api_key
    )
    monkeypatch.setattr(
        "math_agent.web.solve_routes.stream_solve_events", _fake_stream_solve_events
    )

    resp = client.post("/api/solve/stream", json={"problem": "1+1"})
    assert resp.status_code == 200

    assert len(fake_store.recorded) == 1
    recorded = fake_store.recorded[0]
    assert recorded["source"] == "platform"
    assert recorded["usage"].prompt_tokens == 10
    assert recorded["usage"].completion_tokens == 5
    assert recorded["usage"].total_tokens == 15
    assert recorded["usage"].provider == "shengsuanyun"
    assert recorded["usage"].model == "openai/gpt-5.5"
    assert recorded["usage"].cost_usd > 0


def test_solve_records_user_key_usage(monkeypatch):
    monkeypatch.delenv("CONJECTA_JWT_SECRET", raising=False)
    monkeypatch.setenv("CONJECTA_ALLOW_UNAUTHENTICATED", "1")

    fake_store = _FakeUsageStore(daily_total=0)
    monkeypatch.setattr("math_agent.web.agent_factory.UsageStore", lambda: fake_store)
    monkeypatch.setattr(
        "math_agent.web.agent_factory._load_user_api_key", _fake_load_user_api_key
    )
    monkeypatch.setattr(
        "math_agent.web.solve_routes.stream_solve_events", _fake_stream_solve_events
    )

    resp = client.post("/api/solve/stream", json={"problem": "1+1"})
    assert resp.status_code == 200

    assert len(fake_store.recorded) == 1
    recorded = fake_store.recorded[0]
    assert recorded["source"] == "user_key"


def test_solve_user_key_skips_quota_check(monkeypatch):
    monkeypatch.delenv("CONJECTA_JWT_SECRET", raising=False)
    monkeypatch.setenv("CONJECTA_ALLOW_UNAUTHENTICATED", "1")

    fake_store = _FakeUsageStore(daily_total=1_000_000)
    monkeypatch.setattr("math_agent.web.agent_factory.UsageStore", lambda: fake_store)
    monkeypatch.setattr(
        "math_agent.web.agent_factory._load_user_api_key", _fake_load_user_api_key
    )
    monkeypatch.setattr(
        "math_agent.web.solve_routes.stream_solve_events", _fake_stream_solve_events
    )

    resp = client.post("/api/solve/stream", json={"problem": "1+1"})
    assert resp.status_code == 200


def test_solve_user_key_does_not_query_daily_usage(monkeypatch):
    monkeypatch.delenv("CONJECTA_JWT_SECRET", raising=False)
    monkeypatch.setenv("CONJECTA_ALLOW_UNAUTHENTICATED", "1")

    daily_calls = []

    class _FakeUsageStoreNoDaily(_FakeUsageStore):
        def daily_usage(self, user_id, target_date=None):
            daily_calls.append({"user_id": user_id, "target_date": target_date})
            return super().daily_usage(user_id, target_date)

    fake_store = _FakeUsageStoreNoDaily(daily_total=0)
    monkeypatch.setattr("math_agent.web.agent_factory.UsageStore", lambda: fake_store)
    monkeypatch.setattr(
        "math_agent.web.agent_factory._load_user_api_key", _fake_load_user_api_key
    )
    monkeypatch.setattr(
        "math_agent.web.solve_routes.stream_solve_events", _fake_stream_solve_events
    )

    resp = client.post("/api/solve/stream", json={"problem": "1+1"})
    assert resp.status_code == 200
    assert daily_calls == []


class _FakeProjectStore:
    def __init__(self, checkpoint):
        self._checkpoint = checkpoint
        self._claimed = False

    def get_checkpoint(self, _session_id):
        return self._checkpoint

    def claim_human_decision(self, _session_id, _decision):
        if self._claimed or self._checkpoint is None:
            return None
        self._claimed = True
        return self._checkpoint


def _make_pending_checkpoint():
    return {
        "session_id": "session-1",
        "project_id": "default",
        "strategy": "react",
        "pending_interaction": {
            "request_id": "hitl-1",
            "allowed_decisions": ["approve", "reject", "edit", "respond"],
        },
    }


def test_solve_unlimited_quota_phone_skips_limit(monkeypatch):
    from math_agent.billing.quota import clear_unlimited_quota_cache
    from math_agent.web.jwt_auth import issue_access_token

    monkeypatch.setenv(
        "CONJECTA_JWT_SECRET", "test-jwt-secret-must-be-at-least-32-bytes"
    )
    monkeypatch.delenv("CONJECTA_ALLOW_UNAUTHENTICATED", raising=False)
    _enable_quota_tracking(monkeypatch)
    monkeypatch.setenv("CONJECTA_UNLIMITED_QUOTA_PHONES", "15721590518")
    clear_unlimited_quota_cache()

    fake_store = _FakeUsageStore(daily_total=500_000)
    monkeypatch.setattr("math_agent.web.agent_factory.UsageStore", lambda: fake_store)
    monkeypatch.setattr(
        "math_agent.web.agent_factory._load_user_api_key", _fake_load_no_user_api_key
    )
    monkeypatch.setattr(
        "math_agent.web.solve_routes.stream_solve_events", _fake_stream_solve_events
    )

    token, _user, _ttl = issue_access_token("15721590518")
    resp = client.post(
        "/api/solve/stream",
        json={"problem": "1+1"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert fake_store.recorded
    clear_unlimited_quota_cache()


def test_resume_decision_stream_blocked_when_quota_exceeded(monkeypatch):
    monkeypatch.delenv("CONJECTA_JWT_SECRET", raising=False)
    monkeypatch.setenv("CONJECTA_ALLOW_UNAUTHENTICATED", "1")
    _enable_quota_tracking(monkeypatch)

    fake_store = _FakeUsageStore(daily_total=500_000)
    monkeypatch.setattr("math_agent.web.agent_factory.UsageStore", lambda: fake_store)
    monkeypatch.setattr(
        "math_agent.web.agent_factory._load_user_api_key", _fake_load_no_user_api_key
    )
    monkeypatch.setattr(
        "math_agent.web.solve_routes._project_store",
        lambda _user_id: _FakeProjectStore(_make_pending_checkpoint()),
    )

    resp = client.post(
        "/api/solve/session-1/decisions/stream",
        json={"request_id": "hitl-1", "decision": "approve"},
    )
    assert resp.status_code == 429
    assert "DAILY_QUOTA_EXCEEDED" in resp.text
    assert fake_store.recorded == []


def test_resume_decision_stream_user_key_bypasses_quota(monkeypatch):
    monkeypatch.delenv("CONJECTA_JWT_SECRET", raising=False)
    monkeypatch.setenv("CONJECTA_ALLOW_UNAUTHENTICATED", "1")

    fake_store = _FakeUsageStore(daily_total=1_000_000)
    monkeypatch.setattr("math_agent.web.agent_factory.UsageStore", lambda: fake_store)
    monkeypatch.setattr(
        "math_agent.web.agent_factory._load_user_api_key", _fake_load_user_api_key
    )
    monkeypatch.setattr(
        "math_agent.web.solve_routes._project_store",
        lambda _user_id: _FakeProjectStore(_make_pending_checkpoint()),
    )
    monkeypatch.setattr(
        "math_agent.web.solve_routes.stream_solve_events", _fake_stream_solve_events
    )

    resp = client.post(
        "/api/solve/session-1/decisions/stream",
        json={"request_id": "hitl-1", "decision": "approve"},
    )
    assert resp.status_code == 200

    assert len(fake_store.recorded) == 1
    recorded = fake_store.recorded[0]
    assert recorded["source"] == "user_key"
