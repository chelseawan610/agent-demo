from datetime import date, timedelta

import pytest

from travel_agent.schemas.request import BudgetSpec, TripRequest, parse_calendar_date
from travel_agent.workflows.intake import collect_trip_request, merge_trip_requests


def test_trip_request_is_not_ready_without_date_or_duration():
    request = TripRequest(origin="上海", destination="首尔", intent="full_plan")

    assert request.status == "needs_clarification"
    assert "出发日期" in request.missing_fields


def test_guide_only_needs_destination():
    request = TripRequest(destination="东京", requested_services=["guide"])

    assert request.status == "ready"
    assert request.missing_fields == []


def test_default_trip_request_is_full_plan():
    request = TripRequest(destination="东京")

    assert request.requested_services == ["flights", "hotels", "activities", "guide"]
    assert request.status == "needs_clarification"
    assert "出发城市" in request.missing_fields
    assert "出发日期" in request.missing_fields


def test_trip_request_is_ready_with_required_fields():
    request = TripRequest(
        intent="full_plan",
        origin="上海",
        destination="首尔",
        start_date="2099-10-01",
        nights=2,
    )

    assert request.status == "ready"
    assert request.missing_fields == []


def test_iso_date_without_zero_padding_is_normalized_once():
    request = TripRequest(
        requested_services=["flights"],
        origin="Shanghai",
        destination="Seoul",
        start_date="2099-12-3",
    )

    assert request.start_date.isoformat() == "2099-12-03"
    assert request.status == "ready"


def test_calendar_formats_accept_dots_and_omitted_year():
    assert parse_calendar_date("2026.10.3") == date(2026, 10, 3)
    assert parse_calendar_date("10.3", today=date(2026, 9, 26)) == date(2026, 10, 3)
    assert parse_calendar_date("10.3", today=date(2026, 11, 1)) == date(2027, 10, 3)


def test_past_model_date_returns_to_clarification_before_tools():
    yesterday = (date.today() - timedelta(days=1)).isoformat()

    request = TripRequest(
        requested_services=["flights"],
        origin="Shanghai",
        destination="Seoul",
        start_date=yesterday,
    )

    assert request.start_date is None
    assert request.status == "needs_clarification"
    assert "出发日期（不能早于今天）" in request.missing_fields


def test_budget_is_normalized_without_inventing_a_default():
    assert TripRequest(destination="东京").budget is None
    assert TripRequest(destination="东京", budget={"level": "medium"}).budget == BudgetSpec(
        level="medium"
    )
    assert TripRequest(destination="东京", budget=10000).budget == BudgetSpec(
        amount=10000, period="total"
    )


def test_follow_up_does_not_drop_existing_preferences():
    old = TripRequest(destination="东京", preferences=["东京塔", "动漫"])
    new = TripRequest(origin="上海", start_date="2099-10-01")

    merged = merge_trip_requests(old, new)

    assert merged.preferences == ["东京塔", "动漫"]


def test_follow_up_cannot_rewrite_confirmed_fields():
    old = TripRequest(
        requested_services=["flights"],
        origin="Shanghai",
        destination="Seoul",
        start_date="2099-11-01",
    )
    new = TripRequest(
        requested_services=["guide"],
        origin="Tokyo",
        destination="Busan",
        start_date="2099-12-01",
    )

    merged = merge_trip_requests(old, new)

    assert merged.origin == "Shanghai"
    assert merged.destination == "Seoul"
    assert merged.start_date.isoformat() == "2099-11-01"
    assert merged.requested_services == ["flights"]


def test_follow_up_preserves_duration_profile_without_overwriting_new_inference():
    old = TripRequest(destination="Tokyo", duration_profile="weekend")
    new = TripRequest(origin="Shanghai")

    merged = merge_trip_requests(old, new)

    assert merged.duration_profile == "weekend"


def test_structured_preferences_keep_user_label_and_search_query():
    request = TripRequest(
        destination="Tokyo",
        preferences=["东京塔"],
        preference_details=[
            {"label": "东京塔", "search_query": "Tokyo Tower Tokyo", "kind": "place"}
        ],
    )

    assert request.preferences == ["东京塔"]
    assert request.preference_details[0].search_query == "Tokyo Tower Tokyo"


