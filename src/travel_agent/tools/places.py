from math import asin, cos, radians, sin, sqrt

from agent_framework import tool

from travel_agent.schemas.evidence import GeoPoint, PlaceCandidate, ToolResult
from travel_agent.tools.http import ExternalToolError, HTTP_CLIENT


OSM_ATTRIBUTION = "© OpenStreetMap contributors, ODbL 1.0"
OVERPASS_ENDPOINTS = (
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
)


@tool(approval_mode="never_require")
def resolve_preference_place(
    label: str,
    search_query: str,
    destination_point: GeoPoint | None = None,
) -> ToolResult[PlaceCandidate]:
    """Resolve one user preference with Photon's OpenStreetMap geocoder."""

    print(
        f"[tool] resolve_preference_place(label={label!r}, "
        f"search_query={search_query!r})"
    )
    try:
        payload = HTTP_CLIENT.get_json(
            "https://photon.komoot.io/api/",
            {"q": search_query, "limit": 5, "lang": "en"},
            min_interval_seconds=1.0,
        )
        features = payload.get("features", [])
        if not features:
            short_query = _short_place_query(search_query)
            if short_query != search_query:
                payload = HTTP_CLIENT.get_json(
                    "https://photon.komoot.io/api/",
                    {"q": short_query, "limit": 5, "lang": "en"},
                    min_interval_seconds=1.0,
                )
                features = payload.get("features", [])
            if not features:
                return ToolResult[PlaceCandidate].unavailable(
                    "Photon",
                    "not_found",
                    f"未定位偏好地点：{label}",
                    attribution=OSM_ATTRIBUTION,
                )
        if destination_point is None:
            feature = features[0]
        else:
            nearby = [
                item
                for item in features
                if _distance_km(
                    destination_point,
                    _feature_point(item),
                ) <= 100
            ]
            if not nearby:
                return ToolResult[PlaceCandidate].unavailable(
                    "Photon",
                    "out_of_area",
                    f"未找到位于目的地附近的偏好地点：{label}",
                    attribution=OSM_ATTRIBUTION,
                )
            relevant = [
                item
                for item in nearby
                if _query_match_score(item, search_query) is not None
            ]
            short_query = _short_place_query(search_query)
            if not relevant and short_query != search_query:
                # Photon can rank a long "place in city" query poorly. Retry
                # the named place itself before declaring the preference
                # unavailable; this remains bounded to one provider retry.
                payload = HTTP_CLIENT.get_json(
                    "https://photon.komoot.io/api/",
                    {"q": short_query, "limit": 5, "lang": "en"},
                    min_interval_seconds=1.0,
                )
                features = payload.get("features", [])
                nearby = [
                    item
                    for item in features
                    if _distance_km(
                        destination_point,
                        _feature_point(item),
                    ) <= 100
                ]
                relevant = [
                    item
                    for item in nearby
                    if _query_match_score(item, short_query) is not None
                ]
            if not relevant:
                return ToolResult[PlaceCandidate].unavailable(
                    "Photon",
                    "not_relevant",
                    f"目的地附近没有名称匹配的偏好地点：{label}",
                    attribution=OSM_ATTRIBUTION,
                )
            nearby = relevant
            feature = min(
                nearby,
                key=lambda item: (
                    _query_match_score(item, search_query),
                    _distance_km(destination_point, _feature_point(item)),
                ),
            )
        properties = feature.get("properties", {})
        longitude, latitude = feature["geometry"]["coordinates"]
        osm_type = properties.get("osm_type", "unknown")
        osm_id = properties.get("osm_id", label)
        return ToolResult[PlaceCandidate].ok(
            "Photon",
            PlaceCandidate(
                name=label,
                category=properties.get("osm_value") or properties.get("osm_key") or "place",
                point=GeoPoint(latitude=latitude, longitude=longitude),
                provider_id=f"{osm_type}:{osm_id}",
                source="Photon / OpenStreetMap",
                verified=True,
                description=properties.get("name", ""),
                opening_hours=properties.get("opening_hours"),
                website=properties.get("website") or properties.get("url"),
                address=_photon_address(properties),
                phone=properties.get("phone"),
                wheelchair_accessible=_optional_bool(properties.get("wheelchair")),
                opening_hours_source=(
                    properties.get("source:opening_hours")
                    or properties.get("opening_hours:url")
                ),
            ),
            attribution=OSM_ATTRIBUTION,
        )
    except (ExternalToolError, KeyError, IndexError, TypeError, ValueError) as error:
        code = error.code if isinstance(error, ExternalToolError) else "invalid_response"
        return ToolResult[PlaceCandidate].unavailable(
            "Photon", code, str(error), attribution=OSM_ATTRIBUTION
        )


