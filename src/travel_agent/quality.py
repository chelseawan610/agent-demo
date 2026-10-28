"""Deterministic workflow checks used beside model-based evaluations.

These checks do not ask another model to judge the run. They verify invariants
that must hold even when the planner's prose is non-deterministic.
"""

from dataclasses import dataclass

from travel_agent.schemas.request import TripRequest
from travel_agent.schemas.search import SearchPlan, SearchResults
from travel_agent.schemas.trip import TripPlan


@dataclass(frozen=True)
class WorkflowCheck:
    name: str
    passed: bool
    message: str
    layer: str = "workflow"
    category: str = "contract"


def _check_layer(name: str) -> str:
    """Map an invariant to the workflow layer it diagnoses."""

    if name.startswith("request_to_plan"):
        return "intake"
    if name.startswith("service_scope") or name.startswith("search_plan"):
        return "planning"
    if "evidence" in name or name == "itinerary_evidence_is_authoritative":
        return "evidence"
    if "route" in name or "activity" in name or "closed" in name:
        return "constraint"
    if name == "tool_trace_is_preserved":
        return "observability"
    return "workflow"


def _check_category(name: str) -> str:
    if "scope" in name or "executed" in name:
        return "tool_selection"
    if "evidence" in name:
        return "grounding"
    if "route" in name or "activity" in name or "closed" in name:
        return "constraint"
    if name.startswith("request_to_plan"):
        return "state_consistency"
    if name == "tool_trace_is_preserved":
        return "trace_loss"
    return "contract"


