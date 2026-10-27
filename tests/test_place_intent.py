"""Contracts between model intent, provider evidence, activities, and routing."""

import pytest
from pydantic import ValidationError

from travel_agent.schemas.evidence import GeoPoint, PlaceCandidate, ToolResult
from travel_agent.schemas.request import TripRequest
from travel_agent.tools import places, routing
from travel_agent.tools.activities import assign_activities
from travel_agent.workflows.search import build_search_plan, execute_search_plan
from travel_agent.workflows.trip_planning import build_deterministic_trip_plan
from travel_agent.app.output import render_trip_plan


def feature(name, key, value, *, longitude=114.18, latitude=22.29, identity=1):
    return {
        "properties": {"name": name, "osm_key": key, "osm_value": value, "osm_type": "W", "osm_id": identity},
        "geometry": {"coordinates": [longitude, latitude]},
    }


class PhotonClient:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def get_json(self, url, params, **kwargs):
        self.calls.append((url, params))
        return self.responses[min(len(self.calls) - 1, len(self.responses) - 1)]


def test_harbour_identity_rejects_nearer_business_and_housing(monkeypatch):
    client = PhotonClient({"features": [
        feature("Victoria Harbour Supreme", "amenity", "restaurant", identity=2),
        feature("Victoria Harbour", "landuse", "residential", identity=3),
        feature("Victoria Harbour", "natural", "strait", identity=4),
    ]})
    monkeypatch.setattr(places, "HTTP_CLIENT", client)
    result = places.resolve_preference_place(
        "维多利亚港", "Victoria Harbour Hong Kong",
        GeoPoint(latitude=22.27832, longitude=114.17469),
        place_name="Victoria Harbour", place_type="waterfront",
    )
    assert result.status == "ok"
    assert result.data.provider_id == "W:4"
    assert result.data.category == "strait"
    assert result.data.venue_type == "outdoor"
    assert result.data.route_eligible is False
    assert client.calls[0][1]["lat"] == 22.27832
    assert client.calls[0][1]["lon"] == 114.17469
    assert len(client.calls) == 1


@pytest.mark.parametrize(("label", "name", "category", "place_type"), [
    ("多瑙河", "Danube", "river", "waterfront"),
    ("艺术博物馆", "Art Museum", "museum", "museum"),
    ("城市公园", "City Park", "park", "park"),
])
def test_same_name_wrong_category_is_rejected(monkeypatch, label, name, category, place_type):
    monkeypatch.setattr(places, "HTTP_CLIENT", PhotonClient({"features": [feature(name, "amenity", "restaurant")]}))
    result = places.resolve_preference_place(label, name, place_name=name, place_type=place_type)
    assert result.status == "unavailable"
    assert result.error_code == "type_mismatch"
    assert result.error_category == "provider_no_match"
    assert result.data is None


@pytest.mark.parametrize("name", ["Musée d’Orsay", "東京タワー", "Café Central"])
def test_identity_comparison_supports_unicode(monkeypatch, name):
    monkeypatch.setattr(places, "HTTP_CLIENT", PhotonClient({"features": [feature(name, "tourism", "attraction")]}))
    result = places.resolve_preference_place(name, name.upper(), place_name=name)
    assert result.status == "ok"


def test_named_restaurant_is_allowed_when_explicitly_requested(monkeypatch):
    monkeypatch.setattr(places, "HTTP_CLIENT", PhotonClient({"features": [feature("Harbour Cafe", "amenity", "cafe")]}))
    result = places.resolve_preference_place("Harbour Cafe", "Harbour Cafe", place_type="restaurant")
    assert result.status == "ok"


def test_canonical_retry_is_bounded_and_does_not_truncate_name(monkeypatch):
    client = PhotonClient({"features": []}, {"features": [feature("Palace of Fine Arts", "tourism", "attraction")]})
    monkeypatch.setattr(places, "HTTP_CLIENT", client)
    result = places.resolve_preference_place("艺术宫", "Palace of Fine Arts San Francisco", place_name="Palace of Fine Arts", place_type="landmark")
    assert result.status == "ok"
    assert [params["q"] for _, params in client.calls] == ["Palace of Fine Arts San Francisco", "Palace of Fine Arts"]


def test_distant_same_name_candidates_are_reported_as_ambiguous(monkeypatch):
    monkeypatch.setattr(places, "HTTP_CLIENT", PhotonClient({"features": [
        feature("City Museum", "tourism", "museum"),
        feature("City Museum", "tourism", "museum", longitude=114.28, identity=2),
    ]}))
    result = places.resolve_preference_place("City Museum", "City Museum", place_type="museum")
    assert result.status == "unavailable"
    assert result.error_code == "ambiguous_place"


def test_bad_candidate_does_not_hide_valid_candidate(monkeypatch):
    monkeypatch.setattr(places, "HTTP_CLIENT", PhotonClient({"features": [None, {"geometry": {"coordinates": [200, 100]}}, feature("Park", "leisure", "park")]}))
    assert places.resolve_preference_place("Park", "Park", place_type="park").status == "ok"


@pytest.mark.parametrize("point", [{"latitude": 91, "longitude": 0}, {"latitude": 0, "longitude": 181}, {"latitude": float("nan"), "longitude": 0}])
def test_invalid_provider_coordinates_fail_at_typed_boundary(point):
    with pytest.raises(ValidationError):
        GeoPoint(**point)


