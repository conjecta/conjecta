"""Tests for the two-layer easy/hard prompt classification."""
from __future__ import annotations

import logging

import pytest

from math_agent.agent.prompt_difficulty import (
    classify_easy_prompt,
    trivially_easy,
)
from math_agent.billing.models import LLMResponse


class QueueCritic:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    async def complete(
        self, messages, system=None, temperature=None, response_format=None, *, logprobs=False
    ):
        self.calls += 1
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return LLMResponse(
            text=item, prompt_tokens=0, completion_tokens=0, total_tokens=0
        )


LOG = logging.getLogger("test.prompt_difficulty")


def test_trivially_easy_rule_short_circuit():
    assert trivially_easy("3+5=?")
    assert trivially_easy("12*9")
    assert trivially_easy("(1+2)*3 = ?")
    assert not trivially_easy("What is 2+2?")
    assert not trivially_easy("计算 3+5")
    assert not trivially_easy("Prove that sqrt(2) is irrational.")
    assert not trivially_easy("证明勾股定理")
    assert not trivially_easy("")
    # Long digit strings are not "tiny" anymore.
    assert not trivially_easy("1+" * 30 + "1")


@pytest.mark.asyncio
async def test_rule_short_circuit_never_calls_critic():
    critic = QueueCritic([AssertionError("critic must not be called")])
    assert await classify_easy_prompt("3+5=?", critic, run_log=LOG) is True
    assert critic.calls == 0


@pytest.mark.asyncio
async def test_rules_mode_treats_non_trivial_as_hard_without_critic():
    critic = QueueCritic([AssertionError("critic must not be called")])
    assert (
        await classify_easy_prompt("What is 2+2?", critic, mode="rules", run_log=LOG)
        is False
    )
    assert critic.calls == 0
    # The structural short-circuit still applies in rules mode.
    assert await classify_easy_prompt("3+5=?", critic, mode="rules", run_log=LOG) is True


@pytest.mark.asyncio
async def test_critic_verdict_easy():
    critic = QueueCritic(['{"difficulty": "easy", "reason": "single arithmetic step"}'])
    assert await classify_easy_prompt("What is 2+2?", critic, run_log=LOG) is True
    assert critic.calls == 1


@pytest.mark.asyncio
async def test_critic_verdict_hard():
    critic = QueueCritic(['{"difficulty": "hard", "reason": "needs a proof"}'])
    assert (
        await classify_easy_prompt("Prove that sqrt(2) is irrational.", critic, run_log=LOG)
        is False
    )


@pytest.mark.asyncio
async def test_critic_verdict_accepts_markdown_fences():
    critic = QueueCritic(['```json\n{"difficulty": "easy", "reason": "definition"}\n```'])
    assert await classify_easy_prompt("什么是导数？", critic, run_log=LOG) is True


@pytest.mark.asyncio
async def test_critic_failure_falls_back_to_hard():
    for response in (
        RuntimeError("backend down"),
        "not json at all",
        '{"verdict": "easy"}',
        '{"difficulty": "maybe"}',
    ):
        critic = QueueCritic([response])
        assert (
            await classify_easy_prompt("What is 2+2?", critic, run_log=LOG) is False
        )


@pytest.mark.asyncio
async def test_empty_problem_is_hard_without_critic():
    critic = QueueCritic([AssertionError("critic must not be called")])
    assert await classify_easy_prompt("", critic, run_log=LOG) is False
    assert critic.calls == 0