def _feature_point(feature: dict) -> GeoPoint:
    longitude, latitude = feature["geometry"]["coordinates"]
    return GeoPoint(latitude=latitude, longitude=longitude)


def _optional_bool(value: object) -> bool | None:
    if value is None:
        return None
    normalized = str(value).casefold()
    if normalized in {"yes", "true", "1"}:
        return True
    if normalized in {"no", "false", "0"}:
        return False
    return None


def _photon_address(properties: dict) -> str | None:
    parts = [
        properties.get("housenumber"),
        properties.get("street"),
        properties.get("district"),
        properties.get("city"),
    ]
    address = " ".join(str(part) for part in parts if part)
    return address or None


def _distance_km(first: GeoPoint, second: GeoPoint) -> float:
    """Return great-circle distance for a provider-result sanity check."""

    lat1, lon1, lat2, lon2 = map(
        radians,
        (first.latitude, first.longitude, second.latitude, second.longitude),
    )
    delta_lat = lat2 - lat1
    delta_lon = lon2 - lon1
    value = (
        sin(delta_lat / 2) ** 2
        + cos(lat1) * cos(lat2) * sin(delta_lon / 2) ** 2
    )
    return 6371 * 2 * asin(sqrt(value))


def _query_match_score(feature: dict, search_query: str) -> int | None:
    """Prefer a named POI match over a nearby place sharing one word."""

    properties = feature.get("properties", {})
    haystack = " ".join(
        str(properties.get(key, ""))
        for key in ("name", "name:en", "name:local")
    ).casefold()
    stop_words = {"the", "in", "of", "and", "at"}
    tokens = [
        token.casefold()
        for token in search_query.replace(",", " ").split()
        if len(token) > 2 and token.casefold() not in stop_words
    ]
    if not tokens:
        return None
    if len(tokens) == 1:
        return 0 if haystack.startswith(tokens[0]) else None
    adjacent_phrases = [
        " ".join(tokens[index : index + 2])
        for index in range(len(tokens) - 1)
    ]
    if any(phrase in haystack for phrase in adjacent_phrases):
        return 0
    if haystack.startswith(tokens[0]):
        return 1
    return None


def _short_place_query(search_query: str) -> str:
    """Strip provider qualifiers so a named POI can be retried once."""

    stop_words = {"the", "in", "of", "and", "at"}
    tokens = [
        token
        for token in search_query.replace(",", " ").split()
        if token.casefold() not in stop_words
    ]
    return " ".join(tokens[:2]) if len(tokens) > 1 else search_query


def _venue_type(tags: dict, category: str) -> str:
    """Infer a cautious indoor/outdoor label from explicit OSM tags."""

    explicit = str(tags.get("indoor", "")).casefold()
    if explicit in {"yes", "true"}:
        return "indoor"
    if explicit in {"no", "false"}:
        return "outdoor"
    if category in {"museum", "gallery", "aquarium", "planetarium", "cinema", "theatre"}:
        return "indoor"
    if category in {"park", "garden", "beach", "viewpoint", "nature_reserve"}:
        return "outdoor"
    if category in {"restaurant", "cafe", "fast_food", "food_court"}:
        outdoor_seating = str(tags.get("outdoor_seating", "")).casefold()
        if outdoor_seating in {"yes", "true"}:
            return "mixed"
        if outdoor_seating in {"no", "false"}:
            return "indoor"
        return "unknown"
    return "unknown"


def _place_from_element(element: dict, default_category: str) -> PlaceCandidate | None:
    tags = element.get("tags", {})
    name = tags.get("name:en") or tags.get("name") or tags.get("name:ja")
    point_data = element if "lat" in element else element.get("center", {})
    if not name or "lat" not in point_data or "lon" not in point_data:
        return None
    category = (
        tags.get("tourism")
        or tags.get("amenity")
        or tags.get("leisure")
        or default_category
    )
    return PlaceCandidate(
        name=name,
        category=category,
        point=GeoPoint(latitude=point_data["lat"], longitude=point_data["lon"]),
        provider_id=f"{element.get('type', 'unknown')}:{element.get('id')}",
        source="Overpass / OpenStreetMap",
        verified=True,
        description=tags.get("description:en") or tags.get("description", ""),
        opening_hours=tags.get("opening_hours"),
        website=tags.get("website") or tags.get("contact:website"),
        address=_osm_address(tags),
        phone=tags.get("phone") or tags.get("contact:phone"),
        wheelchair_accessible=_optional_bool(tags.get("wheelchair")),
        opening_hours_source=(
            tags.get("source:opening_hours") or tags.get("opening_hours:url")
        ),
        venue_type=_venue_type(tags, category),
        cuisine=tags.get("cuisine"),
    )


