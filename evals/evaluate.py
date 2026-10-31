"""Run the intake golden set against one or more configured models.

This is intentionally opt-in because it calls the configured LLM provider and
may consume tokens. Example:

    PYTHONPATH=src uv run python -m evals.evaluate \\
      --models qwen/qwen3.7-flash,deepseek/deepseek-v4.1-flash
"""

import argparse
import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from evals.cases import CASES, HELD_OUT_CASES, IntakeCase
from evals.metrics import summarize_rows
from travel_agent.agents.intake import create_intake_agent
from travel_agent.workflows.intake import extract_trip_request


def _check(case: IntakeCase, request: Any) -> list[str]:
    failures: list[str] = []
    if case.expected_services is not None and tuple(request.requested_services) != case.expected_services:
        failures.append(
            f"services expected={case.expected_services} actual={tuple(request.requested_services)}"
        )
    for field in ("origin", "destination", "days", "nights"):
        expected = getattr(case, field)
        if expected is not None and getattr(request, field) != expected:
            failures.append(f"{field} expected={expected!r} actual={getattr(request, field)!r}")
    if case.start_date is not None:
        actual = request.start_date.isoformat() if request.start_date else None
        if actual != case.start_date:
            failures.append(f"start_date expected={case.start_date!r} actual={actual!r}")
    if case.return_date is not None:
        actual = request.return_date.isoformat() if request.return_date else None
        if actual != case.return_date:
            failures.append(f"return_date expected={case.return_date!r} actual={actual!r}")
    if case.budget_amount is not None:
        actual = request.budget.amount if request.budget else None
        if actual != case.budget_amount:
            failures.append(f"budget_amount expected={case.budget_amount!r} actual={actual!r}")
    if case.budget_level is not None:
        actual = request.budget.level if request.budget else None
        if actual != case.budget_level:
            failures.append(f"budget_level expected={case.budget_level!r} actual={actual!r}")
    if case.budget_amount is None and case.budget_level is None and case.name == "budget_unspecified":
        if request.budget is not None:
            failures.append(f"budget expected=None actual={request.budget.model_dump()!r}")
    if tuple(request.preferences) != case.preferences:
        failures.append(
            f"preferences expected={case.preferences} actual={tuple(request.preferences)}"
        )
    if case.missing_fields is not None and tuple(request.missing_fields) != case.missing_fields:
        failures.append(
            f"missing_fields expected={case.missing_fields} actual={tuple(request.missing_fields)}"
        )
    if case.ready is not None and (request.status == "ready") != case.ready:
        failures.append(f"ready expected={case.ready!r} actual={request.status!r}")
    return failures


async def evaluate_model(model: str, cases: list[IntakeCase]) -> dict[str, Any]:
    agent = create_intake_agent(model=model)
    rows: list[dict[str, Any]] = []
    for case in cases:
        started = asyncio.get_running_loop().time()
        try:
            request = await extract_trip_request(agent, case.text)
            failures = _check(case, request)
            rows.append(
                {
                    "name": case.name,
                    "passed": not failures,
                    "failures": failures,
                    "request": request.model_dump(mode="json"),
                    "elapsed_ms": round((asyncio.get_running_loop().time() - started) * 1000, 2),
                }
            )
        except Exception as error:
            rows.append(
                {
                    "name": case.name,
                    "passed": False,
                    "failures": [f"{type(error).__name__}: {error}"],
                    "elapsed_ms": round((asyncio.get_running_loop().time() - started) * 1000, 2),
                }
            )
    return {
        "model": model,
        **summarize_rows(rows),
        "cases": rows,
    }


async def main() -> None:
    parser = argparse.ArgumentParser(description="Compare intake models on the travel golden set")
    parser.add_argument("--models", required=True, help="Comma-separated OpenRouter model ids")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument(
        "--split",
        choices=["all", "golden", "heldout"],
        default="all",
        help="Evaluate the original golden set, held-out paraphrases, or both",
    )
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()
    selected_cases = {
        "golden": CASES,
        "heldout": HELD_OUT_CASES,
        "all": [*CASES, *HELD_OUT_CASES],
    }[args.split]
    cases = selected_cases[: args.limit] if args.limit else selected_cases
    reports = [await evaluate_model(model.strip(), cases) for model in args.models.split(",") if model.strip()]
    report = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "case_count": len(cases),
        "split": args.split,
        "models": reports,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    asyncio.run(main())
