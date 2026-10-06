"""Run the typed travel workflow without a language-model provider."""

from travel_agent.schemas.request import TripRequest
from travel_agent.schemas.search import SearchResults
from travel_agent.schemas.trip import TripPlan
from travel_agent.workflows.trip_planning import build_deterministic_trip_plan


def build_offline_trip_plan(
    request: TripRequest,
    search_results: SearchResults,
) -> TripPlan:
    """Build a validated deterministic plan for the CLI offline mode."""

    if request.status != "ready":
        raise ValueError(
            "离线模式需要 ready 状态的 TripRequest；"
            f"仍缺少：{', '.join(request.missing_fields) or '未知字段'}"
        )
    return build_deterministic_trip_plan(request, search_results)
