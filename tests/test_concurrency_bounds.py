"""Regression tests for the process-wide resource bounds added for concurrency.

Each of these guards a resource that previously grew without limit in a
long-running server, or a cap that keeps overload from degrading every
in-flight solve instead of shedding the excess.
"""
from __future__ import annotations

import logging
import os

import pytest

from math_agent.config import clear_config_cache, load_config
from math_agent.log_config import close_session_logger, new_session_logger
from math_agent.web.active_solves import SolveCapacity, max_concurrent_solves
from math_agent.web.project_store import ProjectStore, project_store_for_user
from math_agent.web.security import InMemoryRateLimiter


class TestSessionLoggerLifecycle:
    def test_closing_releases_handlers_and_registry_entry(self, tmp_path, monkeypatch):
        """Without this, each solve leaks one open fd and one Logger that
        logging keeps alive forever in Logger.manager.loggerDict."""
        monkeypatch.setattr("math_agent.log_config.SESSION_DIR", tmp_path)
        monkeypatch.setattr("math_agent.log_config._CONFIGURED", True)

        session_id, logger = new_session_logger("probe")
        assert logger.handlers
        assert logger.name in logging.Logger.manager.loggerDict

        close_session_logger(logger)

        assert not logger.handlers
        assert logger.name not in logging.Logger.manager.loggerDict

    def test_closing_is_idempotent(self, tmp_path, monkeypatch):
        monkeypatch.setattr("math_agent.log_config.SESSION_DIR", tmp_path)
        monkeypatch.setattr("math_agent.log_config._CONFIGURED", True)
        _, logger = new_session_logger("probe")
        close_session_logger(logger)
        close_session_logger(logger)
        close_session_logger(None)

    def test_many_sessions_do_not_accumulate(self, tmp_path, monkeypatch):
        monkeypatch.setattr("math_agent.log_config.SESSION_DIR", tmp_path)
        monkeypatch.setattr("math_agent.log_config._CONFIGURED", True)
        before = len(logging.Logger.manager.loggerDict)
        for _ in range(25):
            _, logger = new_session_logger("probe")
            close_session_logger(logger)
        assert len(logging.Logger.manager.loggerDict) == before


class TestRateLimiterBounds:
    def test_key_count_is_capped(self):
        """The key embeds the attacker-controlled request path, so an
        unbounded dict is a memory-exhaustion vector."""
        limiter = InMemoryRateLimiter(limit_per_minute=100, max_keys=50)
        for i in range(500):
            limiter.check(f"host:/api/{i}", now=1000.0)
        assert len(limiter._hits) <= 51

    def test_expired_keys_are_swept(self):
        limiter = InMemoryRateLimiter(limit_per_minute=100, max_keys=1000)
        for i in range(20):
            limiter.check(f"host:/api/{i}", now=1000.0)
        limiter.check("host:/api/fresh", now=1200.0)
        assert len(limiter._hits) == 1

    def test_limit_still_enforced_and_window_rolls(self):
        limiter = InMemoryRateLimiter(limit_per_minute=3)
        for _ in range(3):
            limiter.check("same", now=2000.0)
        with pytest.raises(Exception) as exc:
            limiter.check("same", now=2000.0)
        assert getattr(exc.value, "status_code", None) == 429
        limiter.check("same", now=2061.0)

    def test_zero_limit_disables_tracking(self):
        limiter = InMemoryRateLimiter(limit_per_minute=0)
        for i in range(100):
            limiter.check(f"k{i}")
        assert not limiter._hits


class TestSolveCapacity:
    def test_admission_is_bounded_and_non_blocking(self, monkeypatch):
        monkeypatch.setenv("CONJECTA_MAX_CONCURRENT_SOLVES", "3")
        capacity = SolveCapacity()
        assert [capacity.try_acquire() for _ in range(5)] == [
            True,
            True,
            True,
            False,
            False,
        ]
        assert capacity.in_flight == 3

    def test_release_frees_a_slot(self, monkeypatch):
        monkeypatch.setenv("CONJECTA_MAX_CONCURRENT_SOLVES", "1")
        capacity = SolveCapacity()
        assert capacity.try_acquire()
        assert not capacity.try_acquire()
        capacity.release()
        assert capacity.try_acquire()

    def test_over_release_cannot_create_slots(self, monkeypatch):
        monkeypatch.setenv("CONJECTA_MAX_CONCURRENT_SOLVES", "2")
        capacity = SolveCapacity()
        capacity.try_acquire()
        for _ in range(10):
            capacity.release()
        assert capacity.in_flight == 0

    def test_invalid_env_falls_back_to_default(self, monkeypatch):
        monkeypatch.setenv("CONJECTA_MAX_CONCURRENT_SOLVES", "not-a-number")
        assert max_concurrent_solves() == 24
        monkeypatch.setenv("CONJECTA_MAX_CONCURRENT_SOLVES", "0")
        assert max_concurrent_solves() == 1


