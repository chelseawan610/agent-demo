import pytest

from travel_agent.agents.intake import create_intake_agent
from travel_agent.workflows.intake import collect_trip_request, extract_trip_request
from travel_agent.workflows.search import build_search_plan


pytestmark = pytest.mark.llm_live


@pytest.mark.asyncio
async def test_llm_live_complete_chinese_request():
    request = await extract_trip_request(
        create_intake_agent(),
        "我从上海出发，2026年10月1日去东京玩三天，想去东京塔，还喜欢动漫。",
    )
    plan = build_search_plan(request)
    assert request.status == "ready"
    assert request.requested_services == ["flights", "hotels", "activities", "guide"]
    assert request.preference_details
    assert plan.flight_queries
    assert plan.attraction_query is not None


@pytest.mark.asyncio
async def test_llm_live_complete_english_request():
    request = await extract_trip_request(
        create_intake_agent(),
        "Plan a three-day Tokyo trip from Shanghai starting October 1, 2026. "
        "I want Tokyo Tower and anime activities.",
    )
    assert request.status == "ready"
    assert request.origin == "Shanghai"
    assert request.destination == "Tokyo"
    assert request.start_date.isoformat() == "2026-10-01"


@pytest.mark.asyncio
async def test_llm_live_three_turn_clarification():
    answers = iter(["上海", "2026年10月1日"])
    missing_by_round = []

    async def ask(request):
        missing_by_round.append(request.missing_fields.copy())
        return next(answers)

    request, _ = await collect_trip_request(
        create_intake_agent(), "我要去东京玩", ask_clarification=ask
    )
    assert missing_by_round == [["出发城市", "出发日期"], ["出发日期"]]
    assert request.status == "ready"


@pytest.mark.asyncio
async def test_llm_live_guide_excludes_purchased_flights():
    request = await extract_trip_request(
        create_intake_agent(), "机票已经买好，只需要东京三日攻略和景点。"
    )
    plan = build_search_plan(request)
    assert request.status == "ready"
    assert "flights" not in request.requested_services
    assert plan.flight_queries == []
    assert plan.flight_link_query is None
    assert plan.attraction_query is not None
