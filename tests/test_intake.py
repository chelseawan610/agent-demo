from datetime import date, timedelta

import pytest

from travel_agent.schemas.request import (
    DEPARTURE_DATE,
    ORIGIN,
    TRIP_LENGTH_OR_NIGHTS,
    TRIP_LENGTH_OR_RETURN,
    BudgetSpec,
    TripRequest,
    parse_calendar_date,
)
from travel_agent.workflows.intake import (
    collect_trip_request,
    drop_uncited_fields,
    merge_trip_requests,
    protect_explicit_destination,
)


def test_trip_request_is_not_ready_without_date_or_duration():
    request = TripRequest(origin="上海", destination="首尔", intent="full_plan")

    assert request.status == "needs_clarification"
    assert DEPARTURE_DATE in request.missing_fields


def test_guide_only_needs_destination():
    request = TripRequest(destination="东京", requested_services=["guide"])

    assert request.status == "ready"
    assert request.missing_fields == []


def test_default_trip_request_is_full_plan():
    request = TripRequest(destination="东京")

    assert request.requested_services == ["flights", "hotels", "activities", "guide"]
    assert request.status == "needs_clarification"
    assert ORIGIN in request.missing_fields
    assert DEPARTURE_DATE in request.missing_fields


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
        flight_type="one_way",
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
    assert f"{DEPARTURE_DATE} (cannot be in the past)" in request.missing_fields


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


def test_explicit_seoul_cannot_be_silently_changed_to_tokyo():
    request = TripRequest(destination="Tokyo", requested_services=["guide"])

    protected = protect_explicit_destination(request, "我要去首尔玩")

    assert protected.destination == "Seoul"
    assert protected.status == "ready"


def test_source_order_keeps_destination_after_origin():
    request = TripRequest(
        origin="Shanghai", destination="Tokyo", requested_services=["guide"]
    )

    protected = protect_explicit_destination(request, "from Shanghai to Seoul")

    assert protected.destination == "Seoul"


def test_preference_city_does_not_override_destination_city():
    request = TripRequest(destination="Tokyo", requested_services=["guide"])

    protected = protect_explicit_destination(request, "我要去东京玩，顺便看首尔美食")

    assert protected.destination == "Tokyo"


def test_unknown_city_is_left_to_model_and_provider():
    request = TripRequest(destination="Tokyo", requested_services=["guide"])

    protected = protect_explicit_destination(request, "我要去一个没有收录的城市")

    assert protected.destination == "Tokyo"


def test_unrequested_food_service_is_removed_from_model_output():
    # food is outside the complete-trip default, so it is only ever present
    # because the user asked; an uncited one is dropped.
    request = TripRequest(destination="Seoul", requested_services=["guide", "food"])

    protected = drop_uncited_fields(request, "我要去首尔玩三天")

    assert protected.requested_services == ["guide"]


def test_explicit_food_service_is_preserved():
    request = TripRequest(
        destination="Seoul",
        requested_services=["guide", "food"],
        field_evidence={"service:food": "想吃寿司"},
    )

    protected = drop_uncited_fields(request, "我要去首尔玩三天，想吃寿司")

    assert "food" in protected.requested_services


def test_a_negated_food_request_is_the_models_job_not_a_keyword_window():
    # The old guard scanned for a negation within eight characters before a
    # food word. The model reads the sentence and simply does not extract the
    # service; if it does anyway, the missing citation still removes it.
    request = TripRequest(destination="Seoul", requested_services=["guide", "food"])

    protected = drop_uncited_fields(request, "去首尔攻略，不需要餐厅推荐")

    assert protected.requested_services == ["guide"]


def test_follow_up_preserves_duration_profile_without_overwriting_new_inference():
    old = TripRequest(destination="Tokyo", duration_profile="weekend")
    new = TripRequest(origin="Shanghai")

    merged = merge_trip_requests(old, new)

    assert merged.duration_profile == "weekend"


