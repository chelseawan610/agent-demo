"""Small, human-readable golden set for comparing intake models."""

from dataclasses import dataclass


@dataclass(frozen=True)
class IntakeCase:
    name: str
    text: str
    expected_services: tuple[str, ...] | None = None
    origin: str | None = None
    destination: str | None = None
    start_date: str | None = None
    return_date: str | None = None
    days: int | None = None
    nights: int | None = None
    budget_amount: float | None = None
    budget_level: str | None = None
    preferences: tuple[str, ...] = ()
    missing_fields: tuple[str, ...] | None = None
    ready: bool | None = None


CASES = [
    IntakeCase(
        name="full_zh_with_budget_and_preferences",
        text="我从上海出发，2026年10月1日去东京玩三天，总预算10000元，我想去东京塔，还喜欢动漫。",
        origin="Shanghai",
        destination="Tokyo",
        start_date="2026-10-01",
        days=3,
        nights=2,
        budget_amount=10000,
        expected_services=("flights", "hotels", "activities", "guide"),
        preferences=("东京塔", "动漫"),
        ready=True,
    ),
    IntakeCase(
        name="full_zh_with_return_date",
        text="我从上海出发，2026-10-01去东京玩3天，10月4日回上海。",
        origin="Shanghai",
        destination="Tokyo",
        start_date="2026-10-01",
        return_date="2026-10-04",
        days=3,
        nights=2,
        expected_services=("flights", "hotels", "activities", "guide"),
        ready=True,
    ),
    IntakeCase(
        name="date_dot_format",
        text="从深圳去香港，2026.10.3玩三天。",
        origin="Shenzhen",
        destination="Hong Kong",
        start_date="2026-10-03",
        days=3,
        nights=2,
        expected_services=("flights", "hotels", "activities", "guide"),
        ready=True,
    ),
    IntakeCase(
        name="full_en",
        text="I am leaving from Shanghai for Seoul on 2026-12-03 for four days and three nights.",
        origin="Shanghai",
        destination="Seoul",
        start_date="2026-12-03",
        days=4,
        nights=3,
        expected_services=("flights", "hotels", "activities", "guide"),
        ready=True,
    ),
    IntakeCase(
        name="guide_only_zh",
        text="东京攻略，只要景点和活动。",
        destination="Tokyo",
        days=1,
        expected_services=("activities", "guide"),
        ready=True,
    ),
    IntakeCase(
        name="guide_only_en",
        text="Give me a two-day sightseeing guide for Tokyo, no flights or hotels.",
        destination="Tokyo",
        days=2,
        expected_services=("activities", "guide"),
        ready=True,
    ),
    IntakeCase(
        name="flights_only_one_way",
        text="只查上海到东京 2026-10-01 的单程机票。",
        origin="Shanghai",
        destination="Tokyo",
        start_date="2026-10-01",
        expected_services=("flights",),
        ready=True,
    ),
    IntakeCase(
        name="activities_only_with_places",
        text="只要东京的活动和景点，不需要机票酒店，我想去东京塔和秋叶原。",
        destination="Tokyo",
        days=1,
        expected_services=("activities", "guide"),
        preferences=("东京塔", "秋叶原"),
        ready=True,
    ),
    IntakeCase(
        name="hotels_only",
        text="只查东京 2026-11-01 入住两晚的酒店。",
        destination="Tokyo",
        start_date="2026-11-01",
        nights=2,
        expected_services=("hotels",),
        ready=True,
    ),
    IntakeCase(
        name="flight_already_bought",
        text="机票已经买好了，我从上海去东京玩三天，只要酒店和攻略。",
        origin="Shanghai",
        destination="Tokyo",
        days=3,
        nights=1,
        expected_services=("hotels", "activities", "guide"),
        ready=False,
        missing_fields=("入住日期",),
    ),
    IntakeCase(
        name="hotel_and_guide_with_checkin",
        text="机票已买好，2026-11-01入住东京两晚，只要酒店和攻略。",
        destination="Tokyo",
        start_date="2026-11-01",
        nights=2,
        expected_services=("hotels", "activities", "guide"),
        ready=True,
    ),
    IntakeCase(
        name="budget_level_only",
        text="我从上海出发，2026-12-01去首尔玩4天，预算中等。",
        origin="Shanghai",
        destination="Seoul",
        start_date="2026-12-01",
        days=4,
        nights=3,
        budget_level="medium",
        expected_services=("flights", "hotels", "activities", "guide"),
        ready=True,
    ),
    IntakeCase(
        name="hotel_already_booked",
        text="酒店已订，从上海去东京2026-10-01玩3天，只要机票和攻略。",
        origin="Shanghai",
        destination="Tokyo",
        start_date="2026-10-01",
        days=3,
        expected_services=("flights", "activities", "guide"),
        ready=True,
    ),
    IntakeCase(
        name="budget_unspecified",
        text="我从上海出发，2026-10-01 去东京玩三天。",
        origin="Shanghai",
        destination="Tokyo",
        start_date="2026-10-01",
        days=3,
        nights=2,
        expected_services=("flights", "hotels", "activities", "guide"),
        ready=True,
    ),
    IntakeCase(
        name="bare_destination_needs_clarification",
        text="我要去东京玩。",
        destination="Tokyo",
        expected_services=("flights", "hotels", "activities", "guide"),
        missing_fields=("出发城市", "出发日期"),
        ready=False,
    ),
    IntakeCase(
        name="flight_request_without_date",
        text="只查上海到东京的机票。",
        origin="Shanghai",
        destination="Tokyo",
        expected_services=("flights",),
        missing_fields=("出发日期",),
        ready=False,
    ),
    IntakeCase(
        name="colloquial_origin_zh",
        text="要去香港玩一圈，3天吧，从深圳去香港，十月3号吧。",
        origin="Shenzhen",
        destination="Hong Kong",
        start_date="2026-10-03",
        days=3,
        nights=2,
        expected_services=("flights", "hotels", "activities", "guide"),
        ready=True,
    ),
    IntakeCase(
        name="colloquial_origin_en",
        text="I want to spend three days in Hong Kong, leaving from Shenzhen on 2026-10-03.",
        origin="Shenzhen",
        destination="Hong Kong",
        start_date="2026-10-03",
        days=3,
        nights=2,
        expected_services=("flights", "hotels", "activities", "guide"),
        ready=True,
    ),
]


