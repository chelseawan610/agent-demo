import os
import re
import unicodedata
from math import asin, cos, radians, sin, sqrt

from agent_framework import tool

from travel_agent.schemas.evidence import GeoPoint, PlaceCandidate, ToolResult
from travel_agent.schemas.request import PlaceType
from travel_agent.tools.http import ExternalToolError, HTTP_CLIENT


OSM_ATTRIBUTION = "© OpenStreetMap contributors, ODbL 1.0"
OVERPASS_ENDPOINTS = (
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
)
OVERPASS_TIMEOUT_SECONDS = float(os.getenv("TRAVEL_OVERPASS_TIMEOUT_SECONDS", "8"))
OVERPASS_ATTEMPTS = max(1, int(os.getenv("TRAVEL_OVERPASS_ATTEMPTS", "1")))


@tool(approval_mode="never_require")
def resolve_preference_place(
    label: str,
    search_query: str,
    destination_point: GeoPoint | None = None,
    place_name: str | None = None,
    place_type: PlaceType = "other",
) -> ToolResult[PlaceCandidate]:
    """Resolve a named entity using provider names, typed intent, and geography.

    Language interpretation belongs to Intake. Photon receives its search query
    and destination bias; this adapter checks provider names and OSM categories.
    A shared word or a nearby business is never sufficient identity evidence.
    """

    print(
        f"[tool] resolve_preference_place(label={label!r}, "
        f"search_query={search_query!r}, place_type={place_type!r})"
    )
    try:
        queries = list(dict.fromkeys(query for query in (search_query, place_name) if query))
        identity_names = [name for name in (place_name, label, search_query) if name]
        error_code = "not_found"
        relevant: list[tuple[int, int, dict]] = []
        for query in queries[:2]:
            params: dict[str, object] = {"q": query, "limit": 5, "lang": "en"}
            if destination_point is not None:
                params.update(lat=destination_point.latitude, lon=destination_point.longitude)
            payload = HTTP_CLIENT.get_json(
                "https://photon.komoot.io/api/", params, min_interval_seconds=1.0
            )
            features = payload["features"]
            if not isinstance(features, list):
                raise ValueError("Photon features must be a list")
            for item in features:
                # One broken candidate must not hide a valid later candidate.
                try:
                    point = _feature_point(item)
                    properties = item["properties"]
                    if not isinstance(properties, dict):
                        raise ValueError("Photon properties must be an object")
                    if destination_point is not None and _distance_km(destination_point, point) > 100:
                        error_code = "out_of_area"
                        continue
                    match_score = max(
                        (
                            _place_identity_score(provider_name, expected_name)
                            for provider_name in _provider_names(properties)
                            for expected_name in identity_names
                        ),
                        default=0,
                    )
                    if not match_score:
                        error_code = "not_relevant"
                        continue
                    if not _matches_place_type(properties, place_type):
                        error_code = "type_mismatch"
                        continue
                    relevant.append(
                        (match_score, _place_type_score(properties, place_type), item)
                    )
                except (KeyError, IndexError, TypeError, ValueError):
                    error_code = "invalid_response"
            if relevant:
                break
        if not relevant:
            return ToolResult[PlaceCandidate].unavailable(
                "Photon", error_code,
                f"No place matched the name, type and destination for: {label}. No similar business was substituted.",
                attribution=OSM_ATTRIBUTION,
            )
        # Prefer an exact provider name/alias over a weaker shortened-name
        # match before checking ambiguity. Never just take the nearest result.
        best_identity_score = max(score for score, _, _ in relevant)
        identity_candidates = [
            (type_score, item)
            for score, type_score, item in relevant
            if score == best_identity_score
        ]
        # A large OSM feature may be represented by several ways: a bridge can
        # appear once as ``bridge=yes`` and again as road segments. Prefer the
        # explicit landmark representation before applying ambiguity checks.
        best_type_score = max(type_score for type_score, _ in identity_candidates)
        candidates = [
            item
            for type_score, item in identity_candidates
            if type_score == best_type_score
        ]
        if any(
            _distance_km(_feature_point(candidates[0]), _feature_point(item)) > 1
            for item in candidates[1:]
        ):
            return ToolResult[PlaceCandidate].unavailable(
                "Photon", "ambiguous_place", f"Several distant places share this name; the exact location must be confirmed: {label}",
                attribution=OSM_ATTRIBUTION,
            )
        feature = min(
            candidates,
            key=lambda item: _distance_km(destination_point, _feature_point(item)) if destination_point else 0,
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
                provider_id=_osm_provider_id(osm_type, osm_id),
                source="Photon / OpenStreetMap",
                verified=True,
                route_eligible=_route_eligible(properties),
                description=properties.get("name", ""),
                opening_hours=properties.get("opening_hours"),
                website=properties.get("website") or properties.get("url"),
                address=_photon_address(properties),
                phone=properties.get("phone"),
                wheelchair_accessible=_optional_bool(properties.get("wheelchair")),
                venue_type=_venue_type(properties, properties.get("osm_value", "")),
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
            "Photon",
            code,
            str(error),
            attribution=OSM_ATTRIBUTION,
            retryable=(error.retryable if isinstance(error, ExternalToolError) else None),
            attempts=(error.attempts if isinstance(error, ExternalToolError) else None),
        )