def test_follow_up_preserves_response_language():
    old = TripRequest(destination="Tokyo", response_language="Chinese")
    new = TripRequest(origin="Shanghai")

    merged = merge_trip_requests(old, new)

    assert merged.response_language == "Chinese"


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


def test_duration_profile_cannot_replace_a_missing_full_plan_boundary():
    request = TripRequest(
        origin="Shanghai",
        destination="Tokyo",
        start_date="2099-10-01",
        duration_profile="weekend",
    )

    assert request.status == "needs_clarification"
    assert request.days is None
    assert request.nights is None
    assert TRIP_LENGTH_OR_RETURN in request.missing_fields


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


def test_inferred_nights_follow_days_minus_one():
    request = TripRequest(
        origin="Shanghai",
        destination="Tokyo",
        start_date="2099-10-01",
        days=3,
        nights=3,
    )

    assert request.nights == 2


def test_explicit_nights_are_preserved_when_marked_explicit():
    request = TripRequest(
        origin="Shanghai",
        destination="Tokyo",
        start_date="2099-10-01",
        days=3,
        nights=3,
        nights_explicit=True,
    )

    assert request.nights == 3


def test_return_date_uses_elapsed_days_without_inclusive_plus_one():
    request = TripRequest(
        origin="Shanghai",
        destination="Tokyo",
        start_date="2099-10-20",
        return_date="2099-10-23",
    )

    assert request.days == 3
    assert request.nights == 3


def test_full_plan_requires_a_duration_boundary_before_tools():
    request = TripRequest(
        origin="Shanghai",
        destination="Tokyo",
        start_date="2099-10-20",
    )

    assert request.status == "needs_clarification"
    assert request.missing_fields == [TRIP_LENGTH_OR_RETURN]


def test_one_way_flight_request_does_not_need_return_boundary():
    request = TripRequest(
        requested_services=["flights"],
        origin="Shanghai",
        destination="Tokyo",
        start_date="2099-10-20",
        flight_type="one_way",
    )

    assert request.status == "ready"
    assert request.missing_fields == []


def test_transport_modes_preserve_driving_and_transit_together():
    request = TripRequest(
        destination="Tokyo",
        days=2,
        requested_services=["activities", "guide"],
        transport_modes=["driving", "transit"],
    )

    assert request.transport_modes == ["driving", "transit"]
    assert request.transport_mode == "transit"


def test_explicit_transit_only_does_not_add_driving():
    request = TripRequest(
        destination="Lisbon",
        days=5,
        requested_services=["activities", "guide"],
        transport_modes=["transit"],
        transport_mode="transit",
    )

    assert request.transport_modes == ["transit"]
    assert request.transport_mode == "transit"


def test_flight_only_ignores_model_inferred_nights():
    request = TripRequest(
        requested_services=["flights"],
        origin="Shanghai",
        destination="Tokyo",
        start_date="2099-10-20",
        days=3,
        nights=3,
    )

    assert request.nights is None


def test_service_specific_duration_questions_are_combined_for_hotel_and_guide():
    request = TripRequest(
        requested_services=["hotels", "guide"],
        destination="Tokyo",
        start_date="2099-10-20",
    )

    assert request.missing_fields == [TRIP_LENGTH_OR_NIGHTS]


def test_return_date_derives_duration_before_search():
    request = TripRequest(
        origin="Shanghai",
        destination="Tokyo",
        start_date="2099-10-01",
        return_date="2099-10-05",
    )

    assert request.days == 4
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

    async def run(self, *_args, **_kwargs):
        assert _kwargs.get("session") is None
        return FakeResponse(next(self.values))


@pytest.mark.asyncio
async def test_collect_trip_request_protects_user_destination_before_search():
    agent = FakeIntakeAgent(
        [TripRequest(destination="Tokyo", requested_services=["guide"])]
    )

    request, _ = await collect_trip_request(agent, "我要去首尔看攻略")

    assert request.destination == "Seoul"
    assert request.status == "ready"


