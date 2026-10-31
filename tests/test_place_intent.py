"""Contracts between model intent, provider evidence, activities, and routing."""

import pytest
from pydantic import ValidationError

from travel_agent.schemas.evidence import GeoPoint, PlaceCandidate, ToolResult
from travel_agent.schemas.request import PreferenceSpec, TripRequest
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
    assert result.data.provider_id == "way:4"
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


def test_provider_official_short_name_can_match_canonical_landmark(monkeypatch):
    monkeypatch.setattr(
        places,
        "HTTP_CLIENT",
        PhotonClient(
            {
                "features": [
                    feature(
                        "Petronas Towers",
                        "tourism",
                        "attraction",
                        longitude=101.7112,
                        latitude=3.1580,
                    )
                ]
            }
        ),
    )

    result = places.resolve_preference_place(
        "双子塔",
        "Petronas Twin Towers Kuala Lumpur",
        GeoPoint(latitude=3.1412, longitude=101.6865),
        place_name="Petronas Twin Towers",
        place_type="landmark",
    )

    assert result.status == "ok"
    assert result.data is not None
    assert result.data.description == "Petronas Towers"


def test_explicit_landmark_type_wins_over_same_name_road_segments(monkeypatch):
    """Large landmarks can be split into several OSM ways with one name."""

    monkeypatch.setattr(
        places,
        "HTTP_CLIENT",
        PhotonClient(
            {
                "features": [
                    feature(
                        "Golden Gate Bridge",
                        "highway",
                        "motorway",
                        longitude=-122.4759,
                        latitude=37.8074,
                        identity=1,
                    ),
                    feature(
                        "Golden Gate Bridge",
                        "bridge",
                        "yes",
                        longitude=-122.4782,
                        latitude=37.8167,
                        identity=2,
                    ),
                ]
            }
        ),
    )

    result = places.resolve_preference_place(
        "Golden Gate Bridge",
        "Golden Gate Bridge San Francisco",
        GeoPoint(latitude=37.77493, longitude=-122.41942),
        place_name="Golden Gate Bridge",
        place_type="landmark",
    )

    assert result.status == "ok"
    assert result.data.provider_id == "way:2"
    assert result.data.venue_type == "outdoor"


def test_provider_name_with_extra_viewpoint_words_is_not_the_landmark(monkeypatch):
    monkeypatch.setattr(
        places,
        "HTTP_CLIENT",
        PhotonClient(
            {
                "features": [
                    feature(
                        "Petronas Towers Viewpoint 2",
                        "tourism",
                        "viewpoint",
                        longitude=101.7138,
                        latitude=3.1562,
                    )
                ]
            }
        ),
    )

    result = places.resolve_preference_place(
        "双子塔",
        "Petronas Twin Towers Kuala Lumpur",
        GeoPoint(latitude=3.1412, longitude=101.6865),
        place_name="Petronas Twin Towers",
        place_type="landmark",
    )

    assert result.status == "unavailable"
    assert result.error_code == "not_relevant"


@pytest.mark.parametrize(
    ("provider_name", "expected_name"),
    [("National Museum", "National History Museum"), ("Музей города", "Музей истории города")],
)
def test_short_name_matching_is_not_specific_to_one_landmark_or_language(
    monkeypatch, provider_name, expected_name,
):
    monkeypatch.setattr(places, "HTTP_CLIENT", PhotonClient({"features": [
        feature(provider_name, "tourism", "museum"),
    ]}))
    result = places.resolve_preference_place(
        expected_name, expected_name, place_name=expected_name, place_type="museum",
    )
    assert result.status == "ok"


def test_provider_alias_is_identity_evidence(monkeypatch):
    candidate = feature("Local Name", "tourism", "museum")
    candidate["properties"]["alt_name"] = "Historic Name;Requested Museum"
    monkeypatch.setattr(places, "HTTP_CLIENT", PhotonClient({"features": [candidate]}))
    result = places.resolve_preference_place(
        "Requested Museum", "Requested Museum", place_type="museum",
    )
    assert result.status == "ok"


def test_exact_identity_wins_over_distant_abbreviation(monkeypatch):
    monkeypatch.setattr(places, "HTTP_CLIENT", PhotonClient({"features": [
        feature("City Museum", "tourism", "museum", longitude=114.28, identity=1),
        feature("City History Museum", "tourism", "museum", identity=2),
    ]}))
    result = places.resolve_preference_place(
        "City History Museum", "City History Museum", place_type="museum",
    )
    assert result.status == "ok"
    assert result.data.provider_id == "way:2"


def test_photon_and_overpass_identity_deduplicates_before_scheduling(monkeypatch):
    monkeypatch.setattr(places, "HTTP_CLIENT", PhotonClient({"features": [
        feature("City Museum", "tourism", "museum", identity=2),
    ]}))
    resolved = places.resolve_preference_place("Museum", "City Museum", place_type="museum")
    overpass = places._place_from_element({
        "type": "way", "id": 2, "center": {"lat": 22.29, "lon": 114.18},
        "tags": {"name": "City Museum", "tourism": "museum"},
    }, "museum")
    assert resolved.data.provider_id == overpass.provider_id
    activities = assign_activities("City", 2, [resolved], ToolResult.ok("Overpass", [overpass]))
    assert len(activities) == 1


