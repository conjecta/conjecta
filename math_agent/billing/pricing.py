from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class PricingConfig:
    input_per_1m: float
    output_per_1m: float

    @classmethod
    def from_env(cls) -> "PricingConfig":
        return cls(
            input_per_1m=float(os.getenv("CONJECTA_PRICE_INPUT_PER_1M", "5")),
            output_per_1m=float(os.getenv("CONJECTA_PRICE_OUTPUT_PER_1M", "30")),
        )


def cost_for(
    prompt_tokens: int,
    completion_tokens: int,
    *,
    provider: str | None = None,
    model: str | None = None,
    config: PricingConfig | None = None,
) -> float:
    """Return the estimated cost in USD for the given token counts.

    Args:
        prompt_tokens: Number of prompt/input tokens.
        completion_tokens: Number of completion/output tokens.
        provider: Optional provider identifier (e.g. "openai").
        model: Optional model identifier (e.g. "gpt-4o").
        config: Optional explicit pricing configuration.

    Note:
        Per-provider/per-model pricing is not yet implemented. The optional
        ``provider`` and ``model`` arguments are accepted for forward
        compatibility; all models currently use the same default price.
        TODO: Implement model-specific pricing tables.
    """
    del provider, model  # Reserved for future per-model pricing.
    cfg = config or PricingConfig.from_env()
    return (
        prompt_tokens * cfg.input_per_1m / 1_000_000
        + completion_tokens * cfg.output_per_1m / 1_000_000
    )
