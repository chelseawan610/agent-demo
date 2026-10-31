from datetime import date, timedelta

import pytest

from travel_agent.schemas.evidence import GeoPoint, PlaceCandidate
from travel_agent.tools.geocoding import resolve_destination
from travel_agent.tools.places import resolve_preference_place, search_attractions
from travel_agent.tools.routing import optimize_day_route
from travel_agent.tools.weather import get_weather_forecast


pytestmark = pytest.mark.live


def _skip_public_transient(result):
    """Public no-key services have rate limits and no CI-grade SLA."""

    if result.status == "unavailable" and result.error_category == "provider_transient":
        pytest.skip(f"Public provider temporarily unavailable: {result.error_code} {result.message}")


@pytest.mark.parametrize("city", ["Tokyo", "Seoul", "New York"])
def test_live_city_geocoding(city):
    result = resolve_destination(city)
    _skip_public_transient(result)
    assert result.status == "ok", result
    assert result.data.latitude
    assert result.data.longitude


def test_live_tokyo_weather_three_days():
    start = date.today() + timedelta(days=1)
    result = get_weather_forecast(
        GeoPoint(latitude=35.6895, longitude=139.69171), start.isoformat(), 3
    )
    _skip_public_transient(result)
    assert result.status == "ok", result
    assert len(result.data) == 3


@pytest.mark.parametrize(
    ("label", "query", "place_type"),
    [
        ("Tokyo Tower", "Tokyo Tower Tokyo", "landmark"),
        ("Victoria Harbour", "Victoria Harbour Hong Kong", "waterfront"),
        (
            "Petronas Twin Towers",
            "Petronas Twin Towers Kuala Lumpur",
            "landmark",
        ),
    ],
)
def test_live_photon_preferences(label, query, place_type):
    result = resolve_preference_place(label, query, place_name=label, place_type=place_type)
    _skip_public_transient(result)
    assert result.status == "ok", result
    assert result.data.point is not None


def test_live_tokyo_attractions():
    result = search_attractions(
        "Tokyo", GeoPoint(latitude=35.6895, longitude=139.69171), 5000, 5
    )
    if result.status == "unavailable":
        _skip_public_transient(result)
        pytest.skip(
            f"Overpass public service unavailable: {result.error_code} {result.message}"
        )
    assert result.status == "ok", result
    assert result.data


def test_live_tokyo_route():
    result = optimize_day_route(
        1,
        [
            PlaceCandidate(
                name="Tokyo Tower",
                provider_id="a",
                source="test",
                point=GeoPoint(latitude=35.6584491, longitude=139.745536),
            ),
            PlaceCandidate(
                name="Akihabara",
                provider_id="b",
                source="test",
                point=GeoPoint(latitude=35.6984, longitude=139.7731),
            ),
        ],
    )
    _skip_public_transient(result)
    assert result.status == "ok", result
    assert result.data.distance_meters > 0
