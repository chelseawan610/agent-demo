import os
from dataclasses import dataclass

from dotenv import load_dotenv


load_dotenv()


@dataclass(frozen=True)
class Settings:
    base_url: str
    api_key: str
    model: str
    output_mode: str = "auto"
    timeout_seconds: float = 90.0
    reasoning_effort: str | None = "none"
    input_price_usd_per_million: float | None = None
    output_price_usd_per_million: float | None = None

    @classmethod
    def from_env(
        cls,
        output_mode: str | None = None,
        *,
        model: str | None = None,
    ) -> "Settings":
        base_url = os.getenv("LLM_BASE_URL")
        api_key = os.getenv("LLM_API_KEY")
        model = model or os.getenv("LLM_MODEL")
        output_mode = (output_mode or os.getenv("TRIP_OUTPUT_MODE", "auto")).lower()
        timeout_seconds = float(os.getenv("LLM_TIMEOUT_SECONDS", "90"))
        reasoning_effort = os.getenv("LLM_REASONING_EFFORT", "none").strip() or None
        input_price = os.getenv("LLM_INPUT_PRICE_USD_PER_MILLION", "").strip()
        output_price = os.getenv("LLM_OUTPUT_PRICE_USD_PER_MILLION", "").strip()

        if not base_url:
            raise ValueError("LLM_BASE_URL is not set")

        if not api_key:
            raise ValueError("LLM_API_KEY is not set")

        if not model:
            raise ValueError("LLM_MODEL is not set")

        if output_mode not in {"text", "convert", "direct", "auto"}:
            raise ValueError("TRIP_OUTPUT_MODE must be text, convert, direct, or auto")

        return cls(
            base_url=base_url,
            api_key=api_key,
            model=model,
            output_mode=output_mode,
            timeout_seconds=timeout_seconds,
            reasoning_effort=reasoning_effort,
            input_price_usd_per_million=float(input_price) if input_price else None,
            output_price_usd_per_million=float(output_price) if output_price else None,
        )
