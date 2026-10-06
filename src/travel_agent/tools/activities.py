from collections.abc import Sequence
from datetime import date, timedelta
from math import cos, radians

from travel_agent.schemas.evidence import PlaceCandidate, ToolResult
from travel_agent.schemas.trip import ActivityOption
from travel_agent.tools.opening_hours import opening_status


def assign_activities(
    destination: str,
    days: int,
    preference_results: list[ToolResult[PlaceCandidate]],
    attraction_result: ToolResult[list[PlaceCandidate]] | None,
    *,
    preference_labels: list[str] | None = None,
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
    for index, result in enumerate(preference_results):
        if result.status == "ok" and result.data is not None:
            preferred.append(result.data)
        elif index < len(preference_labels):
            label = preference_labels[index]
            preferred.append(
                PlaceCandidate(
                    name=label,
                    category="user_preference",
                    provider_id=f"user-preference:{index}:{label}",
                    source="用户明确偏好（位置未核验）",
                    verified=False,
                )
            )

    attractions = (
        attraction_result.data
        if attraction_result is not None
        and attraction_result.status == "ok"
        and attraction_result.data is not None
        else []
    )
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
    rain_probability_by_day: Sequence[int | None] | None = None,
) -> int | None:
    available = [
        day for day in eligible_days if len(buckets[day]) < capacities[day]
    ]
    if not available:
        return None

    def score(day: int) -> tuple[int, int, float, int]:
        bucket = buckets[day]
        rain_penalty = _weather_penalty(
            place,
            day,
            rain_probability_by_day,
        )
        if not bucket or place.point is None:
            return (rain_penalty, len(bucket), 0.0, day)
        distances = [
            _approx_distance_km(place, existing)
            for existing in bucket
            if existing.point is not None
        ]
        return (rain_penalty, len(bucket), min(distances, default=0.0), day)

    return min(available, key=score)


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
