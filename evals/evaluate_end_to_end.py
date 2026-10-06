"""Optional model-backed end-to-end evaluation.

The travel HTTP providers are replaced by deterministic evidence fixtures, so
this evaluates the model-facing workflow without spending network calls on
weather/places or allowing a provider outage to obscure model behavior.
"""

import argparse
import asyncio
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from evals.workflow import build_fixture_results
from evals.metrics import summarize_rows
from travel_agent.agents.intake import create_intake_agent
from travel_agent.agents.travel import create_travel_agent, create_trip_formatter
from travel_agent.quality import check_workflow_invariants
from travel_agent.workflows.intake import extract_trip_request
from travel_agent.workflows.prompts import build_planner_prompt
from travel_agent.workflows.search import build_search_plan
from travel_agent.workflows.trip_planning import plan_trip_structured


@dataclass(frozen=True)
class EndToEndCase:
    name: str
    text: str
    expected_services: tuple[str, ...] | None = None
    expected_ready: bool = True


CASES = [
    EndToEndCase(
        "full_zh_with_preference",
        "我从上海出发，2026年10月1日去东京玩三天，我想去东京塔。",
        ("flights", "hotels", "activities", "guide"),
    ),
    EndToEndCase(
        "guide_only_en",
        "Give me a two-day sightseeing guide for Tokyo, no flights or hotels.",
        ("activities", "guide"),
    ),
    EndToEndCase(
        "flights_only_zh",
        "只查上海到东京 2026-10-01 的单程机票。",
        ("flights",),
    ),
    EndToEndCase(
        "hotels_only_zh",
        "只查东京 2026-11-01 入住两晚的酒店。",
        ("hotels",),
    ),
    EndToEndCase(
        "missing_flight_date",
        "只查从广州到大阪的机票，日期还没决定。",
        ("flights",),
        expected_ready=False,
    ),
]


async def evaluate_model(model: str, cases: list[EndToEndCase]) -> dict[str, Any]:
    intake = create_intake_agent(model=model)
    planner = create_travel_agent(model=model)
    formatter = create_trip_formatter(model=model)
    rows: list[dict[str, Any]] = []

    for case in cases:
        started = asyncio.get_running_loop().time()
        try:
            request = await extract_trip_request(intake, case.text)
            failures: list[str] = []
            if case.expected_services is not None and tuple(request.requested_services) != case.expected_services:
                failures.append(
                    f"services expected={case.expected_services} actual={tuple(request.requested_services)}"
                )
            if case.expected_ready and request.status != "ready":
                failures.append(f"ready expected=True actual={request.status}")
            if not case.expected_ready:
                if request.status == "ready":
                    failures.append("missing-field case unexpectedly became ready")
                rows.append(
                    {
                        "name": case.name,
                        "passed": not failures,
                        "failures": failures,
                        "stage": "intake_only",
                        "elapsed_ms": round((asyncio.get_running_loop().time() - started) * 1000, 2),
                    }
                )
                continue
            if request.status != "ready":
                rows.append(
                    {
                        "name": case.name,
                        "passed": False,
                        "failures": failures or ["intake did not produce a ready request"],
                        "stage": "intake",
                        "elapsed_ms": round((asyncio.get_running_loop().time() - started) * 1000, 2),
                    }
                )
                continue

            search_plan = build_search_plan(request)
            tool_results = build_fixture_results(request, search_plan)
            final_plan = await plan_trip_structured(
                planner,
                build_planner_prompt(case.text, request, search_plan, tool_results),
                formatter=formatter,
                mode="auto",
                tools=[],
                request_context=request,
                search_results=tool_results,
            )
            failures.extend(
                check.message
                for check in check_workflow_invariants(
                    request, search_plan, tool_results, final_plan
                )
                if not check.passed
            )
            rows.append(
                {
                    "name": case.name,
                    "passed": not failures,
                    "failures": failures,
                    "stage": "intake_to_trip_plan",
                    "final_status": final_plan.status,
                    "elapsed_ms": round((asyncio.get_running_loop().time() - started) * 1000, 2),
                }
            )
        except Exception as error:
            rows.append(
                {
                    "name": case.name,
                    "passed": False,
                    "failures": [f"{type(error).__name__}: {error}"],
                    "stage": "exception",
                    "elapsed_ms": round((asyncio.get_running_loop().time() - started) * 1000, 2),
                }
            )

    return {
        "model": model,
        **summarize_rows(rows),
        "cases": rows,
    }


async def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate the full travel workflow with fixture tools")
    parser.add_argument("--models", required=True, help="Comma-separated model IDs")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()
    selected = CASES[: args.limit] if args.limit else CASES
    reports = [
        await evaluate_model(model.strip(), selected)
        for model in args.models.split(",")
        if model.strip()
    ]
    report = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "case_count": len(selected),
        "models": reports,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    asyncio.run(main())
