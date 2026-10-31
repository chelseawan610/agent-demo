"""Optional metacognitive review stage for a completed TripPlan."""

import json
from typing import Any

from agent_framework import Agent

from travel_agent.schemas.request import TripRequest
from travel_agent.schemas.review import ReviewResult
from travel_agent.schemas.search import SearchPlan, SearchResults
from travel_agent.schemas.trip import TripPlan
from travel_agent.quality import check_workflow_invariants
from travel_agent.workflows.trip_planning import TraceObserver, plan_trip


def build_review_prompt(
    request: TripRequest,
    search_plan: SearchPlan,
    search_results: SearchResults,
    plan: TripPlan,
) -> str:
    deterministic_checks = check_workflow_invariants(
        request, search_plan, search_results, plan
    )
    checks_json = json.dumps(
        [
            {
                "name": check.name,
                "passed": check.passed,
                "message": check.message,
            }
            for check in deterministic_checks
        ],
        ensure_ascii=False,
    )
    return (
        "Review this travel plan as a read-only quality checker. Return only a "
        "ReviewResult JSON. Check that the plan respects the normalized request, "
        "requested service scope, user preferences, and tool evidence. Do not "
        "invent facts and do not propose changing tool facts.\n\n"
        f"REQUEST:\n{request.model_dump_json(ensure_ascii=False)}\n\n"
        f"SEARCH_PLAN:\n{search_plan.model_dump_json(ensure_ascii=False)}\n\n"
        f"SEARCH_RESULTS:\n{search_results.model_dump_json(ensure_ascii=False)}\n\n"
        f"TRIP_PLAN:\n{plan.model_dump_json(ensure_ascii=False)}\n\n"
        "DETERMINISTIC_CHECKS（程序硬校验，优先于你的主观判断）：\n"
        f"{checks_json}\n\n"
        "如果任何 deterministic check 为 false，ReviewResult 必须是 needs_revision。"
    )


async def review_trip_plan(
    agent: Agent,
    request: TripRequest,
    search_plan: SearchPlan,
    search_results: SearchResults,
    plan: TripPlan,
    on_trace: TraceObserver | None = None,
) -> ReviewResult:
    response = await plan_trip(
        agent,
        build_review_prompt(request, search_plan, search_results, plan),
        options={"response_format": ReviewResult},
        stage="review",
        on_trace=on_trace,
    )
    value: Any | None
    try:
        value = response.value
    except Exception:
        value = None
    if value is not None:
        return ReviewResult.model_validate(value)
    return ReviewResult.model_validate(json.loads(response.text.strip()))
