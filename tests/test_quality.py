from travel_agent.quality import check_workflow_invariants
from travel_agent.schemas.evidence import ToolCallRecord
from travel_agent.schemas.request import TripRequest
from travel_agent.schemas.search import SearchResults
from travel_agent.schemas.trip import ActivityOption, DayPlan, TripPlan
from travel_agent.workflows.search import build_search_plan


def test_workflow_checks_accept_authoritative_evidence_and_scope():
    request = TripRequest(
        origin="Shanghai",
        destination="Tokyo",
        start_date="2099-10-01",
        days=2,
        requested_services=["activities", "guide"],
        preferences=["Tokyo Tower"],
    )
    search_plan = build_search_plan(request)
    results = SearchResults(
        tool_calls=[
            ToolCallRecord(
                tool_name="resolve_destination",
                provider="Open-Meteo",
                status="ok",
                elapsed_ms=1,
            ),
            ToolCallRecord(
                tool_name="optimize_day_route",
                provider="OSRM",
                status="skipped",
                elapsed_ms=0,
            ),
            ToolCallRecord(
                tool_name="optimize_day_route",
                provider="OSRM",
                status="skipped",
                elapsed_ms=0,
            ),
        ]
    )
    final_plan = TripPlan(
        destination="Tokyo",
        days=2,
        summary="demo",
        preferences=["Tokyo Tower"],
        tool_status=results.tool_calls,
    )

    checks = check_workflow_invariants(request, search_plan, results, final_plan)

    assert all(check.passed for check in checks)


def test_workflow_checks_detects_model_changed_flight_evidence():
    request = TripRequest(
        origin="Shanghai",
        destination="Tokyo",
        start_date="2099-10-01",
        requested_services=["flights"],
    )
    search_plan = build_search_plan(request)
    results = SearchResults()
    final_plan = TripPlan(
        destination="Tokyo",
        days=1,
        summary="demo",
        flights=[
            {
                "origin": "Shanghai",
                "destination": "Tokyo",
                "date": "2099-10-01",
                "airline": "Invented Air",
                "departure": "00:00",
                "arrival": "00:01",
                "price": "free",
            }
        ],
    )

    checks = check_workflow_invariants(request, search_plan, results, final_plan)

    assert not next(
        check for check in checks if check.name == "flight_evidence_is_authoritative"
    ).passed


def test_workflow_checks_detects_model_invented_itinerary_activity():
    request = TripRequest(
        destination="Tokyo",
        days=1,
        requested_services=["activities", "guide"],
    )
    search_plan = build_search_plan(request)
    evidence = ActivityOption(
        destination="Tokyo",
        day=1,
        name="Tokyo Tower",
        provider_id="tokyo-tower",
        source="fixture",
    )
    results = SearchResults(activities=[evidence])
    final_plan = TripPlan(
        destination="Tokyo",
        days=1,
        summary="demo",
        itinerary=[
            DayPlan(
                day=1,
                title="demo",
                activities=[{"name": "Invented Place"}],
            )
        ],
    )

    checks = check_workflow_invariants(request, search_plan, results, final_plan)

    assert not next(
        check
        for check in checks
        if check.name == "itinerary_evidence_is_authoritative"
    ).passed
