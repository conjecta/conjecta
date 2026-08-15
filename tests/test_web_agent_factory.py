import asyncio
from types import SimpleNamespace
from unittest.mock import Mock, call
from pathlib import Path

import pytest

from math_agent.agent import supervisor
from math_agent.agent.knowledge_evaluator import KnowledgeEvaluator
from math_agent.agent.plan_memory import DEFAULT_SEED_MEMORY_PATH
from math_agent.billing.models import StoredApiKey
from math_agent.config import Config, CriticConfig, LLMConfig
from math_agent.net_safety import UnsafeFetchURL
from math_agent.web import agent_factory as web_app
from math_agent.web.project_store import ProjectStore


def _factory_config() -> Config:
    config = Config(
        llm=LLMConfig(
            provider="shengsuanyun",
            model="openai/configured-main",
            temperature=0.7,
        ),
        critic=CriticConfig(
            provider="deepseek",
            model="configured-critic",
            temperature=0.15,
        ),
    )
    config.lean.enabled = False
    config.agent.memory_consolidation_enabled = False
    return config


def _stub_agent_dependencies(monkeypatch, config: Config):
    captured = {}

    def build_supervisor(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(**kwargs)

    monkeypatch.setattr(web_app, "load_config", lambda: config)
    monkeypatch.setattr(web_app, "_project_store", lambda _user_id=None: None)
    monkeypatch.setattr(web_app, "_cloud_knowledge_store", lambda _user_id=None, _knowledge_config=None: None)
    monkeypatch.setattr(web_app, "_material_store", lambda _user_id=None: object())
    monkeypatch.setattr(web_app, "KnowledgeGraph", lambda *_args, **_kwargs: object())

    def build_registry(**kwargs):
        captured["tool_registry_kwargs"] = kwargs
        return object()

    monkeypatch.setattr(web_app, "ToolRegistry", build_registry)
    monkeypatch.setattr(supervisor, "SupervisorAgent", build_supervisor)
    return captured


def _stub_stored_user_key(monkeypatch, stored: StoredApiKey):
    from math_agent.knowledge import supabase_client

    fake_query = SimpleNamespace(
        select=lambda *_args: fake_query,
        eq=lambda *_args: fake_query,
        limit=lambda *_args: fake_query,
        execute=lambda: SimpleNamespace(data=[{"api_keys_encrypted": "ciphertext"}]),
    )
    fake_client = SimpleNamespace(table=lambda *_args: fake_query)
    monkeypatch.setattr(
        supabase_client.SupabaseConfig,
        "from_env",
        classmethod(lambda cls, **_kwargs: object()),
    )
    monkeypatch.setattr(
        supabase_client, "create_supabase_client", lambda **_kwargs: fake_client
    )
    monkeypatch.setattr(web_app, "decrypt_api_key", lambda _ciphertext: stored)


@pytest.mark.asyncio
async def test_main_model_override_preserves_configured_critic(monkeypatch):
    config = _factory_config()
    captured = _stub_agent_dependencies(monkeypatch, config)
    main_backend = object()
    critic_backend = object()
    from_model_string = Mock(return_value=main_backend)
    from_config = Mock(return_value=critic_backend)
    monkeypatch.setattr(web_app, "create_backend_from_model_string", from_model_string)
    monkeypatch.setattr(web_app, "create_backend", from_config)

    agent = await web_app._build_agent(
        model_string="openai/ui-main",
        api_key="main-provider-key",
        user_id="user-1",
    )

    from_model_string.assert_called_once_with(
        "openai/ui-main",
        temperature=config.llm.temperature,
        api_key="main-provider-key",
        timeout_seconds=config.llm.timeout_seconds,
    )
    from_config.assert_called_once_with(config.critic)
    assert agent.llm is main_backend
    assert agent.critic_llm is critic_backend
    assert captured["critic_llm"] is critic_backend


@pytest.mark.asyncio
async def test_user_endpoint_is_used_for_main_critic_and_enabled_prover(monkeypatch):
    config = _factory_config()
    config.prover.model = "server-prover"
    captured = _stub_agent_dependencies(monkeypatch, config)
    main_backend = object()
    critic_backend = object()
    prover_backend = object()
    user_endpoint = StoredApiKey(
        api_key="sk-user", base_url="https://api.example.com/v1"
    )
    for_user = Mock(
        side_effect=[main_backend, critic_backend, prover_backend]
    )
    from_config = Mock()
    monkeypatch.setattr(web_app, "create_backend_for_user", for_user)
    monkeypatch.setattr(web_app, "create_prover_backend", from_config)

    agent = await web_app._build_agent(
        user_id="user-1", user_api_key=user_endpoint
    )

    assert for_user.call_args_list == [
        call(config.llm, user_endpoint),
        call(config.critic, user_endpoint),
        call(config.prover, user_endpoint),
    ]
    from_config.assert_not_called()
    assert agent.llm is main_backend
    assert agent.critic_llm is critic_backend
    assert captured["tool_registry_kwargs"]["prover_llm"] is prover_backend


@pytest.mark.asyncio
async def test_load_user_api_key_revalidates_stored_base_url(monkeypatch):
    stored = StoredApiKey(
        api_key="sk-user", base_url="https://api.example.com/v1"
    )
    _stub_stored_user_key(monkeypatch, stored)
    validated = []

    async def validate(base_url: str) -> str:
        validated.append(base_url)
        return base_url

    monkeypatch.setattr(web_app, "validate_public_https_url", validate)

    assert await web_app._load_user_api_key("user-1") is stored
    assert validated == ["https://api.example.com/v1"]


@pytest.mark.asyncio
async def test_load_user_api_key_requires_rebind_for_unsafe_stored_url(monkeypatch):
    _stub_stored_user_key(
        monkeypatch,
        StoredApiKey(api_key="sk-user", base_url="https://127.0.0.1/v1"),
    )

    async def reject(_base_url: str) -> str:
        raise UnsafeFetchURL("private or reserved")

    monkeypatch.setattr(web_app, "validate_public_https_url", reject)

    with pytest.raises(web_app.HTTPException) as error:
        await web_app._load_user_api_key("user-1")
    assert error.value.status_code == 409
    assert error.value.detail == "API_ENDPOINT_REBIND_REQUIRED"


@pytest.mark.asyncio
async def test_load_user_api_key_requires_rebind_for_legacy_record(monkeypatch):
    _stub_stored_user_key(
        monkeypatch,
        StoredApiKey(api_key="sk-old", legacy_provider="openai"),
    )

    with pytest.raises(web_app.HTTPException) as error:
        await web_app._load_user_api_key("user-1")
    assert error.value.status_code == 409
    assert error.value.detail == "API_ENDPOINT_REBIND_REQUIRED"


@pytest.mark.asyncio
async def test_supabase_is_the_authoritative_knowledge_store(monkeypatch):
    config = _factory_config()
    captured = _stub_agent_dependencies(monkeypatch, config)
    local_store = object()
    cloud_store = object()
    monkeypatch.setattr(web_app, "_project_store", lambda _user_id=None: local_store)
    monkeypatch.setattr(web_app, "_cloud_knowledge_store", lambda _user_id=None, _knowledge_config=None: cloud_store)
    monkeypatch.setattr(
        web_app, "create_backend", lambda _config, api_key=None: object()
    )

    await web_app._build_agent(user_id="u-one")

    assert captured["knowledge_store"] is cloud_store
    assert captured["project_store"] is local_store
    assert captured["tool_registry_kwargs"]["knowledge_store"] is cloud_store


@pytest.mark.asyncio
async def test_local_store_is_single_knowledge_fallback(monkeypatch):
    config = _factory_config()
    captured = _stub_agent_dependencies(monkeypatch, config)
    local_store = object()
    monkeypatch.setattr(web_app, "_project_store", lambda _user_id=None: local_store)
    monkeypatch.setattr(web_app, "_cloud_knowledge_store", lambda _user_id=None, _knowledge_config=None: None)
    monkeypatch.setattr(web_app, "create_backend", lambda _config, api_key=None: object())

    await web_app._build_agent(user_id="u-one")

    assert captured["knowledge_store"] is local_store
    assert captured["project_store"] is local_store
    assert captured["tool_registry_kwargs"]["knowledge_store"] is local_store


def test_knowledge_evaluator_never_dual_writes():
    cloud = Mock()
    local = Mock()
    evaluator = KnowledgeEvaluator(llm=object(), knowledge_store=cloud, project_store=local)

    evaluator._update_item("project", "item", "fact", {"score": 0.5})
    evaluator._set_score("project", "item", "fact", 0.5)
    evaluator._delete_item("project", "item", "fact")

    cloud.update_item.assert_called_once()
    cloud.set_score.assert_called_once()
    cloud.delete_item.assert_called_once()
    local.update_knowledge_item.assert_not_called()
    local.delete_knowledge_item.assert_not_called()


def test_knowledge_evaluator_proposals_remain_candidates():
    store = Mock()
    evaluator = KnowledgeEvaluator(llm=object(), knowledge_store=store)

    evaluator._add_item(
        "project",
        "fact",
        {"statement": "Proposed fact", "why": "Needs review"},
    )

    fact_rows = store.add_many.call_args.args[1]
    assert fact_rows[0]["status"] == "candidate"
    store.add_fact.assert_not_called()


def test_knowledge_evaluator_mutates_local_authoritative_fallback(tmp_path):
    store = ProjectStore(tmp_path)
    fact = store.add_fact("project", "Original", why="old")
    evaluator = KnowledgeEvaluator(
        llm=object(), knowledge_store=store, project_store=store
    )

    evaluator._update_item("project", fact["id"], "fact", {"why": "new"})
    evaluator._set_score("project", fact["id"], "fact", 0.75)

    updated = store.list_facts("project")[0]
    assert updated["why"] == "new"
    assert updated["score"] == 0.75

    evaluator._delete_item("project", fact["id"], "fact")
    assert store.list_facts("project") == []


def test_knowledge_evaluator_verified_revision_is_fully_immutable(tmp_path):
    store = ProjectStore(tmp_path)
    fact = store.add_many(
        "project",
        [{
            "statement": "Original",
            "why": "old",
            "status": "verified",
            "formal_status": "lean_verified",
            "lean_name": "Trusted.name",
            "source": "lean_check",
            "source_type": "lean_verified",
            "source_ref": "session:1",
            "source_title": "Verified session",
            "evidence": "theorem trusted : True := by trivial",
            "confidence": "1.0",
            "created_by": "lean_promotion",
            "review_note": "human approved",
        }],
        [],
        [],
    )["facts"][0]
    evaluator = KnowledgeEvaluator(llm=object(), knowledge_store=store)
    trust_patch = {
        "status": "candidate",
        "formal_status": "informal",
        "lean_name": "Forged.name",
        "source": "forged",
        "source_type": "manual",
        "source_ref": "forged",
        "source_title": "forged",
        "evidence": "forged",
        "confidence": "0.0",
        "created_by": "knowledge_evaluator",
        "review_note": "overwrite human note",
    }

    counts = evaluator._apply_ops(
        "project",
        {"revisions": [{"id": fact["id"], "kind": "fact", "fields": {
            "why": "semantic revision",
            **trust_patch,
        }}]},
        [fact],
        [],
        [],
    )

    updated = store.list_facts("project")[0]
    assert counts["revised"] == 0
    assert updated["why"] == "old"
    for field in trust_patch:
        assert updated[field] == fact[field]

    trust_only = evaluator._apply_ops(
        "project",
        {"revisions": [{"id": fact["id"], "kind": "fact", "fields": trust_patch}]},
        [updated],
        [],
        [],
    )
    assert trust_only["revised"] == 0


@pytest.mark.asyncio
async def test_tenant_plan_and_graph_roots_use_sanitized_user_id(monkeypatch, tmp_path):
    config = _factory_config()
    config.agent.memory_consolidation_enabled = True
    _stub_agent_dependencies(monkeypatch, config)
    captured = {}

    class CapturingPlanMemory:
        def __init__(self, *args, **kwargs):
            captured["plan_args"] = args
            captured["plan_kwargs"] = kwargs

    class CapturingKnowledgeGraph:
        def __init__(self, *args, **kwargs):
            captured["graph_args"] = args
            captured["graph_kwargs"] = kwargs

    monkeypatch.setenv("CONJECTA_PLAN_MEMORY_DIR", str(tmp_path / "plans"))
    monkeypatch.setenv("CONJECTA_KNOWLEDGE_GRAPH_DIR", str(tmp_path / "graphs"))
    monkeypatch.setattr(web_app, "PlanMemory", CapturingPlanMemory)
    monkeypatch.setattr(web_app, "KnowledgeGraph", CapturingKnowledgeGraph)
    monkeypatch.setattr(web_app, "_project_store", lambda _user_id=None: object())
    monkeypatch.setattr(web_app, "create_backend", lambda _config, api_key=None: object())

    await web_app._build_agent(user_id="../User A")

    plan_path = Path(captured["plan_kwargs"]["path"])
    graph_root = Path(captured["graph_kwargs"]["root"])
    assert plan_path != Path(DEFAULT_SEED_MEMORY_PATH)
    assert plan_path.parent.name == graph_root.name
    assert plan_path.parent.name not in {"", ".", ".."}
    assert "/" not in plan_path.parent.name
    assert " " not in plan_path.parent.name


def test_web_factory_reuses_per_user_project_store(monkeypatch, tmp_path):
    monkeypatch.setenv("CONJECTA_PROJECT_STORE_DIR", str(tmp_path))
    web_app._PROJECT_STORE_CACHE.clear()

    first = web_app._project_store("u-cache")
    second = web_app._project_store("u-cache")
    other = web_app._project_store("u-other")

    assert first is second
    assert other is not first


@pytest.mark.asyncio
async def test_dedicated_critic_override_uses_its_own_credentials(monkeypatch):
    config = _factory_config()
    _stub_agent_dependencies(monkeypatch, config)
    main_backend = object()
    critic_backend = object()
    from_model_string = Mock(side_effect=[main_backend, critic_backend])
    from_config = Mock()
    monkeypatch.setattr(web_app, "create_backend_from_model_string", from_model_string)
    monkeypatch.setattr(web_app, "create_backend", from_config)

    agent = await web_app._build_agent(
        model_string="openai/ui-main",
        api_key="main-provider-key",
        critic_model_string="deepseek/ui-critic",
        critic_api_key="critic-provider-key",
    )

    assert from_model_string.call_args_list == [
        call(
            "openai/ui-main",
            temperature=config.llm.temperature,
            api_key="main-provider-key",
            timeout_seconds=config.llm.timeout_seconds,
        ),
        call(
            "deepseek/ui-critic",
            temperature=config.critic.temperature,
            api_key="critic-provider-key",
            timeout_seconds=config.critic.timeout_seconds,
        ),
    ]
    from_config.assert_not_called()
    assert agent.llm is main_backend
    assert agent.critic_llm is critic_backend


@pytest.mark.asyncio
async def test_shared_premise_retriever_initializes_once(monkeypatch):
    config = _factory_config()
    config.lean.enabled = True
    config.lean.mathlib_dep = False
    config.lean.premise_index_enabled = True
    builds = []

    class FakeRunner:
        async def ensure_dependencies(self):
            return None

    class FakeRetriever:
        def build_index(self):
            builds.append("built")

    monkeypatch.setattr(web_app, "PremiseRetriever", FakeRetriever)
    monkeypatch.setattr(web_app, "_shared_premise_retriever", None)
    monkeypatch.setattr(web_app, "_shared_premise_retriever_task", None)

    first, second = await asyncio.gather(
        web_app._get_shared_premise_retriever(config, FakeRunner()),
        web_app._get_shared_premise_retriever(config, FakeRunner()),
    )

    assert first is second
    assert builds == ["built"]


@pytest.mark.asyncio
async def test_web_factory_injects_retriever_into_shared_codegen(monkeypatch):
    config = _factory_config()
    config.lean.enabled = True
    captured = _stub_agent_dependencies(monkeypatch, config)
    retriever = object()
    runner = object()

    async def shared_retriever(_config, _runner):
        assert _runner is runner
        return retriever

    monkeypatch.setattr(web_app, "LeanRunner", lambda _config: runner)
    monkeypatch.setattr(
        web_app, "_get_shared_premise_retriever", shared_retriever
    )
    monkeypatch.setattr(web_app, "create_backend", lambda _config, api_key=None: object())

    agent = await web_app._build_agent(user_id="u-premise")

    assert agent.lean_codegen.premise_retriever is retriever
    assert captured["tool_registry_kwargs"]["lean_codegen"] is agent.lean_codegen
    assert captured["tool_registry_kwargs"]["premise_retriever"] is retriever
