"""A model-filled fact must be quotable from the user's own words.

This replaced a per-field set of keyword and regular-expression guards. The
model names the span it relied on; the program checks that the span is really
in the user's message. A value that cannot be cited is cleared, which returns
it to the ordinary ``required_missing_fields`` clarification flow rather than
inventing a second mechanism.
"""

import pytest

from travel_agent.schemas.request import (
    ORIGIN,
    TripRequest,
)
from travel_agent.workflows.intake import apply_evidence_guards, drop_uncited_fields


FULL = ["flights", "hotels", "activities", "guide"]

MALDIVES_ZH = (
    "我想要2099-10-29去马尔代夫玩3天，一定要去马富士岛，"
    "但我预算只有3万你帮我规划整个行程。"
)
MALDIVES_EN = (
    "I want to go to the Maldives for 3 days on 2099-10-29, I must visit "
    "Maafushi Island, my budget is only 30000 CNY."
)


def _request(**overrides):
    values = {
        "requested_services": FULL,
        "destination": "Maldives",
        "days": 3,
    }
    values.update(overrides)
    return TripRequest(**values)


# --- A quote that is really there --------------------------------------


def test_a_cited_fact_survives():
    guarded = drop_uncited_fields(
        _request(
            origin="Shanghai",
            start_date="2099-10-29",
            budget={"amount": 30000, "currency": "CNY", "period": "total"},
            field_evidence={
                "origin": "从上海出发",
                "start_date": "10月29号",
                "budget": "预算只有3万",
            },
        ),
        "从上海出发，10月29号去马尔代夫玩3天，预算只有3万",
    )

    assert guarded.origin == "Shanghai"
    assert guarded.start_date is not None
    assert guarded.budget.amount == 30000


def test_the_same_mechanism_works_in_english_with_no_english_keywords():
    guarded = drop_uncited_fields(
        _request(
            origin="Shanghai",
            start_date="2099-10-29",
            field_evidence={"origin": "from Shanghai", "start_date": "on Oct 29"},
        ),
        "from Shanghai to the Maldives on Oct 29 for 3 days",
    )

    assert guarded.origin == "Shanghai"
    assert guarded.start_date is not None


def test_full_width_characters_still_match():
    guarded = drop_uncited_fields(
        _request(start_date="2099-10-29", field_evidence={"start_date": "２０９９-１０-２９"}),
        "２０９９-１０-２９ 去马尔代夫",
    )

    assert guarded.start_date is not None


# --- A quote that is missing or fabricated ---------------------------------


@pytest.mark.parametrize("field, value", [
    ("origin", "Tokyo"),
    ("start_date", "2099-11-01"),
])
def test_an_uncited_fact_is_cleared(field, value):
    guarded = drop_uncited_fields(_request(**{field: value}), MALDIVES_ZH)

    assert getattr(guarded, field) is None


def test_a_fabricated_quote_is_caught():
    # The citation is well formed but the span never appears in the message.
    guarded = drop_uncited_fields(
        _request(origin="Beijing", field_evidence={"origin": "从北京出发"}),
        "我想去马尔代夫玩3天",
    )

    assert guarded.origin is None


def test_a_cleared_date_also_clears_a_derived_return_date():
    guarded = drop_uncited_fields(
        _request(start_date="2099-11-01", return_date="2099-11-04"), "去马尔代夫玩3天"
    )

    assert guarded.start_date is None
    assert guarded.return_date is None


def test_a_one_character_quote_is_not_a_citation():
    guarded = drop_uncited_fields(
        _request(origin="Shanghai", field_evidence={"origin": "上"}), "上海很远"
    )

    assert guarded.origin is None


def test_a_cleared_field_returns_to_the_normal_clarification_flow():
    # The point of clearing rather than erroring: no new mechanism is needed,
    # required_missing_fields already knows how to ask for this.
    guarded = drop_uncited_fields(_request(origin="Tokyo"), MALDIVES_ZH)

    assert guarded.status == "needs_clarification"
    assert ORIGIN in guarded.missing_fields


# --- Flight type -----------------------------------------------------------


