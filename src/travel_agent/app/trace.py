"""Redacted trajectory and metrics for one local Agent Harness run."""

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import math
from typing import Any
from uuid import uuid4

from travel_agent.schemas.evidence import ToolCallRecord
from travel_agent.schemas.trace import TraceEvent


@dataclass(frozen=True)
class TokenPricing:
    """USD price per one million input/output tokens."""

    input_usd_per_million: float | None = None
    output_usd_per_million: float | None = None


def _usage_value(usage: Any, name: str) -> int | None:
    if usage is None:
        return None
    if isinstance(usage, dict):
        value = usage.get(name)
    else:
        value = getattr(usage, name, None)
    return int(value) if isinstance(value, (int, float)) else None


def _p95(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, math.ceil(len(ordered) * 0.95) - 1)
    return round(ordered[index], 2)


class TraceRecorder:
    """Collect model/tool spans and derive safe run-level metrics."""

    def __init__(
        self,
        model: str | None = None,
        *,
        run_id: str | None = None,
        trace_id: str | None = None,
        pricing_by_model: dict[str, TokenPricing] | None = None,
        capture_content: bool = False,
    ):
        self.model = model
        self.run_id = run_id
        self.trace_id = trace_id or uuid4().hex
        self.pricing_by_model = pricing_by_model or {}
        self.capture_content = capture_content
        self.events: list[TraceEvent] = []

    def _response_preview(self, response: Any) -> str | None:
        """Capture a bounded response preview only when explicitly enabled."""

        if not self.capture_content or response is None:
            return None
        try:
            value = getattr(response, "text", None)
            if not value:
                value = getattr(response, "value", None)
            if hasattr(value, "model_dump"):
                value = value.model_dump(mode="json")
            if isinstance(value, (dict, list)):
                value = json.dumps(value, ensure_ascii=False)
            return str(value)[:4000] if value is not None else None
        except Exception:
            return None

    def _cost(
        self,
        model: str | None,
        input_tokens: int | None,
        output_tokens: int | None,
    ) -> float | None:
        pricing = self.pricing_by_model.get(model or "")
        if pricing is None:
            return None
        if input_tokens is None and output_tokens is None:
            return None
        amount = 0.0
        if input_tokens is not None and pricing.input_usd_per_million is not None:
            amount += input_tokens / 1_000_000 * pricing.input_usd_per_million
        if output_tokens is not None and pricing.output_usd_per_million is not None:
            amount += output_tokens / 1_000_000 * pricing.output_usd_per_million
        return round(amount, 8)

    def record_llm(
        self,
        stage: str,
        started_at: datetime,
        elapsed_ms: float,
        response: Any,
        status: str,
        error: Exception | None,
        model: str | None = None,
    ) -> None:
        usage = getattr(response, "usage_details", None) if response is not None else None
        actual_model = model or self.model
        input_tokens = _usage_value(usage, "input_token_count")
        output_tokens = _usage_value(usage, "output_token_count")
        self.events.append(
            TraceEvent(
                run_id=self.run_id,
                trace_id=self.trace_id,
                kind="llm",
                name=stage,
                stage=stage,
                status="ok" if status == "ok" else "error",
                started_at=started_at,
                elapsed_ms=round(elapsed_ms, 2),
                provider="configured LLM",
                model=actual_model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                total_tokens=_usage_value(usage, "total_token_count"),
                reasoning_tokens=_usage_value(usage, "reasoning_output_token_count"),
                estimated_cost_usd=self._cost(actual_model, input_tokens, output_tokens),
                output_preview=self._response_preview(response),
                error=(type(error).__name__ if error is not None else None),
            )
        )

    def build_trajectory(self, tool_results: Any = None) -> list[TraceEvent]:
        """Return a time-ordered copy; tool inputs are typed, not raw prompts."""

        events = list(self.events)
        for record in getattr(tool_results, "tool_calls", []):
            if not isinstance(record, ToolCallRecord):
                continue
            events.append(
                TraceEvent(
                    run_id=record.run_id or self.run_id,
                    trace_id=record.trace_id or self.trace_id,
                    kind="tool",
                    name=record.tool_name,
                    stage="tool",
                    status=record.status,
                    started_at=record.started_at or datetime.now(timezone.utc),
                    elapsed_ms=record.elapsed_ms,
                    provider=record.provider,
                    tool_inputs=record.inputs,
                    error=record.message or record.error_code,
                )
            )
        return sorted(events, key=lambda event: event.started_at)

    def build_metrics(self, tool_results: Any = None) -> dict[str, Any]:
        """Aggregate reliability, latency, token, and cost metrics for a run."""

        events = self.build_trajectory(tool_results)
        llm_events = [event for event in events if event.kind == "llm"]
        tool_events = [event for event in events if event.kind == "tool"]
        attempted_tools = [event for event in tool_events if event.status != "skipped"]
        attempted = [*llm_events, *attempted_tools]
        provider_errors = [
            event
            for event in attempted
            if event.status == "error" or event.status == "unavailable"
        ]
        total_cost = [
            event.estimated_cost_usd
            for event in llm_events
            if event.estimated_cost_usd is not None
        ]
        input_tokens = [event.input_tokens for event in llm_events if event.input_tokens is not None]
        output_tokens = [event.output_tokens for event in llm_events if event.output_tokens is not None]
        total_tokens = [event.total_tokens for event in llm_events if event.total_tokens is not None]

        def grouped_metrics(key: str) -> dict[str, dict[str, Any]]:
            groups: dict[str, list[TraceEvent]] = {}
            for event in attempted:
                group_name = getattr(event, key) or "unknown"
                groups.setdefault(group_name, []).append(event)
            return {
                group_name: {
                    "calls": len(group_events),
                    "success_rate": round(
                        sum(event.status == "ok" for event in group_events)
                        / len(group_events),
                        4,
                    ),
                    "error_rate": round(
                        sum(
                            event.status == "error"
                            or event.status == "unavailable"
                            for event in group_events
                        )
                        / len(group_events),
                        4,
                    ),
                    "p95_latency_ms": _p95(
                        [event.elapsed_ms for event in group_events]
                    ),
                }
                for group_name, group_events in groups.items()
            }

        return {
            "event_count": len(events),
            "llm_calls": len(llm_events),
            "llm_success_rate": round(
                sum(event.status == "ok" for event in llm_events) / len(llm_events), 4
            )
            if llm_events
            else None,
            "tool_calls": len(tool_events),
            "tool_success_rate": round(
                sum(event.status == "ok" for event in attempted_tools) / len(attempted_tools), 4
            )
            if attempted_tools
            else None,
            "provider_error_rate": round(len(provider_errors) / len(attempted), 4)
            if attempted
            else None,
            "llm_p95_latency_ms": _p95([event.elapsed_ms for event in llm_events]),
            "tool_p95_latency_ms": _p95(
                [event.elapsed_ms for event in attempted_tools]
            ),
            "p95_latency_ms": _p95([event.elapsed_ms for event in attempted]),
            "input_tokens": sum(input_tokens) if input_tokens else None,
            "output_tokens": sum(output_tokens) if output_tokens else None,
            "total_tokens": sum(total_tokens) if total_tokens else None,
            "estimated_cost_usd": round(sum(total_cost), 8) if total_cost else None,
            "pricing_configured": bool(self.pricing_by_model),
            "stages": grouped_metrics("stage"),
            "providers": grouped_metrics("provider"),
        }
