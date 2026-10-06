"""Offline end-to-end workflow regression cases.

These cases deliberately stop before public HTTP calls. They test the contracts
owned by this repository: service routing, dates, evidence authority, and
deterministic output assembly.
"""

from dataclasses import dataclass
from datetime import date
from time import perf_counter
from typing import Any

from travel_agent.quality import check_workflow_invariants
from evals.metrics import summarize_rows
from travel_agent.schemas.evidence import PlaceCandidate, ToolCallRecord, ToolResult
from travel_agent.schemas.request import TripRequest
from travel_agent.schemas.search import SearchResults
from travel_agent.schemas.trip import FlightOption, HotelOption
from travel_agent.rag.models import KnowledgeHit
from travel_agent.workflows.search import build_search_plan
from travel_agent.workflows.trip_planning import build_deterministic_trip_plan


@dataclass(frozen=True)
class WorkflowCase:
    name: str
    request: TripRequest
    expected_ready: bool = True


def cases() -> list[WorkflowCase]:
    return [
        WorkflowCase(
            "full_trip_with_preference",
            TripRequest(
                origin="Shanghai",
                destination="Tokyo",
                start_date=date(2027, 10, 1),
                days=3,
                preferences=["Tokyo Tower"],
            ),
        ),
        WorkflowCase(
            "guide_only_no_transport",
            TripRequest(
                destination="Paris",
                days=2,
                requested_services=["activities", "guide"],
                preferences=["museums"],
            ),
        ),
        WorkflowCase(
            "flights_only_one_way",
            TripRequest(
                origin="Beijing",
                destination="Singapore",
                start_date=date(2027, 11, 8),
                requested_services=["flights"],
            ),
        ),
        WorkflowCase(
            "hotels_only",
            TripRequest(
                destination="Bangkok",
                start_date=date(2027, 12, 12),
                nights=3,
                requested_services=["hotels"],
            ),
        ),
        WorkflowCase(
            "flight_already_bought",
            TripRequest(
                destination="Tokyo",
                days=3,
                requested_services=["activities", "guide"],
            ),
        ),
        WorkflowCase(
            "food_only_with_explicit_scope",
            TripRequest(
                destination="Tokyo",
                days=2,
                requested_services=["food"],
                preferences=["sushi"],
            ),
        ),
        WorkflowCase(
            "transit_route_keeps_adapter_boundary",
            TripRequest(
                destination="Tokyo",
                days=2,
                requested_services=["activities", "guide"],
                transport_mode="transit",
            ),
        ),
        WorkflowCase(
            "complex_transit_food_preference_budget",
            TripRequest(
                origin="Shenzhen",
                destination="Hong Kong",
                start_date=date(2027, 10, 3),
                days=3,
                budget={"amount": 10000, "period": "total"},
                transport_mode="transit",
                preferences=["维多利亚港", "粤菜"],
                preference_details=[
                    {
                        "label": "维多利亚港",
                        "search_query": "Victoria Harbour Hong Kong",
                        "kind": "place",
                    },
                    {
                        "label": "粤菜",
                        "search_query": "Cantonese food Hong Kong",
                        "kind": "food",
                    },
                ],
            ),
        ),
        WorkflowCase(
            "missing_flight_date",
            TripRequest(
                origin="Guangzhou",
                destination="Osaka",
                requested_services=["flights"],
            ),
            expected_ready=False,
        ),
        WorkflowCase(
            "rag_evidence_is_preserved",
            TripRequest(
                destination="Tokyo",
                days=2,
                requested_services=["activities", "guide"],
                preferences=["Tokyo Tower"],
            ),
        ),
    ]