def _feature_point(feature: dict) -> GeoPoint:
    longitude, latitude = feature["geometry"]["coordinates"]
    return GeoPoint(latitude=latitude, longitude=longitude)


def _normalized_place_name(name: str) -> str:
    """Compare identity with Unicode/case/spacing normalization, not keywords."""

    return "".join(character for character in unicodedata.normalize("NFKC", name).casefold() if character.isalnum())


def _provider_names(properties: dict) -> list[str]:
    """Use provider-supplied multilingual names and aliases, not invented ones."""

    return [
        name.strip()
        for key, value in properties.items()
        if key in {"name", "alt_name", "official_name", "short_name", "int_name", "loc_name"}
        or key.startswith("name:")
        for name in (value.split(";") if isinstance(value, str) else [])
        if name.strip()
    ]


def _place_identity_score(provider_name: str, expected_name: str) -> int:
    """Rank name evidence; this is a guard, not a semantic identity oracle.

    An exact provider name/alias is stronger than a conservative abbreviation.
    A shortened name needs at least two shared tokens and two-thirds coverage,
    and cannot introduce extra tokens. Unrelated synonyms remain unresolved
    rather than silently replacing the requested place. Geography and type are
    independent checks; no particular city or landmark is hard-coded here.
    """

    if _normalized_place_name(provider_name) == _normalized_place_name(expected_name):
        return 2
    provider_tokens = set(_name_tokens(provider_name))
    expected_tokens = set(_name_tokens(expected_name))
    return int(
        len(provider_tokens) >= 2
        and provider_tokens < expected_tokens
        and len(provider_tokens) / len(expected_tokens) >= 2 / 3
    )


def _osm_provider_id(osm_type: str, osm_id: object) -> str:
    """Photon and Overpass use different spellings for the same OSM types."""

    element_type = {"N": "node", "W": "way", "R": "relation"}.get(osm_type, osm_type)
    return f"{element_type}:{osm_id}"


def _matches_place_type(properties: dict, expected: PlaceType) -> bool:
    """Check machine-readable OSM metadata against Intake's semantic category."""

    key, value = properties.get("osm_key"), properties.get("osm_value")
    categories = {
        "waterfront": {("natural", "bay"), ("natural", "strait"), ("natural", "water"), ("natural", "beach"), ("waterway", "river"), ("waterway", "dock"), ("man_made", "pier"), ("leisure", "marina")},
        "museum": {("tourism", "museum"), ("tourism", "gallery")},
        "park": {("leisure", "park"), ("leisure", "garden"), ("leisure", "nature_reserve"), ("boundary", "national_park")},
        "restaurant": {("amenity", "restaurant"), ("amenity", "cafe"), ("amenity", "fast_food"), ("amenity", "food_court")},
        "hotel": {("tourism", "hotel"), ("tourism", "hostel"), ("tourism", "guest_house"), ("tourism", "motel")},
    }
    if expected in categories:
        return (key, value) in categories[expected]
    # Unknown metadata cannot prove a specific category. For broad landmark
    # requests, prevent a same-named business or housing development replacing it.
    return value not in {"restaurant", "cafe", "fast_food", "hotel", "hostel", "residential"}


def _place_type_score(properties: dict, expected: PlaceType) -> int:
    """Rank explicit provider types before treating same-name results as ambiguous."""

    key, value = properties.get("osm_key"), properties.get("osm_value")
    if expected != "landmark":
        return 1
    # These tags describe a landmark itself more directly than a road segment
    # carrying the same name. The set is generic; it does not name a city or
    # landmark and only uses provider metadata.
    if (key, value) in {
        ("bridge", "yes"),
        ("tourism", "attraction"),
        ("tourism", "viewpoint"),
        ("historic", "monument"),
        ("man_made", "tower"),
    }:
        return 2
    if key == "highway":
        return 0
    return 1


def _route_eligible(properties: dict) -> bool:
    """A water/area centroid is not a verified road-accessible entrance."""

    return (properties.get("osm_key"), properties.get("osm_value")) not in {
        ("natural", "bay"), ("natural", "strait"), ("natural", "water"),
        ("natural", "sea"), ("waterway", "river"), ("waterway", "dock"),
        ("boundary", "protected_area"),
    }


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


def _name_tokens(value: str) -> list[str]:
    """Tokenize provider names without assuming a particular user language."""

    return re.findall(r"[^\W_]+", unicodedata.normalize("NFKC", value).casefold())


