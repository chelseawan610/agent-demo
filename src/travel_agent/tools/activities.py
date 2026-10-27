from collections.abc import Sequence
from datetime import date, timedelta
from math import cos, radians

from travel_agent.schemas.evidence import PlaceCandidate, ToolResult
from travel_agent.schemas.request import PreferenceSpec
from travel_agent.schemas.trip import ActivityOption
from travel_agent.tools.opening_hours import opening_status


def assign_activities(
    destination: str,
    days: int,
    preference_results: list[ToolResult[PlaceCandidate]],
    attraction_result: ToolResult[list[PlaceCandidate]] | None,
    *,
    preference_labels: list[str] | None = None,
    preference_details: Sequence[PreferenceSpec] | None = None,
    venue_mix: str = "unspecified",
    start_date: date | None = None,
    day_capacities: Sequence[int] | None = None,
    rain_probability_by_day: Sequence[int | None] | None = None,
) -> list[ActivityOption]:
    """Assign place evidence to feasible days with a dynamic daily capacity.

    The old implementation always allowed three places per day.  This version
    derives capacity from the number of candidates and the number of days,
    optionally limits travel days, skips places known to be closed, and favors
    geographically coherent groups.  It never treats missing opening hours as
    proof that a place is open.
    """

    if days < 1:
        return []
    preference_labels = preference_labels or []
    preferred: list[PlaceCandidate] = []
    if preference_details is not None:
        # The executor preserves the order of preference_details. Only a
        # concrete ``place`` can become a map stop. Semantic interests such
        # as venue_mix remain constraints and never become fake activities.
        for detail, result in zip(preference_details, preference_results):
            if detail.kind != "place":
                continue
            if result.status == "ok" and result.data is not None:
                preferred.append(result.data)
    else:
        # Backward-compatible direct callers can still pass provider results
        # without metadata. Failed lookups are never fake POIs.
        for result in preference_results:
            if result.status == "ok" and result.data is not None:
                preferred.append(result.data)

    attractions = (
        attraction_result.data
        if attraction_result is not None
        and attraction_result.status == "ok"
        and attraction_result.data is not None
        else []
    )
    if venue_mix in {"indoor", "outdoor"}:
        # A strict venue constraint needs positive provider evidence. Explicit
        # named places remain visible even if they conflict with this preference.
        attractions = [place for place in attractions if place.venue_type == venue_mix]
    ordered_places = _deduplicate([*preferred, *attractions])
    capacities = _build_capacities(days, len(ordered_places), day_capacities)

    buckets: list[list[PlaceCandidate]] = [[] for _ in range(days)]
    for place in ordered_places:
        eligible_days = _eligible_days(place, days, start_date)
        target_day = _choose_day(
            place,
            buckets,
            capacities,
            eligible_days,
            venue_mix=venue_mix,
            rain_probability_by_day=rain_probability_by_day,
        )
        if target_day is None:
            continue
        buckets[target_day].append(place)

    activities: list[ActivityOption] = []
    for day_number, bucket in enumerate(buckets, start=1):
        visit_date = (
            start_date + timedelta(days=day_number - 1)
            if start_date is not None
            else None
        )
        for place in bucket:
            status = opening_status(place.opening_hours, visit_date)
            activities.append(
                ActivityOption(
                    destination=destination,
                    day=day_number,
                    name=place.name,
                    description=place.description,
                    location=destination,
                    notes=_activity_note(place, status),
                    point=place.point,
                    provider_id=place.provider_id,
                    source=place.source,
                    verified=place.verified,
                    route_eligible=place.route_eligible,
                    opening_hours=place.opening_hours,
                    opening_status=status,
                    website=place.website,
                    estimated_visit_minutes=place.estimated_visit_minutes
                    or _default_visit_minutes(place.category),
                    venue_type=place.venue_type,
                    cuisine=place.cuisine,
                )
            )
    return activities


def _deduplicate(places: list[PlaceCandidate]) -> list[PlaceCandidate]:
    seen: set[str] = set()
    result: list[PlaceCandidate] = []
    for place in places:
        if place.provider_id in seen:
            continue
        seen.add(place.provider_id)
        result.append(place)
    return result


