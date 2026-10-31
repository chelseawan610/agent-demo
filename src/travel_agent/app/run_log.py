"""Small JSONL run log for local debugging and research evaluation."""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4


def new_run_id() -> str:
    return uuid4().hex


def _dump(value: Any) -> Any:
    if value is None:
        return None
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return value


def write_run_record(
    path: str | Path | None,
    *,
    run_id: str,
    mode: str,
    model: str | None,
    status: str,
    request_text: str | None = None,
    request: Any = None,
    search_plan: Any = None,
    tool_results: Any = None,
    final_plan: Any = None,
    error: str | None = None,
    fallback_used: bool = False,
    include_input: bool = False,
    trajectory: Any = None,
    trace_id: str | None = None,
    metrics: Any = None,
) -> None:
    """Append a redacted run summary; logging failure never breaks a run."""

    if path is None:
        return
    try:
        record: dict[str, Any] = {
            "run_id": run_id,
            "trace_id": trace_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "mode": mode,
            "model": model,
            "fallback_used": fallback_used,
            # Keep the historical field above, but expose the two different
            # meanings explicitly for new runs.
            "model_fallback_used": fallback_used,
            "deterministic_fallback_used": (
                getattr(final_plan, "generation_source", None)
                == "deterministic_fallback"
            ),
            "status": status,
            "request": _dump(request),
            "search_plan": _dump(search_plan),
            "tool_calls": [
                _dump(item)
                for item in getattr(tool_results, "tool_calls", [])
            ],
            "search_results": _dump(tool_results),
            "final": (
                {
                    "status": getattr(final_plan, "status", None),
                    "destination": getattr(final_plan, "destination", None),
                    "days": getattr(final_plan, "days", None),
                    "generation_source": getattr(
                        final_plan, "generation_source", None
                    ),
                }
                if final_plan is not None
                else None
            ),
            "final_plan": _dump(final_plan),
            "error": error,
            "trajectory": [_dump(item) for item in (trajectory or [])],
            "metrics": _dump(metrics),
            "failure_attribution": (
                {
                    "categories": metrics.get("failure_categories", {}),
                    "layers": metrics.get("failure_by_stage", {}),
                    "tool_failures": [
                        {
                            "tool": item.tool_name,
                            "stage": item.stage,
                            "provider": item.provider,
                            "status": item.status,
                            "error_code": item.error_code,
                            "error_category": item.error_category,
                            "retryable": item.retryable,
                        }
                        for item in getattr(tool_results, "tool_calls", [])
                        if getattr(item, "status", None) in {"error", "unavailable"}
                    ],
                }
                if isinstance(metrics, dict)
                else None
            ),
        }
        if include_input:
            record["input"] = request_text
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        # Observability is useful, but must never make the travel workflow fail.
        return