class TestProjectStoreSharing:
    def test_one_instance_per_root(self, tmp_path, monkeypatch):
        """A fresh store replays the whole event log on first read (~21ms on a
        4.4MB log), so per-request construction is a real cost."""
        monkeypatch.setenv("CONJECTA_PROJECT_STORE_DIR", str(tmp_path))
        from math_agent.web import agent_factory, project_store

        project_store._STORE_CACHE.clear()
        a = project_store_for_user("u1")
        b = project_store_for_user("u1")
        c = agent_factory._project_store("u1")
        assert a is b is c
        assert project_store_for_user("u2") is not a

    def test_agent_factory_alias_is_the_real_cache(self, tmp_path, monkeypatch):
        """Tests isolate tenants by clearing this alias; if it were a private
        copy the clear would silently do nothing."""
        monkeypatch.setenv("CONJECTA_PROJECT_STORE_DIR", str(tmp_path))
        from math_agent.web import agent_factory, project_store

        assert agent_factory._PROJECT_STORE_CACHE is project_store._STORE_CACHE
        first = agent_factory._project_store("u1")
        agent_factory._PROJECT_STORE_CACHE.clear()
        assert agent_factory._project_store("u1") is not first

    def test_locks_are_per_instance(self, tmp_path):
        """A module-global lock serialized every tenant's reads behind any one
        tenant's index rebuild."""
        a = ProjectStore(root=tmp_path / "a")
        b = ProjectStore(root=tmp_path / "b")
        assert a._lock is not b._lock


class TestCheckpointCompaction:
    def test_superseded_checkpoints_are_reclaimed(self, tmp_path, monkeypatch):
        """Only the newest record per session is ever read, but every ReAct step
        appends a full trace snapshot."""
        monkeypatch.setattr(
            "math_agent.web.project_store.CHECKPOINT_COMPACT_MIN_BYTES", 4096
        )
        store = ProjectStore(root=tmp_path)
        payload = "x" * 2000
        for step in range(40):
            store.write_checkpoint({"session_id": "s1", "step": step, "blob": payload})

        size = store.checkpoint_log_path.stat().st_size
        assert size < 4096 * 3, f"log kept growing: {size} bytes"

        reloaded = ProjectStore(root=tmp_path)
        assert reloaded.get_checkpoint("s1")["step"] == 39

    def test_compaction_preserves_every_live_session(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "math_agent.web.project_store.CHECKPOINT_COMPACT_MIN_BYTES", 4096
        )
        store = ProjectStore(root=tmp_path)
        payload = "y" * 1000
        for round_ in range(6):
            for session in range(5):
                store.write_checkpoint(
                    {"session_id": f"s{session}", "round": round_, "blob": payload}
                )

        reloaded = ProjectStore(root=tmp_path)
        for session in range(5):
            assert reloaded.get_checkpoint(f"s{session}")["round"] == 5

    def test_distinct_sessions_are_not_rewritten(self, tmp_path, monkeypatch):
        """Compaction only pays off when superseded records dominate; a log of
        unique sessions must be left alone."""
        monkeypatch.setattr(
            "math_agent.web.project_store.CHECKPOINT_COMPACT_MIN_BYTES", 2048
        )
        store = ProjectStore(root=tmp_path)
        for i in range(30):
            store.write_checkpoint({"session_id": f"s{i}", "blob": "z" * 500})
        reloaded = ProjectStore(root=tmp_path)
        assert len(reloaded.list_checkpoints()) == 30


class TestConfigCache:
    def test_repeated_loads_share_one_object(self):
        clear_config_cache()
        assert load_config() is load_config()

    def test_env_override_invalidates(self, monkeypatch):
        clear_config_cache()
        first = load_config()
        monkeypatch.setenv("CONJECTA_LLM_MODEL", "probe-model")
        second = load_config()
        assert second is not first
        assert second.llm.model == "probe-model"

    def test_file_change_invalidates(self, tmp_path):
        clear_config_cache()
        path = tmp_path / "config.toml"
        path.write_text('[llm]\nmodel = "one"\n', encoding="utf-8")
        first = load_config(path)
        assert first.llm.model == "one"
        path.write_text('[llm]\nmodel = "two"\n', encoding="utf-8")
        os.utime(path, (0, 0))
        assert load_config(path).llm.model == "two"

    def test_cache_is_bounded(self, monkeypatch):
        clear_config_cache()
        from math_agent import config as config_module

        for i in range(40):
            monkeypatch.setenv("CONJECTA_LLM_MODEL", f"m{i}")
            load_config()
        assert len(config_module._CONFIG_CACHE) <= 32
