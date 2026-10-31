from openai import AsyncOpenAI
from agent_framework.openai import OpenAIChatCompletionClient

from travel_agent.config import Settings


def create_client(
    settings: Settings | None = None,
    *,
    model: str | None = None,
) -> OpenAIChatCompletionClient:
    settings = settings or Settings.from_env(model=model)
    async_client = AsyncOpenAI(
        api_key=settings.api_key,
        base_url=settings.base_url,
        timeout=settings.timeout_seconds,
    )

    client = OpenAIChatCompletionClient(
        model=model or settings.model,
        async_client=async_client,
    )
    # Request-level guardrails: tools may be called more than once when a
    # real API needs pagination or a return-leg query, but the model cannot
    # loop forever or spend an unbounded amount on one user request.
    client.function_invocation_configuration.update(
        {
            "max_iterations": 6,
            "max_function_calls": 12,
            "max_duration_seconds": 45.0,
            "max_consecutive_errors_per_request": 3,
        }
    )
    return client