def _venue_type(tags: dict, category: str) -> str:
    """Infer a cautious indoor/outdoor label from explicit OSM tags."""

    explicit = str(tags.get("indoor", "")).casefold()
    if explicit in {"yes", "true"}:
        return "indoor"
    if explicit in {"no", "false"}:
        return "outdoor"
    if category in {"museum", "gallery", "aquarium", "planetarium", "cinema", "theatre"}:
        return "indoor"
    if category in {"park", "garden", "beach", "viewpoint", "nature_reserve", "bay", "strait", "water", "river"}:
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
        provider_id=_osm_provider_id(element.get("type", "unknown"), element.get("id")),
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
                timeout_seconds=OVERPASS_TIMEOUT_SECONDS,
                attempts=OVERPASS_ATTEMPTS,
                backoff_base_seconds=0.5,
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
    venue_mix: str = "unspecified",
) -> ToolResult[list[PlaceCandidate]]:
    """Find named attractions and museums around a destination with Overpass."""

    print(
        f"[tool] search_attractions(destination={destination!r}, "
        f"radius_meters={radius_meters!r}, limit={limit!r})"
    )
    overpass_limit = max(limit * 3, limit)
    tourism_values = "attraction|museum|gallery|aquarium|viewpoint"
    if venue_mix == "indoor":
        tourism_values = "museum|gallery|aquarium"
    selectors = [
        f'nwr["tourism"~"^({tourism_values})$"]'
        f"(around:{radius_meters},{center.latitude},{center.longitude});"
    ]
    if venue_mix in {"balanced", "outdoor"}:
        selectors.append(
            'nwr["leisure"~"^(park|garden)$"]'
            f"(around:{radius_meters},{center.latitude},{center.longitude});"
        )
    query = (
        f"[out:json][timeout:{int(OVERPASS_TIMEOUT_SECONDS)}];"
        f"({''.join(selectors)});"
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
        if venue_mix in {"indoor", "outdoor"}:
            candidates = [item for item in candidates if item.venue_type == venue_mix]
        elif venue_mix == "balanced":
            # Apply before truncation so museums cannot crowd every known
            # outdoor candidate out of the provider's bounded result pool.
            indoor = [item for item in candidates if item.venue_type == "indoor"]
            outdoor = [item for item in candidates if item.venue_type == "outdoor"]
            other = [item for item in candidates if item.venue_type not in {"indoor", "outdoor"}]
            candidates = [
                pool[index]
                for index in range(max(len(indoor), len(outdoor)))
                for pool in (indoor, outdoor)
                if index < len(pool)
            ] + other
        return ToolResult[list[PlaceCandidate]].ok(
            "Overpass API", candidates[:limit], attribution=OSM_ATTRIBUTION
        )
    except (ExternalToolError, KeyError, TypeError, ValueError) as error:
        code = error.code if isinstance(error, ExternalToolError) else "invalid_response"
        return ToolResult[list[PlaceCandidate]].unavailable(
            "Overpass API",
            code,
            str(error),
            attribution=OSM_ATTRIBUTION,
            retryable=(error.retryable if isinstance(error, ExternalToolError) else None),
            attempts=(error.attempts if isinstance(error, ExternalToolError) else None),
        )


@tool(approval_mode="never_require")
def search_food(
    destination: str,
    center: GeoPoint,
    radius_meters: int = 5000,
    limit: int = 6,
    preferences: list[str] | None = None,
    preference_queries: list[str] | None = None,
) -> ToolResult[list[PlaceCandidate]]:
    """Find restaurants and cafes; prices and reservations are not inferred."""

    print(
        f"[tool] search_food(destination={destination!r}, "
        f"radius_meters={radius_meters!r}, limit={limit!r})"
    )
    overpass_limit = max(limit * 3, limit)
    query = (
        f"[out:json][timeout:{int(OVERPASS_TIMEOUT_SECONDS)}];"
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
        requested_queries = [
            query.strip()
            for query in [*(preferences or []), *(preference_queries or [])]
            if query and query.strip()
        ]
        if requested_queries:
            # OSM exposes cuisine as a structured tag. Only keep a candidate
            # when one of those tags overlaps a provider-friendly preference
            # query; a missing tag is treated as unknown, not as a match.
            candidates = [
                candidate
                for candidate in candidates
                if _matches_food_preference(candidate, requested_queries)
            ]
        return ToolResult[list[PlaceCandidate]].ok(
            "Overpass API", candidates[:limit], attribution=OSM_ATTRIBUTION
        )
    except (ExternalToolError, KeyError, TypeError, ValueError) as error:
        code = error.code if isinstance(error, ExternalToolError) else "invalid_response"
        return ToolResult[list[PlaceCandidate]].unavailable(
            "Overpass API",
            code,
            str(error),
            attribution=OSM_ATTRIBUTION,
            retryable=(error.retryable if isinstance(error, ExternalToolError) else None),
            attempts=(error.attempts if isinstance(error, ExternalToolError) else None),
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


def _matches_food_preference(
    candidate: PlaceCandidate,
    requested_queries: list[str],
) -> bool:
    """Match only explicit OSM cuisine tags to the typed food query."""

    if not candidate.cuisine:
        return False
    cuisine_tokens = set(_name_tokens(candidate.cuisine))
    requested_tokens = {
        token
        for query in requested_queries
        for token in _name_tokens(query)
    }
    return bool(cuisine_tokens & requested_tokens)