def _flight_request(**overrides):
    values = {
        "requested_services": ["flights"],
        "origin": "Shanghai",
        "destination": "Tokyo",
        "start_date": "2099-10-01",
        "flight_type": "one_way",
    }
    values.update(overrides)
    return TripRequest(**values)


def test_an_uncited_one_way_falls_back_to_round_trip():
    # one_way removes the duration-boundary requirement, so an invented one
    # silently skips a question. Round trip is the documented default.
    guarded = drop_uncited_fields(_flight_request(), "我要从上海去东京的机票，10月1号")

    assert guarded.flight_type == "round_trip"
    assert guarded.status == "needs_clarification"


@pytest.mark.parametrize(
    "text, quote",
    [
        ("我要上海到东京的单程机票，10月1号", "单程"),
        ("one-way flight from Shanghai to Tokyo on Oct 1", "one-way"),
    ],
)
def test_a_cited_one_way_is_preserved(text, quote):
    guarded = drop_uncited_fields(
        _flight_request(field_evidence={"flight_type": quote}), text
    )

    assert guarded.flight_type == "one_way"


# --- Service scope ---------------------------------------------------------


def test_naming_a_service_narrows_the_request_to_it():
    guarded = drop_uncited_fields(
        _request(destination="Tokyo", field_evidence={"service:guide": "攻略"}),
        "给我一份东京攻略",
    )

    assert guarded.requested_services == ["activities", "guide"]


def test_naming_an_extra_service_does_not_drop_the_default_trip():
    # "顺便推荐餐厅" adds food on top of an unnamed complete trip; it is not a
    # statement that flights and hotels are unwanted.
    guarded = drop_uncited_fields(
        _request(
            destination="Tokyo",
            requested_services=[*FULL, "food"],
            field_evidence={"service:food": "推荐餐厅"},
        ),
        "去东京玩3天，顺便推荐餐厅",
    )

    assert guarded.requested_services == [*FULL, "food"]


def test_an_uncited_optional_service_is_removed():
    guarded = drop_uncited_fields(
        _request(destination="Tokyo", requested_services=[*FULL, "food"]),
        "我要去东京玩3天",
    )

    assert "food" not in guarded.requested_services


def test_a_request_naming_no_service_keeps_the_complete_trip_default():
    guarded = drop_uncited_fields(_request(destination="Tokyo"), "我要去东京玩3天")

    assert guarded.requested_services == FULL


# --- Clarification answers -------------------------------------------------


def test_a_bare_answer_to_our_own_question_needs_no_citation():
    # "上海" cites nothing, but it is the user's answer to the question the
    # program itself asked, so the value stands.
    guarded = apply_evidence_guards(
        _request(origin="Shanghai"), "上海", requested_fields={ORIGIN}
    )

    assert guarded.origin == "Shanghai"


def test_an_unasked_field_still_needs_a_citation_in_a_clarification_turn():
    guarded = apply_evidence_guards(
        _request(origin="Shanghai", budget={"amount": 9000, "currency": "CNY"}),
        "上海",
        requested_fields={ORIGIN},
    )

    assert guarded.origin == "Shanghai"
    assert guarded.budget is None


# --- The real sentence -----------------------------------------------------


@pytest.mark.parametrize(
    "text, date_quote, budget_quote",
    [
        (MALDIVES_ZH, "2099-10-29", "预算只有3万"),
        (MALDIVES_EN, "2099-10-29", "budget is only 30000 CNY"),
    ],
)
def test_the_original_sentence_is_not_disturbed(text, date_quote, budget_quote):
    guarded = apply_evidence_guards(
        _request(
            start_date="2099-10-29",
            budget={"amount": 30000, "currency": "CNY", "period": "total"},
            preferences=["马富士岛"],
            field_evidence={"start_date": date_quote, "budget": budget_quote},
        ),
        text,
    )

    assert guarded.destination == "Maldives"
    assert guarded.start_date is not None
    assert guarded.budget.amount == 30000
    assert guarded.requested_services == FULL
    # The origin genuinely was absent, so it must still be asked for.
    assert guarded.origin is None
    assert ORIGIN in guarded.missing_fields
