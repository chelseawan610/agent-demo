from agent_framework import tool

from travel_agent.schemas.evidence import PlaceCandidate, RouteSummary, ToolResult
from travel_agent.tools.http import ExternalToolError, HTTP_CLIENT


OSRM_ATTRIBUTION = "Routing: OSRM; map data © OpenStreetMap contributors"


@tool(approval_mode="never_require")
def optimize_day_route(
    day: int,
    places: list[PlaceCandidate],
) -> ToolResult[RouteSummary]:
    """Order a day's resolved stops and estimate a driving route with OSRM."""

    resolved = [place for place in places if place.point is not None]
    print(f"[tool] optimize_day_route(day={day!r}, stops={len(resolved)!r})")
    if len(resolved) < 2:
        return ToolResult[RouteSummary].skipped(
            "OSRM", "少于两个已定位地点，无法计算路线。"
        )
    coordinates = ";".join(
        f"{place.point.longitude},{place.point.latitude}" for place in resolved
    )
    try:
        payload = HTTP_CLIENT.get_json(
            f"https://router.project-osrm.org/trip/v1/driving/{coordinates}",
            {
                "roundtrip": "false",
                "source": "first",
                "destination": "last",
                "overview": "false",
                "steps": "false",
            },
        )
        if payload.get("code") != "Ok" or not payload.get("trips"):
            return ToolResult[RouteSummary].unavailable(
                "OSRM",
                "no_route",
                payload.get("message", "未找到可用道路路线。"),
                attribution=OSRM_ATTRIBUTION,
            )
        trip = payload["trips"][0]
        waypoints = payload.get("waypoints", [])
        ordered = sorted(
            enumerate(waypoints), key=lambda item: item[1].get("waypoint_index", item[0])
        )
        ordered_stops = [resolved[index].name for index, _ in ordered]
        return ToolResult[RouteSummary].ok(
            "OSRM",
            RouteSummary(
                day=day,
                ordered_stops=ordered_stops,
                distance_meters=trip["distance"],
                duration_seconds=trip["duration"],
            ),
            attribution=OSRM_ATTRIBUTION,
        )
    except (ExternalToolError, KeyError, IndexError, TypeError, ValueError) as error:
        code = error.code if isinstance(error, ExternalToolError) else "invalid_response"
        return ToolResult[RouteSummary].unavailable(
            "OSRM", code, str(error), attribution=OSRM_ATTRIBUTION
        )


@tool(approval_mode="never_require")
def optimize_transit_route(
    day: int,
    places: list[PlaceCandidate],
) -> ToolResult[RouteSummary]:
    """Return a transparent boundary until a GTFS/OTP provider is configured.

    OSRM has a driving profile, but its public endpoint is not a public-transit
    planner. This adapter deliberately refuses to turn driving minutes into
    subway or bus minutes. A future OpenTripPlanner/GTFS implementation can
    replace this function without changing SearchPlan or TripPlan.
    """

    resolved_count = sum(place.point is not None for place in places)
    print(f"[tool] optimize_transit_route(day={day!r}, stops={resolved_count!r})")
    if resolved_count < 2:
        return ToolResult[RouteSummary].skipped(
            "OpenTripPlanner adapter", "少于两个已定位地点，无需计算公共交通路线。"
        )
    return ToolResult[RouteSummary].unavailable(
        "OpenTripPlanner adapter",
        "not_configured",
        "公共交通路线需要配置带 GTFS 数据的 OpenTripPlanner 服务；当前未伪造公交时间。",
    )
