from datetime import date, timedelta
from urllib.error import HTTPError

import pytest

from travel_agent.schemas.evidence import GeoPoint, PlaceCandidate
from travel_agent.tools.http import ExternalToolError, JsonHttpClient


class FakeClient:
    def __init__(self, payload=None, error: ExternalToolError | None = None):
        self.payload = payload
        self.error = error

    def get_json(self, *_args, **_kwargs):
        if self.error:
            raise self.error
        return self.payload


def test_open_meteo_geocoding_normal_and_empty(monkeypatch):
    from travel_agent.tools import geocoding

    monkeypatch.setattr(
        geocoding,
        "HTTP_CLIENT",
        FakeClient({"results": [{"latitude": 35.6895, "longitude": 139.6917}]}),
    )
    assert geocoding.resolve_destination("Tokyo").status == "ok"

    monkeypatch.setattr(geocoding, "HTTP_CLIENT", FakeClient({"results": []}))
    result = geocoding.resolve_destination("missing")
    assert result.status == "unavailable"
    assert result.error_code == "not_found"


def test_open_meteo_weather_normal_and_invalid_response(monkeypatch):
    from travel_agent.tools import weather

    forecast_date = (date.today() + timedelta(days=1)).isoformat()
    payload = {
        "daily": {
            "time": [forecast_date],
            "weather_code": [2],
            "temperature_2m_max": [22.0],
            "temperature_2m_min": [14.0],
            "precipitation_probability_max": [35],
        }
    }
    monkeypatch.setattr(weather, "HTTP_CLIENT", FakeClient(payload))
    result = weather.get_weather_forecast(
        GeoPoint(latitude=35, longitude=139), forecast_date, 1
    )
    assert result.status == "ok"
    assert result.data[0].temperature_max_c == 22

    monkeypatch.setattr(weather, "HTTP_CLIENT", FakeClient({"bad": "shape"}))
    assert weather.get_weather_forecast(
        GeoPoint(latitude=35, longitude=139), forecast_date, 1
    ).status == "unavailable"


def test_open_meteo_weather_skips_dates_outside_forecast_window(monkeypatch):
    from travel_agent.tools import weather

    class UnexpectedClient:
        def get_json(self, *_args, **_kwargs):
            raise AssertionError("out-of-window weather must not call the API")

    monkeypatch.setattr(weather, "HTTP_CLIENT", UnexpectedClient())
    far_future = (date.today() + timedelta(days=30)).isoformat()

    result = weather.get_weather_forecast(
        GeoPoint(latitude=35, longitude=139), far_future, 3
    )

    assert result.status == "skipped"
    assert "16 天预报窗口" in result.message


def test_photon_and_overpass_normal_empty_and_timeout(monkeypatch):
    from travel_agent.tools import places

    photon_payload = {
        "features": [
            {
                "geometry": {"coordinates": [139.7455, 35.6584]},
                "properties": {
                    "name": "Tokyo Tower",
                    "osm_type": "R",
                    "osm_id": 1,
                    "osm_value": "tower",
                    "opening_hours": "Mo-Su 09:00-22:00",
                    "website": "https://example.test/tower",
                    "street": "Example Street",
                    "city": "Tokyo",
                    "wheelchair": "yes",
                },
            }
        ]
    }
    monkeypatch.setattr(places, "HTTP_CLIENT", FakeClient(photon_payload))
    resolved = places.resolve_preference_place("东京塔", "Tokyo Tower Tokyo")
    assert resolved.status == "ok"
    assert resolved.data.name == "东京塔"
    assert resolved.data.opening_hours == "Mo-Su 09:00-22:00"
    assert resolved.data.website == "https://example.test/tower"
    assert resolved.data.address == "Example Street Tokyo"
    assert resolved.data.wheelchair_accessible is True

    far_payload = {
        "features": [
            {
                "geometry": {"coordinates": [91.8031, 26.1232]},
                "properties": {"name": "unrelated result"},
            }
        ]
    }
    monkeypatch.setattr(places, "HTTP_CLIENT", FakeClient(far_payload))
    far = places.resolve_preference_place(
        "东京塔",
        "Tokyo Tower Tokyo",
        GeoPoint(latitude=35.6895, longitude=139.6917),
    )
    assert far.status == "unavailable"
    assert far.error_code == "out_of_area"

    monkeypatch.setattr(places, "HTTP_CLIENT", FakeClient({"features": []}))
    assert places.resolve_preference_place("未知", "missing").error_code == "not_found"

    overpass_payload = {
        "elements": [
            {
                "type": "node",
                "id": 2,
                "lat": 35.6,
                "lon": 139.7,
                "tags": {
                    "name": "Museum",
                    "tourism": "museum",
                    "wikidata": "Q2",
                    "opening_hours": "Tu-Su 10:00-18:00",
                    "website": "https://example.test/museum",
                    "addr:street": "Museum Road",
                    "addr:city": "Tokyo",
                    "wheelchair": "no",
                },
            }
        ]
    }
    monkeypatch.setattr(places, "HTTP_CLIENT", FakeClient(overpass_payload))
    found = places.search_attractions("Tokyo", GeoPoint(latitude=35, longitude=139), 1000, 3)
    assert found.status == "ok"
    assert found.data[0].provider_id == "node:2"
    assert found.data[0].opening_hours == "Tu-Su 10:00-18:00"
    assert found.data[0].website == "https://example.test/museum"
    assert found.data[0].address == "Museum Road Tokyo"
    assert found.data[0].wheelchair_accessible is False
    assert found.data[0].venue_type == "indoor"

    food_payload = {
        "elements": [
            {
                "type": "node",
                "id": 3,
                "lat": 35.61,
                "lon": 139.71,
                "tags": {
                    "name": "Demo Sushi",
                    "amenity": "restaurant",
                    "cuisine": "sushi; japanese",
                    "opening_hours": "Mo-Su 11:00-22:00",
                    "outdoor_seating": "yes",
                },
            }
        ]
    }
    monkeypatch.setattr(places, "HTTP_CLIENT", FakeClient(food_payload))
    food = places.search_food("Tokyo", GeoPoint(latitude=35, longitude=139), 1000, 3)
    assert food.status == "ok"
    assert food.data[0].category == "restaurant"
    assert food.data[0].cuisine == "sushi; japanese"
    assert food.data[0].venue_type == "mixed"

    monkeypatch.setattr(
        places, "HTTP_CLIENT", FakeClient(error=ExternalToolError("timeout", "slow"))
    )
    assert places.search_attractions(
        "Tokyo", GeoPoint(latitude=35, longitude=139)
    ).error_code == "timeout"


