import pytest

from math_agent.billing.models import LLMResponse
from math_agent.llm.tracking import UsageAccumulator, UsageTrackingBackend


class _Backend:
    async def complete(
        self, messages, system="", temperature=None, response_format=None, *, logprobs=False
    ):
        return LLMResponse(text="ok", prompt_tokens=3, completion_tokens=4, total_tokens=7)

    async def stream(
        self, messages, system="", temperature=None, response_format=None, *, logprobs=False
    ):
        yield LLMResponse(text="a", prompt_tokens=0, completion_tokens=0, total_tokens=0)
        yield LLMResponse(text="", prompt_tokens=5, completion_tokens=6, total_tokens=11)


@pytest.mark.asyncio
async def test_tracks_complete_and_stream():
    acc = UsageAccumulator()
    backend = UsageTrackingBackend(_Backend(), acc)
    await backend.complete([])
    async for _ in backend.stream([]):
        pass
    assert acc.calls == 2
    assert (acc.prompt_tokens, acc.completion_tokens, acc.total_tokens) == (8, 10, 18)


@pytest.mark.asyncio
async def test_call_counting_backend_counts_every_call():
    from math_agent.llm.tracking import CallCountingBackend, LLMCallCounter

    counter = LLMCallCounter()
    backend = CallCountingBackend(_Backend(), counter)
    await backend.complete([])
    await backend.complete([])
    async for _ in backend.stream([]):
        pass
    assert counter.calls == 3
    counter.reset()
    assert counter.calls == 0