def check_workflow_invariants(
    request: TripRequest,
    search_plan: SearchPlan,
    search_results: SearchResults,
    final_plan: TripPlan,
) -> list[WorkflowCheck]:
    """Check process-level contracts after one workflow run."""

    checks: list[WorkflowCheck] = []

    def add(name: str, passed: bool, message: str) -> None:
        checks.append(
            WorkflowCheck(
                name,
                passed,
                message,
                layer=_check_layer(name),
                category=_check_category(name),
            )
        )

    add(
        "request_to_plan_duration",
        request.days is None or final_plan.days == request.days,
        "The plan's day count must equal the confirmed request.",
    )
    add(
        "request_to_plan_destination",
        request.destination is None or final_plan.destination == request.destination,
        "The final destination must equal the confirmed request.",
    )
    add(
        "request_to_plan_preferences",
        final_plan.preferences == request.preferences,
        "The plan must carry every preference the user stated.",
    )

    called = {record.tool_name for record in search_results.tool_calls}
    flight_call_count = sum(
        record.tool_name == "search_flights" for record in search_results.tool_calls
    )
    hotel_call_count = sum(
        record.tool_name == "search_hotels" for record in search_results.tool_calls
    )
    food_call_count = sum(
        record.tool_name == "search_food" for record in search_results.tool_calls
    )
    add(
        "search_plan_flights_executed_once",
        flight_call_count == len(search_plan.flight_queries),
        "Each planned flight query must run exactly once.",
    )
    add(
        "search_plan_hotels_executed_once",
        hotel_call_count == (1 if search_plan.hotel_query is not None else 0),
        "A planned hotel query must run exactly once.",
    )
    add(
        "search_plan_food_executed_once",
        food_call_count == (1 if search_plan.food_query is not None else 0),
        "A planned dining query must run exactly once.",
    )
    services = set(request.requested_services)
    if "flights" not in services:
        add(
            "service_scope_flights",
            not ({"search_flights", "build_flight_search_link"} & called),
            "Flight tools must not run when flights were not requested.",
        )
    if "hotels" not in services:
        add(
            "service_scope_hotels",
            not ({"search_hotels", "build_hotel_search_link"} & called),
            "Hotel tools must not run when hotels were not requested.",
        )
    if "food" not in services and search_plan.food_query is None:
        add(
            "service_scope_food",
            "search_food" not in called,
            "Dining tools must not run when dining was not requested.",
        )

    add(
        "flight_evidence_is_authoritative",
        final_plan.flights == search_results.flights,
        "Final flight candidates must come from tool results.",
    )
    add(
        "hotel_evidence_is_authoritative",
        final_plan.hotels == search_results.hotels,
        "Final hotel candidates must come from tool results.",
    )
    food_evidence = (
        search_results.foods.data
        if search_results.foods is not None
        and search_results.foods.status == "ok"
        and search_results.foods.data is not None
        else []
    )
    add(
        "food_evidence_is_authoritative",
        final_plan.food == food_evidence,
        "Final dining candidates must come from tool results, never invented by the model.",
    )
    expected_weather = (
        search_results.weather.data
        if search_results.weather is not None
        and search_results.weather.status == "ok"
        and search_results.weather.data is not None
        else []
    )
    add(
        "weather_evidence_is_authoritative",
        final_plan.weather == expected_weather,
        "Final weather must come from the weather tool, never written by the model.",
    )
    expected_routes = (
        search_results.routes.data
        if search_results.routes is not None
        and search_results.routes.status == "ok"
        and search_results.routes.data is not None
        else []
    )
    add(
        "route_evidence_is_authoritative",
        final_plan.routes == expected_routes,
        "Final routes must come from the routing tool; model prose is not a route fact.",
    )
    expected_knowledge_sources = []
    if (
        search_results.knowledge is not None
        and search_results.knowledge.status == "ok"
        and search_results.knowledge.data is not None
    ):
        expected_knowledge_sources = [
            {
                "chunk_id": hit.chunk_id,
                "source": hit.source,
                "title": hit.title,
                "section": hit.section,
                "score": hit.score,
            }
            for hit in search_results.knowledge.data
        ]
    add(
        "knowledge_evidence_is_authoritative",
        [item.model_dump(mode="json") for item in final_plan.knowledge_sources]
        == expected_knowledge_sources,
        "Knowledge citations must come from local retrieval, never invented by the model.",
    )
    expected_activity_keys = {
        (activity.day, activity.provider_id or activity.name)
        for activity in search_results.activities
    }
    actual_activity_keys = {
        (day.day, activity.provider_id or activity.name)
        for day in final_plan.itinerary
        for activity in day.activities
    }
    add(
        "itinerary_evidence_is_authoritative",
        actual_activity_keys == expected_activity_keys,
        "Itinerary places and days must come from the tool assignment; the model may not add or move them.",
    )
    if search_plan.route_enabled:
        add(
            "route_modes_are_authoritative",
            final_plan.transport_modes == search_plan.route_modes,
            "Transport modes must equal the confirmed SearchPlan; transit may not replace driving.",
        )
        days = search_plan.activity_query.days if search_plan.activity_query is not None else (request.days or 1)
        expected_route_calls = len(search_plan.route_modes) * days
        actual_route_calls = sum(
            record.tool_name in {"optimize_day_route", "optimize_transit_route"}
            for record in search_results.tool_calls
        )
        add(
            "route_profiles_are_executed_once_per_day",
            actual_route_calls == expected_route_calls,
            "Each planned route profile must run exactly once per day.",
        )
        driving_calls = sum(
            record.tool_name == "optimize_day_route"
            for record in search_results.tool_calls
        )
        transit_calls = sum(
            record.tool_name == "optimize_transit_route"
            for record in search_results.tool_calls
        )
        add(
            "each_requested_route_mode_is_executed_once_per_day",
            driving_calls == (days if "driving" in search_plan.route_modes else 0)
            and transit_calls == (days if "transit" in search_plan.route_modes else 0),
            "Driving and transit must each run per day as requested; one may not stand in for the other.",
        )
    add(
        "tool_trace_is_preserved",
        final_plan.tool_status == search_results.tool_calls,
        "The plan must preserve the status of every tool call.",
    )

    valid_days = (
        set(range(1, final_plan.days + 1))
        if final_plan.days is not None
        else set()
    )
    activity_days = {
        activity.day for activity in search_results.activities
    }
    add(
        "activity_days_are_valid",
        activity_days <= valid_days,
        "Activities must not fall outside the trip's days.",
    )
    add(
        "route_days_are_valid",
        all(route.day in valid_days for route in final_plan.routes),
        "Routes must not fall outside the trip's days.",
    )
    add(
        "closed_activities_are_not_scheduled",
        all(
            activity.opening_status != "closed"
            for day in final_plan.itinerary
            for activity in day.activities
        ),
        "A place confirmed closed must not reach the itinerary.",
    )

    return checks