def test_area_centroids_are_never_sent_to_routing(monkeypatch):
    class UnexpectedClient:
        def get_json(self, *args, **kwargs):
            raise AssertionError("Only one road stop remains; no HTTP call is allowed")

    monkeypatch.setattr(routing, "HTTP_CLIENT", UnexpectedClient())
    candidates = [
        PlaceCandidate(name="Harbour", provider_id="harbour", source="test", point=GeoPoint(latitude=22.29, longitude=114.18), route_eligible=False),
        PlaceCandidate(name="Museum", provider_id="museum", source="test", point=GeoPoint(latitude=22.3, longitude=114.2)),
    ]
    assert routing.optimize_day_route(1, candidates).status == "skipped"
    assert routing.optimize_transit_route(1, candidates).status == "skipped"


def test_venue_mix_changes_provider_pool_before_truncation(monkeypatch):
    queries = []

    def fetch(query):
        queries.append(query)
        return {"elements": [
            {"type": "node", "id": index, "lat": 22.29, "lon": 114.18, "tags": {"name": str(index), **tags}}
            for index, tags in enumerate([
                {"tourism": "museum"}, {"tourism": "gallery"}, {"leisure": "park"}, {"leisure": "garden"}
            ])
        ]}

    monkeypatch.setattr(places, "_fetch_overpass", fetch)
    result = places.search_attractions("City", GeoPoint(latitude=22.29, longitude=114.18), limit=2, venue_mix="balanced")
    assert {item.venue_type for item in result.data} == {"indoor", "outdoor"}
    assert '"leisure"' in queries[0]


def test_indoor_only_requires_positive_metadata_for_generic_candidates():
    candidates = [PlaceCandidate(name=kind, provider_id=kind, source="test", venue_type=kind) for kind in ("indoor", "outdoor", "unknown")]
    activities = assign_activities("City", 1, [], ToolResult.ok("test", candidates), venue_mix="indoor")
    assert [item.name for item in activities] == ["indoor"]


def test_typed_preferences_survive_execution_and_final_assembly(monkeypatch):
    import travel_agent.workflows.search as workflow

    received = []
    request = TripRequest(
        destination="Hong Kong", days=2, requested_services=["activities", "guide"],
        preference_details=[
            {"label": "维多利亚港", "search_query": "Victoria Harbour Hong Kong", "kind": "place", "place_name": "Victoria Harbour", "place_type": "waterfront"},
            {"label": "室内外结合", "search_query": "indoor outdoor", "kind": "interest", "planning_hint": "venue_mix"},
        ],
    )
    monkeypatch.setattr(workflow, "resolve_destination", lambda _: ToolResult.ok("test", GeoPoint(latitude=22.29, longitude=114.18)))

    def resolve(label, query, point, **kwargs):
        received.append(kwargs)
        return ToolResult.ok("test", PlaceCandidate(name=label, category="strait", provider_id="harbour", source="test", point=point, route_eligible=False))

    monkeypatch.setattr(workflow, "resolve_preference_place", resolve)
    monkeypatch.setattr(workflow, "search_attractions", lambda *args, **kwargs: ToolResult.unavailable("test", "timeout", "test outage"))
    plan = build_search_plan(request)
    results = execute_search_plan(plan)
    final = build_deterministic_trip_plan(request, results)
    assert received == [{"place_name": "Victoria Harbour", "place_type": "waterfront"}]
    assert plan.attraction_query.venue_mix == "balanced"
    assert [item.name for item in results.activities] == ["维多利亚港"]
    assert final.preferences == ["维多利亚港", "室内外结合"]
    assert final.itinerary[0].activities[0].route_eligible is False
    assert final.status == "partial"
    assert final.routes == []
    assert any("室内外搭配尚未满足" in note for note in final.notes)
    assert "非到访入口" in render_trip_plan(final)


def test_indoor_only_reports_an_unmet_preference_instead_of_an_empty_plan(monkeypatch):
    # assign_activities hard-filters on a strict venue constraint. When the
    # filter removes every candidate, the plan must say so rather than look
    # like a complete itinerary that simply has nothing in it.
    import travel_agent.workflows.search as workflow

    request = TripRequest(
        destination="Hong Kong",
        days=2,
        requested_services=["activities", "guide"],
        preference_details=[
            {
                "label": "只要室内",
                "search_query": "indoor",
                "kind": "interest",
                "planning_hint": "indoor_only",
            }
        ],
    )
    monkeypatch.setattr(
        workflow,
        "resolve_destination",
        lambda _: ToolResult.ok("test", GeoPoint(latitude=22.29, longitude=114.18)),
    )
    monkeypatch.setattr(
        workflow,
        "search_attractions",
        lambda *args, **kwargs: ToolResult.ok(
            "test",
            [
                PlaceCandidate(
                    name="Park",
                    provider_id="park",
                    source="test",
                    venue_type="outdoor",
                )
            ],
        ),
    )
    final = build_deterministic_trip_plan(
        request, execute_search_plan(build_search_plan(request))
    )

    assert final.status == "partial"
    assert any("只要室内活动的偏好尚未满足" in note for note in final.notes)