def test_island_candidates_are_outdoor_but_not_road_route_stops():
    candidate = places._place_from_element(
        {
            "type": "relation",
            "id": 42,
            "center": {"lat": 37.8267, "lon": -122.4227},
            "tags": {"name": "Alcatraz Island", "natural": "islet"},
        },
        "attraction",
    )

    assert candidate is not None
    assert candidate.venue_type == "outdoor"
    assert candidate.route_eligible is False


def test_photon_place_islet_is_not_a_road_route_stop(monkeypatch):
    monkeypatch.setattr(
        places,
        "HTTP_CLIENT",
        PhotonClient(
            {
                "features": [
                    {
                        "properties": {
                            "name": "Alcatraz Island",
                            "osm_key": "place",
                            "osm_value": "islet",
                            "osm_type": "R",
                            "osm_id": 42,
                        },
                        "geometry": {"coordinates": [-122.4227, 37.8267]},
                    }
                ]
            }
        ),
    )
    result = places.resolve_preference_place(
        "Alcatraz Island",
        "Alcatraz Island San Francisco",
        GeoPoint(latitude=37.77493, longitude=-122.41942),
        place_name="Alcatraz Island",
        place_type="landmark",
    )

    assert result.status == "ok"
    assert result.data.venue_type == "outdoor"
    assert result.data.route_eligible is False


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
    assert any("indoor/outdoor mix was not satisfied" in note for note in final.notes)
    assert "not a verified entrance" in render_trip_plan(final)


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
    assert any("indoor-only preference was not satisfied" in note for note in final.notes)


def test_a_place_pinned_to_a_day_lands_on_that_day():
    # "第3天想去双子塔" used to be flattened to "sometime during the trip":
    # the day was not representable, so the user could not tell it was lost.
    details = [
        PreferenceSpec(
            label="双子塔",
            search_query="Petronas",
            kind="place",
            place_name="Petronas Twin Towers",
            preferred_day=3,
        )
    ]
    resolved = [
        ToolResult.ok(
            "test",
            PlaceCandidate(
                name="Petronas Twin Towers", provider_id="petronas", source="test"
            ),
        )
    ]
    others = ToolResult.ok(
        "test",
        [
            PlaceCandidate(name=f"Spot{index}", provider_id=f"s{index}", source="test")
            for index in range(6)
        ],
    )

    activities = assign_activities(
        "Kuala Lumpur", 5, resolved, others, preference_details=details
    )
    pinned = [item for item in activities if item.name == "Petronas Twin Towers"]

    assert [item.day for item in pinned] == [3]


def test_a_pinned_day_outside_the_trip_is_ignored_rather_than_dropped():
    details = [
        PreferenceSpec(
            label="双子塔",
            search_query="Petronas",
            kind="place",
            place_name="Petronas Twin Towers",
            preferred_day=9,
        )
    ]
    resolved = [
        ToolResult.ok(
            "test",
            PlaceCandidate(
                name="Petronas Twin Towers", provider_id="petronas", source="test"
            ),
        )
    ]

    activities = assign_activities(
        "Kuala Lumpur", 3, resolved, None, preference_details=details
    )

    assert [item.name for item in activities] == ["Petronas Twin Towers"]
    assert 1 <= activities[0].day <= 3


def test_an_unresolved_named_place_is_reported_as_unmet(monkeypatch):
    # The summary once claimed a trip was "built around" a landmark that no
    # provider could verify, and every invariant still passed.
    import travel_agent.workflows.search as workflow

    request = TripRequest(
        destination="Kuala Lumpur",
        days=3,
        requested_services=["activities", "guide"],
        preference_details=[
            {
                "label": "双子塔",
                "search_query": "Petronas Twin Towers",
                "kind": "place",
                "place_name": "Petronas Twin Towers",
                "place_type": "landmark",
            }
        ],
    )
    monkeypatch.setattr(
        workflow,
        "resolve_destination",
        lambda _: ToolResult.ok("test", GeoPoint(latitude=3.15, longitude=101.7)),
    )
    monkeypatch.setattr(
        workflow,
        "resolve_preference_place",
        lambda *a, **k: ToolResult.unavailable("Photon", "no_match", "未找到可核验的地点"),
    )
    monkeypatch.setattr(
        workflow,
        "search_attractions",
        lambda *a, **k: ToolResult.unavailable("Overpass API", "timeout", "timed out"),
    )

    final = build_deterministic_trip_plan(
        request, execute_search_plan(build_search_plan(request))
    )

    assert final.status == "partial"
    assert any("双子塔" in note and "could not be scheduled" in note for note in final.notes)
    assert any("No daily itinerary was produced" in note for note in final.notes)
