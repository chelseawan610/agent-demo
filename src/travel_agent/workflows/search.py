from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from time import perf_counter
from typing import Any, Callable

from travel_agent.schemas.evidence import (
    PlaceCandidate,
    RouteSummary,
    ToolCallRecord,
    ToolResult,
)
from travel_agent.schemas.request import TripRequest
from travel_agent.schemas.search import (
    ActivityQuery,
    AttractionQuery,
    DestinationQuery,
    FoodQuery,
    FlightLinkQuery,
    FlightQuery,
    HotelLinkQuery,
    HotelQuery,
    SearchPlan,
    SearchResults,
    WeatherQuery,
)
from travel_agent.tools.activities import assign_activities
from travel_agent.tools.adapters import TravelAdapters
from travel_agent.tools.geocoding import resolve_destination
from travel_agent.tools.places import (
    resolve_preference_place,
    search_attractions,
    search_food,
)
from travel_agent.tools.routing import optimize_day_route, optimize_transit_route
from travel_agent.tools.search_links import build_flight_search_link, build_hotel_search_link
from travel_agent.tools.weather import get_weather_forecast
from travel_agent.rag.retriever import KnowledgeRetriever


def _return_date(request: TripRequest) -> date | None:
    """Use one shared date rule for flight, hotel, and route planning."""

    if request.return_date:
        return request.return_date
    if not request.start_date:
        return None
    start = request.start_date
    if request.nights is not None:
        return start + timedelta(days=request.nights)
    if request.days is not None:
        return start + timedelta(days=max(request.days - 1, 0))
    return None


def _attraction_candidate_limit(days: int) -> int:
    """Fetch a small, duration-aware candidate pool instead of always nine."""

    return min(max(days * 2, days), 12)


def build_search_plan(request: TripRequest) -> SearchPlan:
    """Convert a validated request into the only allowed list of tool work."""

    if request.status != "ready":
        raise ValueError(
            "不能为未完成的 TripRequest 创建 SearchPlan；"
            f"仍缺少：{', '.join(request.missing_fields) or '未知字段'}"
        )

    services = set(request.requested_services)
    days = request.days or 1
    nights = request.nights
    if nights is None and "hotels" in services:
        nights = max(days - 1, 0) if request.days is not None else 1
    plan = SearchPlan()
    return_date = _return_date(request)

    if "flights" in services and request.origin and request.destination and request.start_date:
        plan.flight_queries.append(
            FlightQuery(
                origin=request.origin,
                destination=request.destination,
                departure_date=request.start_date,
            )
        )
        inferred_round_trip = len(services) > 1
        if return_date and (request.return_date or inferred_round_trip):
            plan.flight_queries.append(
                FlightQuery(
                    origin=request.destination,
                    destination=request.origin,
                    departure_date=return_date,
                )
            )
        plan.flight_link_query = FlightLinkQuery(
            origin=request.origin,
            destination=request.destination,
            departure_date=request.start_date,
            return_date=(
                return_date if request.return_date or inferred_round_trip else None
            ),
        )

    if (
        "hotels" in services
        and request.destination
        and request.start_date
        and nights is not None
        and nights > 0
    ):
        check_out = request.start_date + timedelta(days=nights)
        plan.hotel_query = HotelQuery(
            destination=request.destination,
            nights=nights,
            budget=request.budget,
        )
        plan.hotel_link_query = HotelLinkQuery(
            destination=request.destination,
            check_in_date=request.start_date,
            check_out_date=check_out,
        )

    activity_scope = bool(services & {"activities", "guide"})
    food_requested = "food" in services or any(
        detail.kind == "food" for detail in request.preference_details
    )
    if (activity_scope or food_requested) and request.destination:
        query_parts = [
            request.destination,
            *request.preferences,
            *(detail.search_query for detail in request.preference_details),
            *sorted(services),
        ]
        unique_parts = list(
            dict.fromkeys(part.strip() for part in query_parts if part.strip())
        )
        plan.knowledge_query = " ".join(unique_parts)
    if (activity_scope or food_requested) and request.destination:
        plan.destination_query = DestinationQuery(destination=request.destination)
    if activity_scope and request.destination:
        plan.attraction_query = AttractionQuery(
            destination=request.destination,
            days=days,
            limit=_attraction_candidate_limit(days),
        )
        plan.activity_query = ActivityQuery(
            destination=request.destination,
            days=days,
            start_date=request.start_date,
            preferences=request.preferences,
            preference_details=request.preference_details,
        )
        plan.route_enabled = True
        plan.route_modes = ["driving"]
        if request.transport_mode == "transit":
            plan.route_modes.append("transit")
        plan.route_mode = request.transport_mode or "driving"
        if request.start_date:
            plan.weather_query = WeatherQuery(start_date=request.start_date, days=days)
    if food_requested and request.destination:
        plan.food_query = FoodQuery(
            destination=request.destination,
            days=days,
            limit=min(max(days * 2, days), 12),
            preferences=[
                detail.label
                for detail in request.preference_details
                if detail.kind == "food"
            ],
        )
    return plan