def _fetch_overpass(query: str) -> dict:
    """Query a public Overpass endpoint with one bounded endpoint fallback."""

    payload = None
    last_error: ExternalToolError | None = None
    for endpoint in OVERPASS_ENDPOINTS:
        try:
            payload = HTTP_CLIENT.get_json(
                endpoint,
                {"data": query},
                min_interval_seconds=1.0,
            )
            break
        except ExternalToolError as error:
            last_error = error
    if payload is None:
        raise last_error or ExternalToolError(
            "no_overpass_endpoint", "all Overpass endpoints failed"
        )
    return payload


@tool(approval_mode="never_require")
def search_attractions(
    destination: str,
    center: GeoPoint,
    radius_meters: int = 8000,
    limit: int = 9,
) -> ToolResult[list[PlaceCandidate]]:
    """Find named attractions and museums around a destination with Overpass."""

    print(
        f"[tool] search_attractions(destination={destination!r}, "
        f"radius_meters={radius_meters!r}, limit={limit!r})"
    )
    overpass_limit = max(limit * 3, limit)
    query = (
        "[out:json][timeout:20];"
        f'nwr["tourism"~"^(attraction|museum|gallery)$"]'
        f"(around:{radius_meters},{center.latitude},{center.longitude});"
        f"out center tags {overpass_limit};"
    )
    try:
        payload = _fetch_overpass(query)
        candidates: list[PlaceCandidate] = []
        seen: set[str] = set()
        for element in payload.get("elements", []):
            candidate = _place_from_element(element, "attraction")
            if candidate is None:
                continue
            provider_id = candidate.provider_id
            if provider_id in seen:
                continue
            seen.add(provider_id)
            candidates.append(candidate)
        # Documented places are generally more useful than anonymous map objects.
        documented_ids = {
            f"{item.get('type', 'unknown')}:{item.get('id')}"
            for item in payload.get("elements", [])
            if item.get("tags", {}).get("wikidata")
            or item.get("tags", {}).get("wikipedia")
        }
        candidates.sort(key=lambda item: item.provider_id not in documented_ids)
        return ToolResult[list[PlaceCandidate]].ok(
            "Overpass API", candidates[:limit], attribution=OSM_ATTRIBUTION
        )
    except (ExternalToolError, KeyError, TypeError, ValueError) as error:
        code = error.code if isinstance(error, ExternalToolError) else "invalid_response"
        return ToolResult[list[PlaceCandidate]].unavailable(
            "Overpass API", code, str(error), attribution=OSM_ATTRIBUTION
        )


@tool(approval_mode="never_require")
def search_food(
    destination: str,
    center: GeoPoint,
    radius_meters: int = 5000,
    limit: int = 6,
) -> ToolResult[list[PlaceCandidate]]:
    """Find restaurants and cafes; prices and reservations are not inferred."""

    print(
        f"[tool] search_food(destination={destination!r}, "
        f"radius_meters={radius_meters!r}, limit={limit!r})"
    )
    overpass_limit = max(limit * 3, limit)
    query = (
        "[out:json][timeout:20];"
        f'nwr["amenity"~"^(restaurant|cafe|fast_food|food_court)$"]'
        f"(around:{radius_meters},{center.latitude},{center.longitude});"
        f"out center tags {overpass_limit};"
    )
    try:
        payload = _fetch_overpass(query)
        candidates: list[PlaceCandidate] = []
        seen: set[str] = set()
        for element in payload.get("elements", []):
            candidate = _place_from_element(element, "restaurant")
            if candidate is None or candidate.provider_id in seen:
                continue
            seen.add(candidate.provider_id)
            candidates.append(candidate)
        documented_ids = {
            f"{item.get('type', 'unknown')}:{item.get('id')}"
            for item in payload.get("elements", [])
            if item.get("tags", {}).get("wikidata")
            or item.get("tags", {}).get("wikipedia")
        }
        candidates.sort(key=lambda item: item.provider_id not in documented_ids)
        return ToolResult[list[PlaceCandidate]].ok(
            "Overpass API", candidates[:limit], attribution=OSM_ATTRIBUTION
        )
    except (ExternalToolError, KeyError, TypeError, ValueError) as error:
        code = error.code if isinstance(error, ExternalToolError) else "invalid_response"
        return ToolResult[list[PlaceCandidate]].unavailable(
            "Overpass API", code, str(error), attribution=OSM_ATTRIBUTION
        )


def _osm_address(tags: dict) -> str | None:
    parts = [
        tags.get("addr:housenumber"),
        tags.get("addr:street"),
        tags.get("addr:district"),
        tags.get("addr:city"),
    ]
    address = " ".join(str(part) for part in parts if part)
    return address or None