def _build_capacities(
    days: int,
    candidate_count: int,
    requested: Sequence[int] | None,
) -> list[int]:
    if requested is not None:
        values = list(requested)
        if len(values) != days:
            raise ValueError("day_capacities must contain one value per travel day")
        return [max(0, value) for value in values]
    if candidate_count == 0:
        return [0] * days
    # The cap is a safety limit, not a target. A larger candidate set may use
    # three places, while a small set naturally produces one or two per day.
    capacity = max(1, min(3, (candidate_count + days - 1) // days))
    return [capacity] * days


def _eligible_days(
    place: PlaceCandidate,
    days: int,
    start_date: date | None,
) -> list[int]:
    if not place.opening_hours or start_date is None:
        return list(range(days))
    statuses = [
        opening_status(
            place.opening_hours,
            start_date + timedelta(days=day_number),
        )
        for day_number in range(days)
    ]
    if "open" in statuses:
        return [index for index, status in enumerate(statuses) if status == "open"]
    # Unsupported expressions remain schedulable but visibly unverified.
    if "unknown" in statuses:
        return list(range(days))
    # A simple schedule says the venue is closed on every requested day.
    return []


def _choose_day(
    place: PlaceCandidate,
    buckets: list[list[PlaceCandidate]],
    capacities: list[int],
    eligible_days: list[int],
    *,
    venue_mix: str = "unspecified",
    rain_probability_by_day: Sequence[int | None] | None = None,
) -> int | None:
    available = [
        day for day in eligible_days if len(buckets[day]) < capacities[day]
    ]
    if not available:
        return None

    def score(day: int) -> tuple[int, int, int, float, int]:
        bucket = buckets[day]
        rain_penalty = _weather_penalty(
            place,
            day,
            rain_probability_by_day,
        )
        mix_penalty = _venue_mix_penalty(place, bucket, venue_mix)
        if not bucket or place.point is None:
            return (rain_penalty, mix_penalty, len(bucket), 0.0, day)
        distances = [
            _approx_distance_km(place, existing)
            for existing in bucket
            if existing.point is not None
        ]
        return (
            rain_penalty,
            mix_penalty,
            len(bucket),
            min(distances, default=0.0),
            day,
        )

    return min(available, key=score)


def _venue_mix_penalty(
    place: PlaceCandidate,
    bucket: list[PlaceCandidate],
    venue_mix: str,
) -> int:
    """Prefer complementary venue types without guessing unknown metadata."""

    if venue_mix != "balanced" or place.venue_type not in {"indoor", "outdoor"}:
        return 0
    if not bucket:
        # Pairing with an existing complementary venue is preferred to opening
        # another empty day when provider metadata is explicit.
        return 1
    known_types = {
        item.venue_type
        for item in bucket
        if item.venue_type in {"indoor", "outdoor"}
    }
    if not known_types:
        return 1
    opposite = "indoor" if place.venue_type == "outdoor" else "outdoor"
    if opposite in known_types:
        return 0
    return 2


def _weather_penalty(
    place: PlaceCandidate,
    day_index: int,
    rain_probability_by_day: Sequence[int | None] | None,
) -> int:
    """Prefer outdoor places on drier days without making weather mandatory.

    A missing forecast, an unknown venue type, or a mixed venue receives no
    penalty.  This keeps weather as a scheduling hint rather than a fabricated
    safety guarantee.  The index is zero-based because activity buckets are
    zero-based internally.
    """

    if not rain_probability_by_day or place.venue_type != "outdoor":
        return 0
    if day_index >= len(rain_probability_by_day):
        return 0
    probability = rain_probability_by_day[day_index]
    return 1 if probability is not None and probability >= 60 else 0


def _approx_distance_km(first: PlaceCandidate, second: PlaceCandidate) -> float:
    if first.point is None or second.point is None:
        return 0.0
    lat_scale = 111.0
    lon_scale = 111.0 * cos(
        radians((first.point.latitude + second.point.latitude) / 2)
    )
    delta_lat = (first.point.latitude - second.point.latitude) * lat_scale
    delta_lon = (first.point.longitude - second.point.longitude) * lon_scale
    return (delta_lat * delta_lat + delta_lon * delta_lon) ** 0.5


def _default_visit_minutes(category: str) -> int:
    if category in {"museum", "gallery"}:
        return 120
    return 90


def _activity_note(place: PlaceCandidate, status: str) -> str:
    if not place.route_eligible:
        return "该坐标为区域中心，并非已核验的到访入口；未用于道路或公共交通路线。"
    if status == "open" and place.opening_hours:
        return (
            f"营业时间：{place.opening_hours}（来自 "
            f"{place.opening_hours_source or place.source}）"
        )
    if status == "closed":
        return "该地点在计划日期显示为关闭，未安排到当天。"
    if place.opening_hours:
        return "已获得营业时间原文，但日期规则未能安全解析，请以官网为准。"
    return "营业时间未核验，请以官方信息为准。"