def _recorded_call(
    results: SearchResults,
    tool_name: str,
    provider: str,
    inputs: dict[str, object],
    call: Callable[[], Any],
    *,
    run_id: str | None = None,
    trace_id: str | None = None,
) -> Any:
    started_at = datetime.now(timezone.utc)
    started = perf_counter()
    try:
        outcome = call()
    except Exception as error:
        # A provider adapter must not take down the whole workflow. Convert an
        # unexpected adapter failure into the same typed state used for timeout,
        # rate-limit, and invalid-response failures.
        outcome = ToolResult.unavailable(
            provider,
            "unexpected_error",
            f"{type(error).__name__}: {error}",
        )
    status = outcome.status if isinstance(outcome, ToolResult) else "ok"
    results.tool_calls.append(
        ToolCallRecord(
            run_id=run_id,
            trace_id=trace_id,
            tool_name=tool_name,
            started_at=started_at,
            inputs=inputs,
            status=status,
            elapsed_ms=round((perf_counter() - started) * 1000, 2),
            provider=outcome.provider if isinstance(outcome, ToolResult) else provider,
            error_code=outcome.error_code if isinstance(outcome, ToolResult) else None,
            message=outcome.message if isinstance(outcome, ToolResult) else None,
        )
    )
    return outcome


def _record_skipped(
    results: SearchResults,
    tool_name: str,
    provider: str,
    inputs: dict[str, object],
    *,
    run_id: str | None = None,
    trace_id: str | None = None,
) -> None:
    results.tool_calls.append(
        ToolCallRecord(
            run_id=run_id,
            trace_id=trace_id,
            tool_name=tool_name,
            started_at=datetime.now(timezone.utc),
            inputs=inputs,
            status="skipped",
            elapsed_ms=0,
            provider=provider,
        )
    )


