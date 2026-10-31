import pytest

from agent_framework.exceptions import ChatClientException
from travel_agent.clients.fallback import FallbackAgent


class FakeAgent:
    def __init__(self, result):
        self.result = result
        self.calls = 0

    async def run(self, _request, **_kwargs):
        self.calls += 1
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


@pytest.mark.asyncio
async def test_fallback_only_handles_transient_provider_failure():
    primary = FakeAgent(ChatClientException("Connection error"))
    fallback = FakeAgent("fallback result")

    result = await FallbackAgent(primary, fallback, "qwen/qwen3.7-flash").run("hello")

    assert result == "fallback result"
    assert primary.calls == 1
    assert fallback.calls == 1


@pytest.mark.asyncio
async def test_fallback_does_not_hide_non_provider_errors():
    primary = FakeAgent(ValueError("bad schema"))
    fallback = FakeAgent("fallback result")
    wrapper = FallbackAgent(primary, fallback, "qwen/qwen3.7-flash")

    with pytest.raises(ValueError, match="bad schema"):
        await wrapper.run("hello")

    assert fallback.calls == 0