def test_osrm_normal_skipped_and_server_error(monkeypatch):
    from travel_agent.tools import routing

    places = [
        PlaceCandidate(
            name="A", provider_id="a", source="test", point=GeoPoint(latitude=1, longitude=2)
        ),
        PlaceCandidate(
            name="B", provider_id="b", source="test", point=GeoPoint(latitude=3, longitude=4)
        ),
    ]
    monkeypatch.setattr(
        routing,
        "HTTP_CLIENT",
        FakeClient(
            {
                "code": "Ok",
                "trips": [{"distance": 1000, "duration": 600}],
                "waypoints": [{"waypoint_index": 0}, {"waypoint_index": 1}],
            }
        ),
    )
    result = routing.optimize_day_route(1, places)
    assert result.status == "ok"
    assert result.data.ordered_stops == ["A", "B"]
    assert routing.optimize_day_route(1, places[:1]).status == "skipped"

    monkeypatch.setattr(
        routing, "HTTP_CLIENT", FakeClient(error=ExternalToolError("http_500", "down"))
    )
    assert routing.optimize_day_route(1, places).error_code == "http_500"


def test_transit_route_is_explicitly_unavailable_without_gtfs_adapter():
    from travel_agent.tools import routing

    result = routing.optimize_transit_route(
        1,
        [
            PlaceCandidate(
                name="A",
                provider_id="a",
                source="test",
                point=GeoPoint(latitude=1, longitude=2),
            ),
            PlaceCandidate(
                name="B",
                provider_id="b",
                source="test",
                point=GeoPoint(latitude=3, longitude=4),
            ),
        ],
    )

    assert result.status == "unavailable"
    assert result.error_code == "not_configured"


@pytest.mark.parametrize("code", ["timeout", "http_429", "http_500", "invalid_json"])
def test_providers_normalize_transport_failures(monkeypatch, code):
    from travel_agent.tools import geocoding

    monkeypatch.setattr(
        geocoding, "HTTP_CLIENT", FakeClient(error=ExternalToolError(code, "failed"))
    )
    result = geocoding.resolve_destination("Tokyo")
    assert result.status == "unavailable"
    assert result.error_code == code


def test_http_client_retries_retryable_status(monkeypatch):
    from travel_agent.tools import http

    calls = 0

    def failing_urlopen(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        raise HTTPError("url", 429, "busy", {}, None)

    monkeypatch.setattr(http, "urlopen", failing_urlopen)
    monkeypatch.setattr(http.time, "sleep", lambda _seconds: None)
    with pytest.raises(ExternalToolError) as error:
        JsonHttpClient(attempts=3).get_json("https://example.test/data")
    assert error.value.code == "http_429"
    assert calls == 3