# These paraphrases are intentionally kept separate from the prompt examples.
# They are a small held-out set used to detect prompt overfitting.
HELD_OUT_CASES = [
    IntakeCase(
        name="heldout_guide_with_preferences_zh",
        text="机票和酒店都安排好了，我想要巴黎五日攻略，偏好美术馆和咖啡馆。",
        destination="Paris",
        days=5,
        expected_services=("activities", "guide"),
        preferences=("美术馆", "咖啡馆"),
        ready=True,
    ),
    IntakeCase(
        name="heldout_flights_only_en",
        text="Please find a one-way flight from Beijing to Singapore on 2026/11/08. No hotel.",
        origin="Beijing",
        destination="Singapore",
        start_date="2026-11-08",
        expected_services=("flights",),
        ready=True,
    ),
    IntakeCase(
        name="heldout_hotels_only_zh",
        text="只看酒店：2026年12月12日从广州去曼谷，住三晚。",
        destination="Bangkok",
        start_date="2026-12-12",
        nights=3,
        expected_services=("hotels",),
        ready=True,
    ),
    IntakeCase(
        name="heldout_budget_per_day",
        text="从杭州去台北，2026-11-20玩4天，每天预算2000元，只要行程攻略。",
        origin="Hangzhou",
        destination="Taipei",
        start_date="2026-11-20",
        days=4,
        expected_services=("activities", "guide"),
        budget_amount=2000,
        ready=True,
    ),
    IntakeCase(
        name="heldout_missing_flight_date",
        text="我只想查从广州到大阪的机票，日期还没决定。",
        origin="Guangzhou",
        destination="Osaka",
        expected_services=("flights",),
        missing_fields=("出发日期",),
        ready=False,
    ),
]
