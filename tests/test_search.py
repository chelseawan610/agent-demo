import pytest

from travel_agent.schemas.request import PreferenceSpec, TripRequest
from travel_agent.schemas.evidence import GeoPoint, PlaceCandidate, ToolResult, WeatherDay
from travel_agent.workflows.search import build_search_plan, execute_search_plan


def test_search_plan_preserves_preferences_and_converts_days_to_nights():
    request = TripRequest(
        origin="上海",
        destination="东京",
        start_date="2099-10-01",
        days=3,
        preferences=["东京塔"],
        budget={"amount": 10000, "period": "total"},
    )

    plan = build_search_plan(request)

    assert plan.hotel_query.nights == 2
    assert plan.activity_query.preferences == ["东京塔"]
    assert plan.activity_query.start_date.isoformat() == "2099-10-01"
    assert plan.attraction_query.limit == 6
    assert plan.hotel_query.budget.amount == 10000


def test_incomplete_request_cannot_create_search_plan():
    request = TripRequest(destination="Tokyo")

    with pytest.raises(ValueError, match="出发城市"):
        build_search_plan(request)


def test_search_execution_calls_only_queries_in_the_plan(monkeypatch):
    monkeypatch.setattr(
        "travel_agent.workflows.search.resolve_destination",
        lambda _destination: ToolResult.ok("test", GeoPoint(latitude=35.0, longitude=139.0)),
    )
    monkeypatch.setattr(
        "travel_agent.workflows.search.get_weather_forecast",
        lambda *_args: ToolResult.ok(
            "test",
            [
                WeatherDay(
                    date="2099-10-01",
                    weather_code=1,
                    temperature_max_c=20,
                    temperature_min_c=10,
                )
            ],
        ),
    )
    monkeypatch.setattr(
        "travel_agent.workflows.search.search_attractions",
        lambda *_args: ToolResult.ok(
            "test",
            [
                PlaceCandidate(
                    name="A",
                    provider_id="a",
                    source="test",
                    point=GeoPoint(latitude=35.1, longitude=139.1),
                ),
                PlaceCandidate(
                    name="B",
                    provider_id="b",
                    source="test",
                    point=GeoPoint(latitude=35.2, longitude=139.2),
                ),
            ],
        ),
    )
    monkeypatch.setattr(
        "travel_agent.workflows.search.optimize_day_route",
        lambda day, _places: ToolResult.skipped("test", f"day {day}"),
    )
    request = TripRequest(
        destination="东京",
        start_date="2099-10-01",
        days=2,
        requested_services=["activities"],
    )

    results = execute_search_plan(build_search_plan(request))

    assert results.flights == []
    assert results.hotels == []
    assert len(results.activities) == 2


def test_three_day_trip_uses_october_third_as_return_and_checkout():
    request = TripRequest(
        origin="Shanghai",
        destination="Tokyo",
        start_date="2099-10-01",
        days=3,
    )

    plan = build_search_plan(request)

    assert plan.flight_queries[1].departure_date.isoformat() == "2099-10-03"
    assert plan.flight_link_query.return_date.isoformat() == "2099-10-03"
    assert plan.hotel_link_query.check_out_date.isoformat() == "2099-10-03"


def test_flight_only_without_return_information_stays_one_way():
    request = TripRequest(
        requested_services=["flights"],
        origin="Shanghai",
        destination="Tokyo",
        start_date="2099-10-01",
    )

    plan = build_search_plan(request)

    assert len(plan.flight_queries) == 1
    assert plan.flight_link_query.return_date is None


def test_one_day_full_plan_skips_hotel_and_returns_same_day():
    request = TripRequest(
        origin="Shanghai",
        destination="Tokyo",
        start_date="2026-10-31",
        days=1,
    )

    plan = build_search_plan(request)

    assert plan.hotel_query is None
    assert plan.flight_queries[1].departure_date.isoformat() == "2026-10-31"


def test_explicit_nights_and_cross_month_date_share_one_boundary():
    request = TripRequest(
        origin="Shanghai",
        destination="Tokyo",
        start_date="2026-10-31",
        nights=2,
    )

    plan = build_search_plan(request)

    assert plan.flight_queries[1].departure_date.isoformat() == "2026-11-02"
    assert plan.hotel_link_query.check_out_date.isoformat() == "2026-11-02"


def test_guide_scope_never_builds_flight_or_hotel_work():
    request = TripRequest(
        destination="Tokyo",
        requested_services=["activities", "guide"],
    )

    plan = build_search_plan(request)

    assert plan.flight_queries == []
    assert plan.hotel_query is None
    assert plan.flight_link_query is None
    assert plan.hotel_link_query is None
    assert plan.attraction_query is not None


def test_food_scope_builds_only_a_bounded_food_query():
    request = TripRequest(
        destination="Tokyo",
        days=3,
        requested_services=["food"],
        preferences=["寿司"],
        preference_details=[
            {"label": "寿司", "search_query": "sushi Tokyo", "kind": "food"}
        ],
    )

    plan = build_search_plan(request)

    assert plan.food_query is not None
    assert plan.food_query.limit == 6
    assert plan.activity_query is None
    assert plan.attraction_query is None
    assert plan.route_enabled is False


def test_transit_mode_is_preserved_in_search_plan():
    request = TripRequest(
        destination="Tokyo",
        days=2,
        requested_services=["activities", "guide"],
        transport_mode="transit",
    )

    plan = build_search_plan(request)

    assert plan.route_modes == ["driving", "transit"]
    assert plan.route_mode == "transit"


