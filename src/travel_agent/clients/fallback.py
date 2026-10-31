"""Optional model fallback with a narrow, explicit failure policy."""

from typing import Any

from agent_framework.exceptions import ChatClientException


def _is_transient_provider_error(error: ChatClientException) -> bool:
    """Only fail over on transport/provider failures, not user-data errors."""

    message = str(error).lower()
    original = error.args[1] if len(error.args) > 1 else None
    status_code = getattr(original, "status_code", None)
    return (
        status_code is not None
        and 500 <= status_code < 600
    ) or any(
        marker in message
        for marker in ("connection error", "timed out", "timeout", "rate limit")
    )


class FallbackAgent:
    """Duck-typed Agent wrapper that switches models only after provider failure.

    The wrapper intentionally does not retry schema or business validation
    errors. Those errors should be handled by the structured-output workflow,
    otherwise a fallback model would hide a real prompt/schema defect.
    """

    def __init__(
        self,
        primary: Any,
        fallback: Any,
        fallback_model: str,
        *,
        primary_model: str | None = None,
    ):
        self.primary = primary
        self.fallback = fallback
        self.primary_model = primary_model or getattr(primary, "model", None)
        self.fallback_model = fallback_model
        self.active_model = self.primary_model
        self.used_fallback = False

    async def run(self, request: str, **kwargs: Any) -> Any:
        self.active_model = self.primary_model
        try:
            return await self.primary.run(request, **kwargs)
        except ChatClientException as error:
            if not _is_transient_provider_error(error):
                raise
            self.used_fallback = True
            self.active_model = self.fallback_model
            return await self.fallback.run(request, **kwargs)