def build_fixture_results(request: TripRequest, plan: Any) -> SearchResults:
    """Build deterministic evidence shaped like the real executor output."""

    calls = [
        ToolCallRecord(
            tool_name="search_flights",
            inputs=query.model_dump(mode="json"),
            status="ok",
            elapsed_ms=1,
            provider="fixture flight provider",
        )
        for query in plan.flight_queries
    ]
    if plan.hotel_query is not None:
        calls.append(
            ToolCallRecord(
                tool_name="search_hotels",
                inputs=plan.hotel_query.model_dump(mode="json"),
                status="ok",
                elapsed_ms=1,
                provider="fixture hotel provider",
            )
        )

    if plan.food_query is not None:
        calls.append(
            ToolCallRecord(
                tool_name="search_food",
                inputs=plan.food_query.model_dump(mode="json"),
                status="ok",
                elapsed_ms=1,
                provider="fixture food provider",
            )
        )

    knowledge = None
    if plan.knowledge_query:
        calls.append(
            ToolCallRecord(
                tool_name="retrieve_knowledge",
                inputs={
                    "query": plan.knowledge_query,
                    "top_k": plan.knowledge_top_k,
                    "max_chars": plan.knowledge_max_chars,
                },
                status="ok",
                elapsed_ms=1,
                provider="local markdown BM25",
            )
        )
        knowledge = ToolResult.ok(
            "local markdown BM25",
            [
                KnowledgeHit(
                    chunk_id="fixture.md:1:1",
                    source="fixture.md",
                    title="Fixture guide",
                    section="Tokyo",
                    text="Tokyo Tower is a named landmark.",
                    score=2.0,
                    matched_terms=["tokyo", "tower"],
                )
            ],
        )

    if plan.route_enabled:
        for mode in plan.route_modes:
            for day in range(1, (plan.activity_query.days if plan.activity_query else 1) + 1):
                calls.append(
                    ToolCallRecord(
                        tool_name=(
                            "optimize_transit_route"
                            if mode == "transit"
                            else "optimize_day_route"
                        ),
                        inputs={"day": day, "mode": mode, "stops": []},
                        status="unavailable" if mode == "transit" else "skipped",
                        elapsed_ms=1,
                        provider=(
                            "OpenTripPlanner adapter" if mode == "transit" else "OSRM"
                        ),
                        error_code="not_configured" if mode == "transit" else None,
                        message=(
                            "fixture transit adapter unavailable"
                            if mode == "transit"
                            else "fixture has no route stops"
                        ),
                    )
                )

    flights = [
        FlightOption(
            origin=query.origin,
            destination=query.destination,
            date=query.departure_date,
            airline="Fixture Air",
            departure="09:00",
            arrival="12:00",
            price="fixture",
        )
        for query in plan.flight_queries
    ]
    hotels = (
        [
            HotelOption(
                name="Fixture Hotel",
                area="central",
                nights=plan.hotel_query.nights,
                price_per_night="fixture",
                rating=4.0,
            )
        ]
        if plan.hotel_query is not None
        else []
    )
    activities = []
    if plan.activity_query is not None:
        labels = plan.activity_query.preferences
        for day in range(1, plan.activity_query.days + 1):
            name = labels[day - 1] if day <= len(labels) else f"Fixture Activity Day {day}"
            activities.append(
                {
                    "destination": request.destination,
                    "day": day,
                    "name": name,
                    "description": "fixture evidence",
                    "provider_id": f"fixture-{day}",
                    "source": "fixture provider",
                    "verified": True,
                }
            )
    foods = (
        ToolResult.ok(
            "fixture food provider",
            [
                PlaceCandidate(
                    name="Fixture Sushi",
                    category="restaurant",
                    cuisine="sushi",
                    provider_id="fixture-food-1",
                    source="fixture food provider",
                    venue_type="mixed",
                )
            ],
        )
        if plan.food_query is not None
        else None
    )
    route_result = (
        ToolResult.unavailable(
            "OpenTripPlanner adapter", "not_configured", "fixture transit adapter unavailable"
        )
        if plan.route_enabled and "transit" in plan.route_modes
        else None
    )
    return SearchResults(
        flights=flights,
        hotels=hotels,
        activities=activities,
        foods=foods,
        routes=route_result,
        route_modes=plan.route_modes,
        knowledge=knowledge,
        tool_calls=calls,
    )


def run_case(case: WorkflowCase) -> dict[str, Any]:
    started = perf_counter()
    request = case.request
    if not case.expected_ready:
        return {
            "name": case.name,
            "passed": request.status != "ready",
            "checks": {"missing_request_stops_before_search": request.status != "ready"},
            "message": "未满足硬字段时不得进入搜索执行。",
            "elapsed_ms": round((perf_counter() - started) * 1000, 2),
        }

    plan = build_search_plan(request)
    results = build_fixture_results(request, plan)
    final_plan = build_deterministic_trip_plan(request, results)
    checks = check_workflow_invariants(request, plan, results, final_plan)
    return {
        "name": case.name,
        "passed": all(check.passed for check in checks),
        "checks": {check.name: check.passed for check in checks},
        "failures": [check.message for check in checks if not check.passed],
        "tool_names": [record.tool_name for record in results.tool_calls],
        "elapsed_ms": round((perf_counter() - started) * 1000, 2),
    }


def run_all() -> dict[str, Any]:
    rows = [run_case(case) for case in cases()]
    return {
        **summarize_rows(rows),
        "cases": rows,
    }
