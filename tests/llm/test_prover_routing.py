"""Role-routing tests: the optional formal-prover backend."""
from unittest.mock import MagicMock

from math_agent.config import ProverConfig
from math_agent.llm.factory import create_prover_backend


def test_prover_backend_disabled_by_default():
    assert create_prover_backend(ProverConfig()) is None
    assert create_prover_backend(ProverConfig(model="  ")) is None


def test_prover_backend_ignores_non_prover_config():
    # Defensive: tests patch load_config with MagicMock containers; the
    # factory must not try to build a backend from mock attributes.
    assert create_prover_backend(MagicMock()) is None


def test_prover_backend_uses_explicit_base_url(monkeypatch):
    monkeypatch.setenv("PROVER_TEST_KEY", "x")
    config = ProverConfig(
        provider="openai",
        model="gpt-5.6-sol",
        base_url="http://localhost:8000/v1",
        api_key="sk-test",
    )
    backend = create_prover_backend(config)
    assert backend is not None
    assert backend.model == "gpt-5.6-sol"
    assert backend._base_url == "http://localhost:8000/v1"
