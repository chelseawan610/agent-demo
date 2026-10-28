from datetime import date
from unittest.mock import patch

from travel_agent.tools.activities import assign_activities
from travel_agent.schemas.evidence import GeoPoint, PlaceCandidate, ToolResult
from travel_agent.schemas.request import (
    ORIGIN,
    PreferenceSpec,
)
from travel_agent.tools.flights import search_flights
from travel_agent.tools.hotels import search_hotels
from travel_agent.schemas.trip import TripPlan
from travel_agent.app.output import DEMO_NOTICE, format_plan, render_trip_plan


def test_activity_assignment_preserves_days_and_real_sources():
    result = assign_activities(
        "东京",
        2,
        [
            ToolResult.ok(
                "Photon",
                PlaceCandidate(
                    name="东京塔",
                    provider_id="tower",
                    source="Photon / OpenStreetMap",
                    point=GeoPoint(latitude=35.6, longitude=139.7),
                ),
            )
        ],
        ToolResult.ok(
            "Overpass",
            [
                PlaceCandidate(
                    name="博物馆",
                    provider_id="museum",
                    source="Overpass / OpenStreetMap",
                    point=GeoPoint(latitude=35.7, longitude=139.8),
                )
            ],
        ),
    )

    assert [(item.day, item.name) for item in result] == [(1, "东京塔"), (2, "博物馆")]
    assert all(item.verified for item in result)


def test_activity_assignment_uses_candidate_count_instead_of_three_per_day():
    places = [
        PlaceCandidate(
            name=f"地点 {index}",
            provider_id=f"place-{index}",
            source="test",
        )
        for index in range(4)
    ]

    result = assign_activities(
        "东京",
        3,
        [],
        ToolResult.ok("test", places),
    )

    counts = [sum(item.day == day for item in result) for day in range(1, 4)]
    assert counts == [2, 1, 1]
    assert max(counts) < 3


def test_activity_assignment_skips_known_closed_places_and_keeps_unknown_visible():
    result = assign_activities(
        "东京",
        2,
        [],
        ToolResult.ok(
            "test",
            [
                PlaceCandidate(
                    name="闭馆地点",
                    provider_id="closed",
                    source="test",
                    opening_hours="Mo-Su off",
                ),
                PlaceCandidate(
                    name="营业时间未知",
                    provider_id="unknown",
                    source="test",
                ),
            ],
        ),
        start_date=date(2026, 10, 5),
    )

    assert [item.name for item in result] == ["营业时间未知"]
    assert result[0].opening_status == "unknown"


def test_activity_assignment_respects_explicit_day_capacities():
    places = [
        PlaceCandidate(
            name=f"地点 {index}",
            provider_id=f"place-{index}",
            source="test",
        )
        for index in range(4)
    ]

    result = assign_activities(
        "东京",
        3,
        [],
        ToolResult.ok("test", places),
        day_capacities=[1, 2, 1],
    )

    counts = [sum(item.day == day for item in result) for day in range(1, 4)]
    assert counts == [1, 2, 1]


def test_unknown_hours_never_make_a_known_closed_day_eligible():
    candidate = PlaceCandidate(name="Museum", provider_id="museum", source="test", opening_hours="complex")
    with patch("travel_agent.tools.activities.opening_status", side_effect=["closed", "unknown", "unknown"]):
        activities = assign_activities(
            "City", 2, [], ToolResult.ok("test", [candidate]), start_date=date(2026, 10, 5),
        )
    assert [activity.day for activity in activities] == [2]


def test_activity_assignment_prefers_outdoor_places_on_drier_days():
    places = [
        PlaceCandidate(
            name="Outdoor Park",
            provider_id="park",
            source="test",
            venue_type="outdoor",
        ),
        PlaceCandidate(
            name="Indoor Museum",
            provider_id="museum",
            source="test",
            venue_type="indoor",
        ),
    ]

    result = assign_activities(
        "Tokyo",
        2,
        [],
        ToolResult.ok("test", places),
        rain_probability_by_day=[80, 10],
        day_capacities=[1, 1],
    )

    assert [(item.day, item.name) for item in result] == [
        (1, "Indoor Museum"),
        (2, "Outdoor Park"),
    ]


def test_interest_constraint_is_not_emitted_as_a_fake_activity():
    result = assign_activities(
        "香港",
        2,
        [ToolResult.skipped("Photon", "室内外结合是行程约束")],
        ToolResult.ok(
            "Overpass",
            [
                PlaceCandidate(
                    name="Victoria Harbour",
                    provider_id="harbour",
                    source="Overpass / OpenStreetMap",
                    point=GeoPoint(latitude=22.28, longitude=114.17),
                )
            ],
        ),
        preference_details=[
            PreferenceSpec(
                label="室内外结合",
                search_query="indoor and outdoor attractions Hong Kong",
                kind="interest",
                planning_hint="venue_mix",
            )
        ],
        venue_mix="balanced",
    )

    assert [item.name for item in result] == ["Victoria Harbour"]
    assert all(item.name != "室内外结合" for item in result)


