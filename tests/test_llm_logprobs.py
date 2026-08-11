from __future__ import annotations

import math

from math_agent.llm.utils import (
    confidence_from_mean_logprob,
    mean_logprob,
    token_logprobs_from_choice,
)


def test_confidence_from_mean_logprob_maps_geometric_mean():
    assert confidence_from_mean_logprob(None) is None
    assert confidence_from_mean_logprob(0.0) == 1.0
    assert abs(confidence_from_mean_logprob(math.log(0.9)) - 0.9) < 1e-9


def test_token_logprobs_from_choice():
    choice = type(
        "Choice",
        (),
        {
            "logprobs": type(
                "LP",
                (),
                {
                    "content": [
                        type("T", (), {"logprob": -0.1})(),
                        type("T", (), {"logprob": -0.2})(),
                    ]
                },
            )()
        },
    )()
    values = token_logprobs_from_choice(choice)
    assert values == [-0.1, -0.2]
    assert abs(mean_logprob(values) - (-0.15)) < 1e-9