@pytest.mark.asyncio
async def test_three_step_clarification_keeps_previous_answers():
    agent = FakeIntakeAgent(
        [
            TripRequest(destination="Tokyo"),
            TripRequest(origin="Shanghai"),
            TripRequest(start_date="2099-10-01"),
            TripRequest(days=3),
        ]
    )
    answers = iter(["上海", "2099年10月1日", "3天"])
    asked = []

    async def ask(request):
        asked.append(request.missing_fields.copy())
        return next(answers)

    request, _ = await collect_trip_request(agent, "我要去东京玩", ask_clarification=ask)

    assert asked == [
        [ORIGIN, DEPARTURE_DATE, TRIP_LENGTH_OR_RETURN],
        [DEPARTURE_DATE, TRIP_LENGTH_OR_RETURN],
        [TRIP_LENGTH_OR_RETURN],
    ]
    assert request.status == "ready"
    assert request.origin == "Shanghai"
    assert request.destination == "Tokyo"
    assert request.start_date.isoformat() == "2099-10-01"
    assert request.days == 3
    assert request.nights == 2


@pytest.mark.asyncio
async def test_follow_up_does_not_reinject_previous_model_destination():
    agent = FakeIntakeAgent(
        [
            TripRequest(destination="Tokyo"),
            TripRequest(
                origin="Shanghai",
                destination="Tokyo",
                start_date="2099-10-01",
                days=3,
            ),
        ]
    )
    prompts = []

    async def ask(_request):
        return "上海"

    def observe(_stage, prompt, _options):
        prompts.append(prompt)

    await collect_trip_request(
        agent,
        "我要去首尔玩",
        ask_clarification=ask,
        on_prompt=observe,
    )

    assert len(prompts) == 2
    assert '"destination": "Tokyo"' not in prompts[1]
    assert "1. 上海" in prompts[1]
    assert "我要去首尔玩" in prompts[1]


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
    assert request.missing_fields == [
        ORIGIN,
        DEPARTURE_DATE,
        TRIP_LENGTH_OR_RETURN,
    ]


@pytest.mark.asyncio
async def test_missing_date_is_asked_again_until_answered():
    agent = FakeIntakeAgent(
        [
            TripRequest(
                requested_services=["flights"],
                origin="Shanghai", destination="Tokyo", flight_type="one_way",
                field_evidence={"origin": "从上海", "flight_type": "单程"},
            ),
            TripRequest(
                requested_services=["flights"],
                origin="Shanghai", destination="Tokyo", flight_type="one_way",
                field_evidence={"origin": "从上海", "flight_type": "单程"},
            ),
            TripRequest(
                requested_services=["flights"],
                start_date="2099-10-01",
                flight_type="one_way",
                field_evidence={"start_date": "2029年10月1日", "flight_type": "单程"},
            ),
        ]
    )
    answers = iter(["日期还没想好", "2029年10月1日"])
    asked = []

    async def ask(request):
        asked.append(request.missing_fields.copy())
        return next(answers)

    request, _ = await collect_trip_request(
        agent,
        "只查从上海到东京的单程机票",
        ask_clarification=ask,
    )

    assert asked == [[DEPARTURE_DATE], [DEPARTURE_DATE]]
    assert request.status == "ready"
    assert request.start_date.isoformat() == "2099-10-01"


# --- Correction intent ------------------------------------------------------
# A clarification turn must be able to change a previously confirmed fact when
# the user says so, while a model that merely drifts must still be rejected.


def test_merge_accepts_a_user_correction_for_a_stated_field():
    old = TripRequest(
        origin="Shanghai", destination="Tokyo", start_date="2099-10-01", days=3
    )
    new = TripRequest(days=4, stated_fields=["days"])

    merged = merge_trip_requests(old, new)

    assert merged.days == 4
    # The derived night count must follow the corrected length, not stay at 2.
    assert merged.nights == 3
    assert merged.destination == "Tokyo"


def test_merge_rejects_a_model_drift_on_an_unstated_field():
    old = TripRequest(
        origin="Shanghai", destination="Tokyo", start_date="2099-10-01", days=3
    )
    new = TripRequest(destination="Osaka", days=3, stated_fields=["budget"])

    merged = merge_trip_requests(old, new)

    assert merged.destination == "Tokyo"