def test_balanced_venue_mix_pairs_known_indoor_and_outdoor_candidates():
    places = [
        PlaceCandidate(
            name="Outdoor Park",
            provider_id="park",
            source="test",
            venue_type="outdoor",
        ),
        PlaceCandidate(
            name="Indoor Museum",
            provider_id="museum",
            source="test",
            venue_type="indoor",
        ),
        PlaceCandidate(
            name="Outdoor Garden",
            provider_id="garden",
            source="test",
            venue_type="outdoor",
        ),
        PlaceCandidate(
            name="Indoor Gallery",
            provider_id="gallery",
            source="test",
            venue_type="indoor",
        ),
    ]

    result = assign_activities(
        "Hong Kong",
        2,
        [],
        ToolResult.ok("test", places),
        venue_mix="balanced",
        day_capacities=[2, 2],
    )

    by_day = {
        day: {item.venue_type for item in result if item.day == day}
        for day in (1, 2)
    }
    assert by_day == {1: {"indoor", "outdoor"}, 2: {"indoor", "outdoor"}}


def test_flight_search_returns_demo_options():
    result = search_flights("上海", "东京", "2026-10-01")

    assert len(result) == 2
    assert all(item.origin == "上海" for item in result)
    assert all(item.destination == "东京" for item in result)
    assert all(item.source_type == "demo" and item.is_live is False for item in result)


def test_hotel_search_preserves_request_constraints():
    result = search_hotels("东京", 2, "medium")

    assert len(result) == 2
    assert all(item.area for item in result)
    assert all(item.nights == 2 for item in result)
    assert all(item.price_per_night for item in result)
    assert all(item.source_type == "demo" and item.is_live is False for item in result)


def test_trip_plan_schema_accepts_a_structured_plan():
    plan = TripPlan(
        destination="东京",
        days=3,
        summary="演示行程",
        itinerary=[
            {
                "day": 1,
                "title": "浅草",
                "activities": [{"name": "浅草寺", "location": "浅草"}],
            }
        ],
    )

    assert plan.itinerary[0].day == 1
    assert plan.itinerary[0].activities[0].name == "浅草寺"
    assert plan.review is None


def test_trip_plan_marks_missing_information():
    plan = TripPlan(
        destination="东京",
        days=0,
        summary="需要补充信息",
        missing_information=[ORIGIN, "旅行日期"],
    )

    assert plan.status == "needs_clarification"


def test_trip_plan_can_show_options_without_a_selection():
    plan = TripPlan(
        destination="东京",
        days=3,
        summary="候选航班和酒店",
        missing_information=["可选航班尚未选择"],
    )

    assert plan.status == "complete"


def test_render_trip_plan_shows_clarification_state():
    plan = TripPlan(
        destination="东京",
        days=0,
        summary="请补充出发城市",
        missing_information=[ORIGIN],
    )

    result = render_trip_plan(plan)

    assert result.startswith("# More trip details needed")
    assert ORIGIN in result
    assert "0日旅行计划" not in result


def test_format_plan_adds_title_and_demo_notice():
    result = format_plan("### Daily Itinerary\n- Day 1")

    assert result.startswith("# Travel plan")
    assert DEMO_NOTICE in result


def test_format_plan_does_not_duplicate_demo_notice():
    result = format_plan(f"计划\n\n{DEMO_NOTICE}")

    assert result.count(DEMO_NOTICE) == 1


def test_markdown_renders_typed_weather_routes_and_links():
    plan = TripPlan(
        destination="东京",
        days=1,
        summary="真实工具演示",
        weather=[
            {
                "date": "2026-10-01",
                "weather_code": 2,
                "temperature_max_c": 22,
                "temperature_min_c": 14,
                "precipitation_probability_max": 35,
            }
        ],
        routes=[
            {
                "day": 1,
                "ordered_stops": ["东京塔", "秋叶原"],
                "distance_meters": 5900,
                "duration_seconds": 600,
            }
        ],
        search_links=[
            {
                "service": "flights",
                "provider": "Google Travel",
                "url": "https://example.test/flights",
            }
        ],
        food=[
            {
                "name": "Demo Sushi",
                "category": "restaurant",
                "provider_id": "sushi",
                "source": "Overpass / OpenStreetMap",
                "cuisine": "sushi; japanese",
                "venue_type": "mixed",
            }
        ],
    )

    markdown = render_trip_plan(plan)
    payload = plan.model_dump(mode="json")

    assert "14–22°C" in markdown
    assert "5.9 km" in markdown
    assert "Google Travel" in markdown
    assert "Demo Sushi" in markdown
    assert "cuisine sushi; japanese" in markdown
    assert payload["routes"][0]["distance_meters"] == 5900


def test_markdown_renders_activity_operating_hours_and_duration():
    plan = TripPlan(
        destination="东京",
        days=1,
        summary="营业时间演示",
        itinerary=[
            {
                "day": 1,
                "title": "地标",
                "activities": [
                    {
                        "name": "东京塔",
                        "source": "Overpass / OpenStreetMap",
                        "opening_hours": "Mo-Su 09:00-22:00",
                        "opening_status": "open",
                        "website": "https://example.test/tokyo-tower",
                        "venue_type": "mixed",
                        "cuisine": "",
                        "estimated_visit_minutes": 90,
                    }
                ],
            }
        ],
    )

    markdown = render_trip_plan(plan)

    assert "open Mo-Su 09:00-22:00" in markdown
    assert "about 90 min" in markdown
    assert "[website](https://example.test/tokyo-tower)" in markdown
    assert "indoor or outdoor" in markdown
