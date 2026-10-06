from datetime import date, timedelta

import pytest

from travel_agent.schemas.evidence import GeoPoint, PlaceCandidate
from travel_agent.tools.geocoding import resolve_destination
from travel_agent.tools.places import resolve_preference_place, search_attractions
from travel_agent.tools.routing import optimize_day_route
from travel_agent.tools.weather import get_weather_forecast


pytestmark = pytest.mark.live


@pytest.mark.parametrize("city", ["Tokyo", "Seoul", "New York"])
def test_live_city_geocoding(city):
    result = resolve_destination(city)
    assert result.status == "ok", result
    assert result.data.latitude
    assert result.data.longitude


def test_live_tokyo_weather_three_days():
    start = date.today() + timedelta(days=1)
    result = get_weather_forecast(
        GeoPoint(latitude=35.6895, longitude=139.69171), start.isoformat(), 3
    )
    assert result.status == "ok", result
    assert len(result.data) == 3


@pytest.mark.parametrize(
    ("label", "query"),
    [("Tokyo Tower", "Tokyo Tower Tokyo"), ("Anime", "Anime Tokyo")],
)
def test_live_photon_preferences(label, query):
    result = resolve_preference_place(label, query)
    assert result.status == "ok", result
    assert result.data.point is not None


def test_live_tokyo_attractions():
    result = search_attractions(
        "Tokyo", GeoPoint(latitude=35.6895, longitude=139.69171), 5000, 5
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
    assert result.status == "ok", result
    assert result.data.distance_meters > 0