def test_food_search_executes_after_destination_resolution(monkeypatch):
    monkeypatch.setattr(
        "travel_agent.workflows.search.resolve_destination",
        lambda _destination: ToolResult.ok("geo", GeoPoint(latitude=35, longitude=139)),
    )
    monkeypatch.setattr(
        "travel_agent.workflows.search.search_food",
        lambda *_args: ToolResult.ok(
            "food",
            [
                PlaceCandidate(
                    name="Demo Sushi",
                    provider_id="sushi",
                    source="food",
                    category="restaurant",
                    cuisine="sushi",
                )
            ],
        ),
    )
    request = TripRequest(
        destination="Tokyo",
        days=2,
        requested_services=["food"],
    )

    results = execute_search_plan(build_search_plan(request))

    assert results.foods.status == "ok"
    assert results.foods.data[0].cuisine == "sushi"
    assert [record.tool_name for record in results.tool_calls] == [
        "resolve_destination",
        "search_food",
    ]


def test_transit_request_uses_transit_adapter_and_preserves_failure(monkeypatch):
    monkeypatch.setattr(
        "travel_agent.workflows.search.resolve_destination",
        lambda _destination: ToolResult.ok("geo", GeoPoint(latitude=35, longitude=139)),
    )
    monkeypatch.setattr(
        "travel_agent.workflows.search.search_attractions",
        lambda *_args: ToolResult.ok(
            "places",
            [
                PlaceCandidate(
                    name="A",
                    provider_id="a",
                    source="places",
                    point=GeoPoint(latitude=35.1, longitude=139.1),
                ),
                PlaceCandidate(
                    name="B",
                    provider_id="b",
                    source="places",
                    point=GeoPoint(latitude=35.2, longitude=139.2),
                ),
            ],
        ),
    )
    request = TripRequest(
        destination="Tokyo",
        days=1,
        requested_services=["activities", "guide"],
        transport_mode="transit",
    )

    results = execute_search_plan(build_search_plan(request))

    route_records = [
        record for record in results.tool_calls if "route" in record.tool_name
    ]
    assert [record.tool_name for record in route_records] == [
        "optimize_day_route",
        "optimize_transit_route",
    ]
    # Driving is the baseline and succeeds even when the optional transit
    # adapter is unavailable. The aggregate result therefore remains usable;
    # the individual transit failure is still visible in tool_calls.
    assert results.routes.status == "ok"
    assert [route.profile for route in results.routes.data] == ["driving"]
    assert any(
        record.tool_name == "optimize_transit_route"
        and record.status == "unavailable"
        for record in results.tool_calls
    )


def test_partial_failure_keeps_successful_attractions(monkeypatch):
    monkeypatch.setattr(
        "travel_agent.workflows.search.resolve_destination",
        lambda _destination: ToolResult.ok("geo", GeoPoint(latitude=35, longitude=139)),
    )
    monkeypatch.setattr(
        "travel_agent.workflows.search.get_weather_forecast",
        lambda *_args: ToolResult.unavailable("weather", "timeout", "slow"),
    )
    monkeypatch.setattr(
        "travel_agent.workflows.search.search_attractions",
        lambda *_args: ToolResult.ok(
            "places",
            [
                PlaceCandidate(
                    name="Museum",
                    provider_id="museum",
                    source="test",
                    point=GeoPoint(latitude=35.1, longitude=139.1),
                )
            ],
        ),
    )
    monkeypatch.setattr(
        "travel_agent.workflows.search.optimize_day_route",
        lambda *_args: ToolResult.skipped("route", "one stop"),
    )
    request = TripRequest(
        destination="Tokyo",
        start_date="2099-10-01",
        requested_services=["activities", "guide"],
    )

    results = execute_search_plan(build_search_plan(request))

    assert results.weather.status == "unavailable"
    assert [activity.name for activity in results.activities] == ["Museum"]
    names = [record.tool_name for record in results.tool_calls]
    assert names.count("resolve_destination") == 1
    assert names.count("get_weather_forecast") == 1
    assert "search_flights" not in names
    assert "search_hotels" not in names


def test_unexpected_tool_exception_becomes_recorded_unavailable(monkeypatch):
    monkeypatch.setattr(
        "travel_agent.workflows.search.resolve_destination",
        lambda _destination: (_ for _ in ()).throw(RuntimeError("provider crashed")),
    )
    request = TripRequest(
        destination="Tokyo",
        requested_services=["activities", "guide"],
    )

    results = execute_search_plan(build_search_plan(request))

    assert results.destination.status == "unavailable"
    assert results.destination.error_code == "unexpected_error"
    record = next(
        item for item in results.tool_calls if item.tool_name == "resolve_destination"
    )
    assert record.status == "unavailable"
    assert record.error_code == "unexpected_error"


def test_destination_failure_skips_preference_geocoding(monkeypatch):
    monkeypatch.setattr(
        "travel_agent.workflows.search.resolve_destination",
        lambda _destination: ToolResult.unavailable("geo", "timeout", "slow"),
    )

    def fail_if_called(*_args):
        raise AssertionError("Photon must not run without a destination point")

    monkeypatch.setattr(
        "travel_agent.workflows.search.resolve_preference_place",
        fail_if_called,
    )
    request = TripRequest(
        destination="Tokyo",
        requested_services=["activities", "guide"],
        preferences=["Tokyo Tower"],
        preference_details=[
            PreferenceSpec(
                label="Tokyo Tower",
                search_query="Tokyo Tower",
                kind="place",
            )
        ],
    )

    results = execute_search_plan(build_search_plan(request))

    assert results.preference_places[0].status == "skipped"
    assert any(
        record.tool_name == "resolve_preference_place" and record.status == "skipped"
        for record in results.tool_calls
    )
