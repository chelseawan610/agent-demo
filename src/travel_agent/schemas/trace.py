"""Serializable, redacted events for one agent workflow trajectory."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class TraceEvent(BaseModel):
    """One model or tool step without raw prompts or credentials."""

    run_id: str | None = None
    trace_id: str | None = None
    kind: Literal["llm", "tool"]
    name: str
    stage: str | None = None
    status: Literal["ok", "error", "unavailable", "skipped"]
    started_at: datetime
    elapsed_ms: float
    provider: str | None = None
    model: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    reasoning_tokens: int | None = None
    estimated_cost_usd: float | None = None
    tool_inputs: dict[str, object] | None = None
    output_preview: str | None = None
    error: str | None = None