def execute_search_plan(
    plan: SearchPlan,
    *,
    adapters: TravelAdapters | None = None,
    knowledge_retriever: KnowledgeRetriever | None = None,
    run_id: str | None = None,
    trace_id: str | None = None,
) -> SearchResults:
    """Execute a bounded plan in dependency order and preserve partial results."""

    results = SearchResults(route_modes=plan.route_modes)
    adapters = adapters or TravelAdapters.demo()

    if plan.knowledge_query and knowledge_retriever is not None:
        inputs = {
            "query": plan.knowledge_query,
            "top_k": plan.knowledge_top_k,
            "max_chars": plan.knowledge_max_chars,
        }
        results.knowledge = _recorded_call(
            results,
            "retrieve_knowledge",
            knowledge_retriever.provider,
            inputs,
            lambda: ToolResult.ok(
                knowledge_retriever.provider,
                knowledge_retriever.search(
                    plan.knowledge_query,
                    top_k=plan.knowledge_top_k,
                    max_chars=plan.knowledge_max_chars,
                ),
            ),
            run_id=run_id,
            trace_id=trace_id,
        )

    for query in plan.flight_queries:
        outcome = _recorded_call(
            results,
            "search_flights",
            "local demo adapter",
            query.model_dump(mode="json"),
            lambda query=query: adapters.flights.search(query),
            run_id=run_id,
            trace_id=trace_id,
        )
        if isinstance(outcome, ToolResult):
            if outcome.status == "ok" and outcome.data is not None:
                results.flights.extend(outcome.data)
        else:
            results.flights.extend(outcome)

    if plan.hotel_query is not None:
        query = plan.hotel_query
        outcome = _recorded_call(
            results,
            "search_hotels",
            "local demo adapter",
            query.model_dump(mode="json"),
            lambda: adapters.hotels.search(query),
            run_id=run_id,
            trace_id=trace_id,
        )
        if isinstance(outcome, ToolResult):
            if outcome.status == "ok" and outcome.data is not None:
                results.hotels.extend(outcome.data)
        else:
            results.hotels.extend(outcome)

    if plan.flight_link_query is not None:
        query = plan.flight_link_query
        outcome = _recorded_call(
            results,
            "build_flight_search_link",
            "Google Travel link builder",
            query.model_dump(mode="json"),
            lambda: build_flight_search_link(
                query.origin,
                query.destination,
                query.departure_date,
                query.return_date,
            ),
            run_id=run_id,
            trace_id=trace_id,
        )
        if outcome.status == "ok" and outcome.data is not None:
            results.search_links.append(outcome.data)

    if plan.hotel_link_query is not None:
        query = plan.hotel_link_query
        outcome = _recorded_call(
            results,
            "build_hotel_search_link",
            "Google Travel link builder",
            query.model_dump(mode="json"),
            lambda: build_hotel_search_link(
                query.destination, query.check_in_date, query.check_out_date
            ),
            run_id=run_id,
            trace_id=trace_id,
        )
        if outcome.status == "ok" and outcome.data is not None:
            results.search_links.append(outcome.data)

    if plan.destination_query is None:
        return results

    destination_query = plan.destination_query
    results.destination = _recorded_call(
        results,
        "resolve_destination",
        "Open-Meteo Geocoding",
        destination_query.model_dump(mode="json"),
        lambda: resolve_destination(destination_query.destination),
        run_id=run_id,
        trace_id=trace_id,
    )
    destination_point = (
        results.destination.data
        if results.destination.status == "ok" and results.destination.data is not None
        else None
    )
    if destination_point is None:
        if plan.weather_query is not None:
            results.weather = ToolResult.skipped(
                "Open-Meteo Forecast", "目的地坐标不可用，跳过天气。"
            )
            _record_skipped(
                results,
                "get_weather_forecast",
                "Open-Meteo Forecast",
                plan.weather_query.model_dump(mode="json"),
                run_id=run_id,
                trace_id=trace_id,
            )
        results.attractions = ToolResult.skipped(
            "Overpass API", "目的地坐标不可用，跳过景点搜索。"
        )
        if plan.attraction_query is not None:
            _record_skipped(
                results,
                "search_attractions",
                "Overpass API",
                plan.attraction_query.model_dump(mode="json"),
                run_id=run_id,
                trace_id=trace_id,
            )

    if plan.weather_query is not None and destination_point is not None:
        query = plan.weather_query
        results.weather = _recorded_call(
            results,
            "get_weather_forecast",
            "Open-Meteo Forecast",
            {**query.model_dump(mode="json"), "point": destination_point.model_dump()},
            lambda: get_weather_forecast(
                destination_point, query.start_date, query.days
            ),
            run_id=run_id,
            trace_id=trace_id,
        )

    if plan.activity_query is not None:
        for preference in plan.activity_query.preference_details:
            if destination_point is None:
                outcome = ToolResult[PlaceCandidate].skipped(
                    "Photon",
                    f"{preference.label} 的目的地坐标不可用，跳过地点解析。",
                )
                results.preference_places.append(outcome)
                _record_skipped(
                    results,
                    "resolve_preference_place",
                    "Photon",
                    preference.model_dump(mode="json"),
                    run_id=run_id,
                    trace_id=trace_id,
                )
                continue
            elif preference.kind != "place":
                # Interests such as "anime" or "food" are semantic filters,
                # not a single map pin. Keep them as explicit unverified
                # preferences instead of accepting an unrelated POI match.
                outcome = ToolResult[PlaceCandidate].skipped(
                    "Photon",
                    f"{preference.label} 是兴趣偏好，不按单一地点解析。",
                )
                results.preference_places.append(outcome)
                _record_skipped(
                    results,
                    "resolve_preference_place",
                    "Photon",
                    preference.model_dump(mode="json"),
                    run_id=run_id,
                    trace_id=trace_id,
                )
                continue
            outcome = _recorded_call(
                results,
                "resolve_preference_place",
                "Photon",
                preference.model_dump(mode="json"),
                lambda preference=preference: resolve_preference_place(
                    preference.label,
                    preference.search_query,
                    destination_point,
                ),
                run_id=run_id,
                trace_id=trace_id,
            )
            results.preference_places.append(outcome)

    if plan.attraction_query is not None and destination_point is not None:
        query = plan.attraction_query
        results.attractions = _recorded_call(
            results,
            "search_attractions",
            "Overpass API",
            query.model_dump(mode="json"),
            lambda: search_attractions(
                query.destination,
                destination_point,
                query.radius_meters,
                query.limit,
            ),
            run_id=run_id,
            trace_id=trace_id,
        )

    if plan.food_query is not None and destination_point is not None:
        query = plan.food_query
        results.foods = _recorded_call(
            results,
            "search_food",
            "Overpass API",
            query.model_dump(mode="json"),
            lambda: search_food(
                query.destination,
                destination_point,
                query.radius_meters,
                query.limit,
            ),
            run_id=run_id,
            trace_id=trace_id,
        )
    elif plan.food_query is not None:
        results.foods = ToolResult.skipped(
            "Overpass API", "目的地坐标不可用，跳过餐饮搜索。"
        )
        _record_skipped(
            results,
            "search_food",
            "Overpass API",
            plan.food_query.model_dump(mode="json"),
            run_id=run_id,
            trace_id=trace_id,
        )

    if plan.activity_query is not None:
        day_capacities: list[int] | None = None
        if len(plan.flight_queries) > 1:
            # The return day is intentionally lighter. We do not know which
            # candidate flight the user will choose, so this is a conservative
            # planning limit rather than a claim about an exact departure time.
            day_capacities = [2] * plan.activity_query.days
            day_capacities[-1] = 1
        rain_probability_by_day = None
        if (
            results.weather is not None
            and results.weather.status == "ok"
            and results.weather.data is not None
        ):
            rain_probability_by_day = [
                weather_day.precipitation_probability_max
                for weather_day in results.weather.data
            ]
        results.activities = assign_activities(
            plan.activity_query.destination,
            plan.activity_query.days,
            results.preference_places,
            results.attractions,
            preference_labels=plan.activity_query.preferences,
            start_date=plan.activity_query.start_date,
            day_capacities=day_capacities,
            rain_probability_by_day=rain_probability_by_day,
        )

    route_summaries: list[RouteSummary] = []
    route_outcomes: list[ToolResult[RouteSummary]] = []
    if plan.route_enabled:
        activities_by_day: dict[int, list[PlaceCandidate]] = defaultdict(list)
        for activity in results.activities:
            activities_by_day[activity.day].append(
                PlaceCandidate(
                    name=activity.name,
                    category="attraction",
                    point=activity.point,
                    provider_id=activity.provider_id or activity.name,
                    source=activity.source,
                    verified=activity.verified,
                )
            )
        total_days = plan.activity_query.days if plan.activity_query else 1
        for route_mode in plan.route_modes:
            for day_number in range(1, total_days + 1):
                places = activities_by_day[day_number]
                route_tool = (
                    optimize_transit_route
                    if route_mode == "transit"
                    else optimize_day_route
                )
                route_name = (
                    "optimize_transit_route"
                    if route_mode == "transit"
                    else "optimize_day_route"
                )
                route_provider = (
                    "OpenTripPlanner adapter" if route_mode == "transit" else "OSRM"
                )
                outcome = _recorded_call(
                    results,
                    route_name,
                    route_provider,
                    {
                        "day": day_number,
                        "mode": route_mode,
                        "stops": [place.name for place in places],
                    },
                    lambda day_number=day_number, places=places, route_tool=route_tool: route_tool(
                        day_number, places
                    ),
                    run_id=run_id,
                    trace_id=trace_id,
                )
                route_outcomes.append(outcome)
                if outcome.status == "ok" and outcome.data is not None:
                    route_summaries.append(outcome.data)

    if route_summaries:
        route_provider = " + ".join(
            "OpenTripPlanner adapter" if mode == "transit" else "OSRM"
            for mode in plan.route_modes
        )
        route_attribution = "; ".join(
            (
                "Routing: OpenTripPlanner/GTFS adapter"
                if mode == "transit"
                else "Routing: OSRM; © OpenStreetMap contributors"
            )
            for mode in plan.route_modes
        )
        results.routes = ToolResult[list[RouteSummary]].ok(
            route_provider,
            route_summaries,
            attribution=route_attribution,
        )
    elif any(outcome.status == "unavailable" for outcome in route_outcomes):
        results.routes = ToolResult[list[RouteSummary]].unavailable(
            " + ".join(
                "OpenTripPlanner adapter" if mode == "transit" else "OSRM"
                for mode in plan.route_modes
            ),
            "no_routes",
            "部分或全部路线适配器不可用。",
        )
    else:
        results.routes = ToolResult[list[RouteSummary]].skipped(
            " + ".join(
                "OpenTripPlanner adapter" if mode == "transit" else "OSRM"
                for mode in plan.route_modes
            ),
            "每天少于两个已定位地点，无需计算路线。",
        )

    return results
