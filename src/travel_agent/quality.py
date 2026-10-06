"""Deterministic workflow checks used beside model-based evaluations.

These checks do not ask another model to judge the run. They verify invariants
that must hold even when the planner's prose is non-deterministic.
"""

from dataclasses import dataclass

from travel_agent.schemas.request import TripRequest
from travel_agent.schemas.search import SearchPlan, SearchResults
from travel_agent.schemas.trip import TripPlan


@dataclass(frozen=True)
class WorkflowCheck:
    name: str
    passed: bool
    message: str


def check_workflow_invariants(
    request: TripRequest,
    search_plan: SearchPlan,
    search_results: SearchResults,
    final_plan: TripPlan,
) -> list[WorkflowCheck]:
    """Check process-level contracts after one workflow run."""

    checks: list[WorkflowCheck] = []

    def add(name: str, passed: bool, message: str) -> None:
        checks.append(WorkflowCheck(name, passed, message))

    add(
        "request_to_plan_duration",
        request.days is None or final_plan.days == request.days,
        "最终计划天数必须等于已确认需求。",
    )
    add(
        "request_to_plan_destination",
        request.destination is None or final_plan.destination == request.destination,
        "最终目的地必须等于已确认需求。",
    )
    add(
        "request_to_plan_preferences",
        final_plan.preferences == request.preferences,
        "最终偏好必须保留用户明确表达。",
    )

    called = {record.tool_name for record in search_results.tool_calls}
    flight_call_count = sum(
        record.tool_name == "search_flights" for record in search_results.tool_calls
    )
    hotel_call_count = sum(
        record.tool_name == "search_hotels" for record in search_results.tool_calls
    )
    food_call_count = sum(
        record.tool_name == "search_food" for record in search_results.tool_calls
    )
    add(
        "search_plan_flights_executed_once",
        flight_call_count == len(search_plan.flight_queries),
        "每个计划中的航班查询必须恰好执行一次。",
    )
    add(
        "search_plan_hotels_executed_once",
        hotel_call_count == (1 if search_plan.hotel_query is not None else 0),
        "计划中的酒店查询必须恰好执行一次。",
    )
    add(
        "search_plan_food_executed_once",
        food_call_count == (1 if search_plan.food_query is not None else 0),
        "计划中的餐饮查询必须恰好执行一次。",
    )
    services = set(request.requested_services)
    if "flights" not in services:
        add(
            "service_scope_flights",
            not ({"search_flights", "build_flight_search_link"} & called),
            "未请求航班时不得调用航班工具。",
        )
    if "hotels" not in services:
        add(
            "service_scope_hotels",
            not ({"search_hotels", "build_hotel_search_link"} & called),
            "未请求酒店时不得调用酒店工具。",
        )
    if "food" not in services and search_plan.food_query is None:
        add(
            "service_scope_food",
            "search_food" not in called,
            "未请求餐饮时不得调用餐饮工具。",
        )

    add(
        "flight_evidence_is_authoritative",
        final_plan.flights == search_results.flights,
        "最终航班候选必须来自工具结果。",
    )
    add(
        "hotel_evidence_is_authoritative",
        final_plan.hotels == search_results.hotels,
        "最终酒店候选必须来自工具结果。",
    )
    food_evidence = (
        search_results.foods.data
        if search_results.foods is not None
        and search_results.foods.status == "ok"
        and search_results.foods.data is not None
        else []
    )
    add(
        "food_evidence_is_authoritative",
        final_plan.food == food_evidence,
        "最终餐饮候选必须来自工具结果，不能由模型凭空编造。",
    )
    expected_weather = (
        search_results.weather.data
        if search_results.weather is not None
        and search_results.weather.status == "ok"
        and search_results.weather.data is not None
        else []
    )
    add(
        "weather_evidence_is_authoritative",
        final_plan.weather == expected_weather,
        "最终天气必须来自天气工具结果，不能由模型补写。",
    )
    expected_routes = (
        search_results.routes.data
        if search_results.routes is not None
        and search_results.routes.status == "ok"
        and search_results.routes.data is not None
        else []
    )
    add(
        "route_evidence_is_authoritative",
        final_plan.routes == expected_routes,
        "最终路线必须来自路线工具结果，不能把模型描述当成路线事实。",
    )
    expected_knowledge_sources = []
    if (
        search_results.knowledge is not None
        and search_results.knowledge.status == "ok"
        and search_results.knowledge.data is not None
    ):
        expected_knowledge_sources = [
            {
                "chunk_id": hit.chunk_id,
                "source": hit.source,
                "title": hit.title,
                "section": hit.section,
                "score": hit.score,
            }
            for hit in search_results.knowledge.data
        ]
    add(
        "knowledge_evidence_is_authoritative",
        [item.model_dump(mode="json") for item in final_plan.knowledge_sources]
        == expected_knowledge_sources,
        "最终知识来源必须来自本地检索结果，不能由模型自行捏造引用。",
    )
    expected_activity_keys = {
        (activity.day, activity.provider_id or activity.name)
        for activity in search_results.activities
    }
    actual_activity_keys = {
        (day.day, activity.provider_id or activity.name)
        for day in final_plan.itinerary
        for activity in day.activities
    }
    add(
        "itinerary_evidence_is_authoritative",
        actual_activity_keys == expected_activity_keys,
        "最终行程中的地点和日期必须来自工具分配结果，不能由模型新增或移动。",
    )
    if search_plan.route_enabled:
        add(
            "route_modes_are_authoritative",
            final_plan.transport_modes == search_plan.route_modes,
            "最终交通方式必须等于已确认的 SearchPlan，且 transit 不能替换 driving。",
        )
        days = request.days or 1
        expected_route_calls = len(search_plan.route_modes) * days
        actual_route_calls = sum(
            record.tool_name in {"optimize_day_route", "optimize_transit_route"}
            for record in search_results.tool_calls
        )
        add(
            "route_profiles_are_executed_once_per_day",
            actual_route_calls == expected_route_calls,
            "每个计划中的路线模式每天必须恰好执行一次。",
        )
        driving_calls = sum(
            record.tool_name == "optimize_day_route"
            for record in search_results.tool_calls
        )
        transit_calls = sum(
            record.tool_name == "optimize_transit_route"
            for record in search_results.tool_calls
        )
        add(
            "each_requested_route_mode_is_executed_once_per_day",
            driving_calls == days
            and transit_calls == (days if "transit" in search_plan.route_modes else 0),
            "驾车和公共交通必须分别按请求的模式逐天执行，不能用一种模式冒充另一种。",
        )
    add(
        "tool_trace_is_preserved",
        final_plan.tool_status == search_results.tool_calls,
        "最终计划必须保留每次工具调用的状态。",
    )

    valid_days = set(range(1, final_plan.days + 1))
    activity_days = {
        activity.day for activity in search_results.activities
    }
    add(
        "activity_days_are_valid",
        activity_days <= valid_days,
        "活动不能被分配到旅行天数之外。",
    )
    add(
        "route_days_are_valid",
        all(route.day in valid_days for route in final_plan.routes),
        "路线不能被分配到旅行天数之外。",
    )
    add(
        "closed_activities_are_not_scheduled",
        all(
            activity.opening_status != "closed"
            for day in final_plan.itinerary
            for activity in day.activities
        ),
        "已确认关闭的地点不能进入最终候选行程。",
    )

    return checks
