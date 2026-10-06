from datetime import date

from agent_framework import Agent

from travel_agent.clients.api import create_client
from travel_agent.config import Settings


def create_intake_agent(model: str | None = None) -> Agent:
    """Create the agent that extracts structured requirements from user text."""

    settings = Settings.from_env(model=model)
    reference_date = date.today()
    today = reference_date.isoformat()
    example_october_3 = date(reference_date.year, 10, 3)
    if example_october_3 < reference_date:
        example_october_3 = date(reference_date.year + 1, 10, 3)
    return Agent(
        client=create_client(settings, model=model),
        name="TripRequestParser",
        instructions=(
            "Extract travel requirements from the user's message. "
            "Extract a flexible requested_services list using only these values: "
            "flights, hotels, activities, guide, food. A bare intention to travel to "
            "a place (for example, 'I want to go to Tokyo' or '我要去东京玩') "
            "is a complete trip and defaults to all four. Only an explicit request "
            "for a destination guide, 攻略, sights, or activities without transport "
            "or lodging should use activities and guide only. If the user asks "
            "only for selected services or says a flight/hotel is already handled, "
            "remove excluded services and keep every requested combination. "
            "Do not force the request into one mutually exclusive intent. "
            "Return only the requested JSON object. Normalize dates to "
            "YYYY-MM-DD. Today's date is "
            f"{today}. If the user gives a month and day without a year, "
            "use the nearest upcoming occurrence relative to today's date; "
            "for example, 十月3号 and 10.3 mean "
            f"{example_october_3.isoformat()}. Accept formats such as 2026.10.3, "
            "2026-10-3, 10/3, and 十月3日, then output the ISO date. "
            "Use null only when no calendar day can be understood. "
            "Normalize origin and destination to internationally recognizable "
            "English city names for downstream public APIs. "
            "When the user uses a source-to-destination expression, preserve both "
            "ends: '从深圳去香港', '从深圳出发前往香港', and 'from Shenzhen to Hong Kong' "
            "mean origin=Shenzhen and destination=Hong Kong. Never leave origin null "
            "when an explicit source city is present in the message. "
            "Extract an explicit return_date when supplied. Do not guess origin, "
            "destination. If the user explicitly gives days or nights, preserve "
            "the exact numbers. If duration is omitted, you must recommend a "
            "reasonable integer days value before tools run, based on the "
            "destination, the user's preferences, requested services, and the "
            "pace implied by the request. Also classify the recommendation in "
            "duration_profile using only these values: day_trip (one day), "
            "weekend (two days), short_break (three days), standard (four days), "
            "extended (about one week), or unknown. Do not use a fixed three-day "
            "default for every destination. Never invent an exact calendar date. "
            "Only leave both days and duration_profile unknown when there is no "
            "itinerary scope, such as a one-way flights-only request. The "
            "application validates this recommendation and locks the planning "
            "horizon before tools run. Days, nights, "
            "and preferences are optional; leave them null when unknown. "
            "For budget, map numeric amounts to amount/currency/period and "
            "map low/medium/high language to level. Never invent a budget "
            "when the user did not mention one. Keep the amount in the unit "
            "the user stated: '每天预算2000元' means amount=2000 and "
            "period=per_day, not amount=8000 or period=total; '每晚预算500元' "
            "means period=per_night. Only multiply or calculate totals later "
            "in application code, never during intake. Preserve explicit places, "
            "interests, food, accessibility needs, and other preferences in "
            "the preferences array; do not discard them. The preferences array "
            "must contain exactly the user-facing labels from preference_details, "
            "not translated duplicates or synonyms. Also create one "
            "preference_details item per preference: label must preserve the "
            "user's wording, search_query must be an English provider-friendly "
            "place query including the destination when useful, and kind must "
            "be place, interest, food, accessibility, service, or other. Use "
            "kind=service for wording that names requested capabilities rather "
            "than a personal preference, and then omit it from preferences. Do "
            "not put "
            "service words such as guide, 攻略, flights, hotels, activities, "
            "or 景点 into preferences. If hotels and a guide are both requested, "
            "keep activities and guide together with hotels. For example, the "
            "meaning of '机票已经买好，只要酒店和攻略' is requested_services "
            "['hotels', 'activities', 'guide'], with preferences []. The meaning "
            "of '东京攻略，只要景点和活动' is requested_services "
            "['activities', 'guide'], with preferences []."
            " Extract transport_mode only when the user explicitly asks for "
            "driving/驾车 or public transit/地铁/公交; use driving for the "
            "route default when it is omitted, and use transit only for an "
            "explicit public-transport request. Use food as a service when the "
            "user explicitly asks for restaurants, meals, food, or dining."
        ),
        default_options={
            "temperature": 0.0,
            "max_tokens": 1000,
            "extra_body": (
                {"reasoning": {"effort": settings.reasoning_effort}}
                if settings.reasoning_effort
                else {}
            ),
        },
    )
