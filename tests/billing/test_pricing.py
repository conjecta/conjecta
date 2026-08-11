
from math_agent.billing.pricing import PricingConfig, cost_for


def test_default_cost_for():
    assert cost_for(1_000_000, 1_000_000) == 35.0


def test_zero_cost():
    assert cost_for(0, 0) == 0.0


def test_custom_prices_via_env(monkeypatch):
    monkeypatch.setenv("CONJECTA_PRICE_INPUT_PER_1M", "10")
    monkeypatch.setenv("CONJECTA_PRICE_OUTPUT_PER_1M", "20")
    cfg = PricingConfig.from_env()
    assert cfg.input_per_1m == 10.0
    assert cfg.output_per_1m == 20.0
    assert cost_for(1_000_000, 1_000_000, config=cfg) == 30.0