def test_guide_request_needs_only_destination_and_defaults_one_day():
    request = TripRequest(destination="Tokyo", requested_services=["activities", "guide"])

    assert request.status == "ready"
    assert request.days == 1
    assert request.missing_fields == []


def test_duration_profile_resolves_before_search_without_fixed_three_day_default():
    request = TripRequest(
        origin="Shanghai",
        destination="Tokyo",
        start_date="2099-10-01",
        duration_profile="weekend",
    )

    assert request.days == 2
    assert request.nights == 1


def test_model_recommended_days_are_authoritative_over_profile_mapping():
    request = TripRequest(
        origin="Shanghai",
        destination="Tokyo",
        start_date="2099-10-01",
        days=5,
        duration_profile="standard",
    )

    assert request.days == 5
    assert request.nights == 4


def test_return_date_derives_duration_before_search():
    request = TripRequest(
        origin="Shanghai",
        destination="Tokyo",
        start_date="2099-10-01",
        return_date="2099-10-05",
    )

    assert request.days == 5
    assert request.nights == 4


def test_hotel_checkout_date_derives_nights_before_search():
    request = TripRequest(
        requested_services=["hotels"],
        destination="Tokyo",
        start_date="2099-10-01",
        return_date="2099-10-04",
    )

    assert request.nights == 3


class FakeResponse:
    def __init__(self, value):
        self.value = value
        self.text = value.model_dump_json()


class FakeIntakeAgent:
    def __init__(self, values):
        self.values = iter(values)
        self.sessions = []

    def create_session(self):
        session = object()
        self.sessions.append(session)
        return session

    async def run(self, *_args, **_kwargs):
        assert _kwargs.get("session") is self.sessions[0]
        return FakeResponse(next(self.values))


@pytest.mark.asyncio
async def test_three_step_clarification_keeps_previous_answers():
    agent = FakeIntakeAgent(
        [
            TripRequest(destination="Tokyo"),
            TripRequest(origin="Shanghai"),
                TripRequest(start_date="2099-10-01"),
        ]
    )
    answers = iter(["上海", "2099年10月1日"])
    asked = []

    async def ask(request):
        asked.append(request.missing_fields.copy())
        return next(answers)

    request, _ = await collect_trip_request(agent, "我要去东京玩", ask_clarification=ask)

    assert asked == [["出发城市", "出发日期"], ["出发日期"]]
    assert request.status == "ready"
    assert request.origin == "Shanghai"
    assert request.destination == "Tokyo"
    assert request.start_date.isoformat() == "2099-10-01"
    assert request.days == 2
    assert request.nights == 1


@pytest.mark.asyncio
async def test_clarification_stops_after_three_answers():
    agent = FakeIntakeAgent([TripRequest(destination="Tokyo") for _ in range(4)])
    calls = 0

    async def ask(_request):
        nonlocal calls
        calls += 1
        return "我还没决定"

    request, _ = await collect_trip_request(agent, "东京旅行", ask_clarification=ask)

    assert calls == 3
    assert request.status == "needs_clarification"
    assert request.missing_fields == ["出发城市", "出发日期"]


@pytest.mark.asyncio
async def test_missing_date_is_asked_again_until_answered():
    agent = FakeIntakeAgent(
        [
            TripRequest(origin="Shanghai", destination="Tokyo"),
            TripRequest(origin="Shanghai", destination="Tokyo"),
            TripRequest(start_date="2099-10-01"),
        ]
    )
    answers = iter(["日期还没想好", "2029年10月1日"])
    asked = []

    async def ask(request):
        asked.append(request.missing_fields.copy())
        return next(answers)

    request, _ = await collect_trip_request(
        agent,
        "只查从上海到东京的机票",
        ask_clarification=ask,
    )

    assert asked == [["出发日期"], ["出发日期"]]
    assert request.status == "ready"
    assert request.start_date.isoformat() == "2099-10-01"
