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
            "a place (for example, 'I want to travel to a destination' or "
            "'我要去某个地方玩') "
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
            "ends: '从出发城市去目的地', '从出发城市出发前往目的地', and "
            "'from origin city to destination city' mean the first city is origin "
            "and the second city is destination. Never leave origin null "
            "when an explicit source city is present in the message. "
            "Extract an explicit return_date when supplied. Do not guess origin, "
            "destination. If the user explicitly gives days or nights, preserve "
            "the exact numbers. Set nights_explicit=true only when the user "
            "actually states a number of nights or a checkout boundary; set it "
            "to false when nights is only being inferred. If the user gives days "
            "but no nights or return date, leave nights null; application code "
            "will derive the normal days-minus-one hotel stay. If flights are "
            "requested, set flight_type to "
            "one_way only when the user explicitly says single/one-way; otherwise "
            "set round_trip. For a full plan, a hotel request, or a round-trip "
            "flight request, do not invent days, nights, or a return_date when "
            "the user has not provided one: leave the missing boundary null so "
            "the application can ask a focused follow-up question. Only "
            "guide/activities/food-only requests may receive a bounded duration "
            "recommendation when no date boundary is needed. Set duration_profile "
            "using only these values: day_trip (one day), weekend (two days), "
            "short_break (three days), standard (four days), extended (about one "
            "week), or unknown. Never invent an exact calendar date. Days, nights, "
            "and preferences are optional only where the request type allows it; "
            "leave them null when a required boundary is unknown. "
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
            "be place, interest, food, accessibility, service, or other. Also "
            "for kind=place set place_name to the canonical English entity name "
            "without city qualifiers, and place_type to landmark, waterfront, "
            "museum, park, restaurant, hotel, or other. A harbour or river is "
            "waterfront, not a restaurant that happens to share its name. "
            "For non-place preferences leave place_name null and place_type other. "
            "Interests and planning constraints must not be converted into named "
            "places or map stops. "
            "When the user ties a place to a specific trip day, such as "
            "'第3天想去双子塔' or 'visit it on day 3', set that "
            "preference's preferred_day to the 1-based day number. Leave "
            "preferred_day null when no day is named; never guess one. "
            "set planning_hint using only venue_mix, indoor_only, outdoor_only, "
            "pace, or none. Use venue_mix for requests such as indoor/outdoor "
            "balance, 室内外结合, or a mix of indoor and outdoor activities. "
            "Use indoor_only or outdoor_only only when the user explicitly "
            "excludes the other type. Use "
            "kind=service for wording that names requested capabilities rather "
            "than a personal preference, and then omit it from preferences. Do "
            "not put "
            "service words such as guide, 攻略, flights, hotels, activities, "
            "or 景点 into preferences. If hotels and a guide are both requested, "
            "keep activities and guide together with hotels. For example, the "
            "meaning of '机票已经买好，只要酒店和攻略' is requested_services "
            "['hotels', 'activities', 'guide'], with preferences []. The meaning "
            "of '目的地攻略，只要景点和活动' is requested_services "
            "['activities', 'guide'], with preferences []."
            " Extract transport_modes as a list. Use [driving] when the user "
            "explicitly asks for driving/驾车 or says nothing about transport. "
            "Use [driving, transit] when the user asks for public transit/地铁/公交 "
            "without excluding driving; this keeps driving as the baseline "
            "comparison and adds the optional transit adapter. Use [transit] when "
            "the user says public transit only/只要公共交通, and [driving] when "
            "the user says driving only/只开车. If the user explicitly asks for "
            "both, preserve both in the list. Keep transport_mode as the primary "
            "compatibility label. "
            "Use food as a service when the "
            "user explicitly asks for restaurants, meals, food, or dining. "
            "Never copy a city name from these instructions or from a previous "
            "model answer. A city is valid only when it is present in the user's "
            "message or in a user-confirmed answer. If multiple cities are present "
            "without a clear origin/destination relation, leave the destination "
            "uncertain instead of selecting a familiar city."
            " Also return field_evidence: for every field you fill, quote the "
            "exact span of the user's message that supports it, copied "
            "verbatim, character for character, with no translation, "
            "normalization, paraphrase or added words. Use the field name as "
            "the key, for example {\"origin\": \"从上海出发\", \"start_date\": "
            "\"2026-10-29\", \"budget\": \"预算只有3万\"}. For each service in "
            "requested_services that the user named explicitly, add a key "
            "\"service:\" plus the service name, for example "
            "\"service:hotels\". Omit the service keys entirely when the user "
            "described a trip without naming any service. If no span of the "
            "user's message supports a field, leave that field null and omit "
            "its key rather than inventing a citation: an uncited value is "
            "discarded and the user is asked again. "
            " Also return stated_fields: the field names this user message "
            "actually states, using only these values: origin, destination, "
            "start_date, return_date, days, nights, budget, flight_type, "
            "requested_services, preferences, transport_modes. Include a field "
            "when the latest user text mentions it, including when the user "
            "changes an earlier value, for example '改成4天' is days, "
            "'其实是去大阪' is destination, '不要酒店了' is requested_services, "
            "and '预算提到两万' is budget. When several user turns are supplied, "
            "report the fields stated in the most recent turn. Never include a "
            "field you only inferred, defaulted, or carried over from earlier "
            "context; an empty list is correct when the user added nothing new."
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