def test_merge_accepts_an_explicit_destination_change():
    old = TripRequest(
        origin="Shanghai", destination="Tokyo", start_date="2099-10-01", days=3
    )
    new = TripRequest(destination="Osaka", stated_fields=["destination"])

    merged = merge_trip_requests(old, new)

    assert merged.destination == "Osaka"


def test_merge_without_stated_fields_keeps_confirmed_values():
    old = TripRequest(
        origin="Shanghai", destination="Tokyo", start_date="2099-10-01", days=3
    )
    new = TripRequest(destination="Osaka", days=9)

    merged = merge_trip_requests(old, new)

    assert merged.destination == "Tokyo"
    assert merged.days == 3


def test_merge_accepts_a_stated_service_scope_change():
    old = TripRequest(
        origin="Shanghai",
        destination="Tokyo",
        start_date="2099-10-01",
        days=3,
        requested_services=["flights", "hotels", "activities", "guide"],
    )
    new = TripRequest(
        requested_services=["activities", "guide"],
        stated_fields=["requested_services"],
    )

    merged = merge_trip_requests(old, new)

    assert merged.requested_services == ["activities", "guide"]


@pytest.mark.asyncio
async def test_clarification_round_can_add_a_food_service():
    # A service named in a later round must survive the merge. The citation
    # is checked against every user turn, not only the first.
    agent = FakeIntakeAgent(
        [
            TripRequest(destination="Tokyo", days=3),
            TripRequest(
                origin="Shanghai",
                destination="Tokyo",
                start_date="2099-10-01",
                days=3,
                requested_services=[
                    "flights", "hotels", "activities", "guide", "food",
                ],
                stated_fields=["origin", "start_date", "requested_services"],
                field_evidence={
                    "origin": "从上海出发",
                    "start_date": "2099-10-01",
                    "service:food": "推荐几家餐厅",
                },
            ),
        ]
    )

    async def ask(_request):
        return "从上海出发，2099-10-01，顺便推荐几家餐厅"

    request, _ = await collect_trip_request(agent, "去东京玩三天", ask_clarification=ask)

    assert "food" in request.requested_services


@pytest.mark.asyncio
async def test_third_round_still_sees_the_second_round_answer():
    agent = FakeIntakeAgent([TripRequest(destination="Tokyo") for _ in range(4)])
    prompts = []
    replies = iter(["从上海出发", "2099-10-01", "玩三天"])

    async def ask(_request):
        return next(replies)

    def observe(_stage, prompt, _options):
        prompts.append(prompt)

    await collect_trip_request(
        agent, "去东京", ask_clarification=ask, on_prompt=observe
    )

    assert "从上海出发" in prompts[-1]
    assert "2099-10-01" in prompts[-1]


# --- Destinations outside the small alias map -------------------------------
# The alias map only knows a handful of cities. When it recognizes the origin
# but not the destination, it must stay silent instead of promoting the origin.


@pytest.mark.parametrize(
    "text, origin, destination",
    [
        ("从东京去马尔代夫玩三天", "Tokyo", "Maldives"),
        ("from Tokyo to Osaka", "Tokyo", "Osaka"),
        ("I want to travel to Maldives from Tokyo", "Tokyo", "Maldives"),
        ("从巴黎去冰岛", "Paris", "Iceland"),
    ],
)
def test_unknown_destination_is_not_replaced_by_a_known_origin(
    text, origin, destination
):
    request = TripRequest(
        origin=origin, destination=destination, start_date="2099-10-29", days=3
    )

    assert protect_explicit_destination(request, text).destination == destination


def test_known_destination_is_still_corrected():
    request = TripRequest(
        origin="Shanghai", destination="Seoul", start_date="2099-10-29", days=3
    )

    assert protect_explicit_destination(request, "from Shanghai to Tokyo").destination == "Tokyo"
