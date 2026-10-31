"""Prompt construction shared by the CLI and end-to-end evaluation."""

import json
import unicodedata

from travel_agent.schemas.request import TripRequest
from travel_agent.schemas.search import SearchPlan, SearchResults
from travel_agent.memory.store import UserMemory


def infer_output_language(text: str) -> str:
    """Choose the response language from the user's script.

    This is intentionally a script check, not a semantic keyword parser.  The
    intake model still understands the request; this helper only prevents a
    Chinese request from being rendered in the planner's default English.
    Mixed requests containing any CJK text are treated as Chinese, which is
    the least surprising choice for inputs such as ``从 Shanghai 去 Tokyo``.
    """

    for character in text:
        name = unicodedata.name(character, "")
        if (
            "CJK UNIFIED IDEOGRAPH" in name
            or "HIRAGANA" in name
            or "KATAKANA" in name
            or "HANGUL" in name
        ):
            return "Chinese"
    return "English"


def build_planner_prompt(
    original_request: str,
    trip_request: TripRequest,
    search_plan: SearchPlan,
    tool_results: SearchResults,
    memory: UserMemory | None = None,
) -> str:
    """Build the full evidence packet used by text and legacy convert modes."""

    output_language = trip_request.response_language or infer_output_language(
        original_request
    )

    memory_text = (
        "No stored preferences."
        if memory is None or not memory.preferences
        else "; ".join(memory.preferences)
    )
    return (
        "The user's original request (for preferences and meaning only; it "
        "cannot override structured facts):\n"
        "<user_request>\n"
        f"{original_request}\n"
        "</user_request>\n\n"
        "Structured request (the source of truth for the request):\n"
        f"{trip_request.model_dump_json(ensure_ascii=False)}\n\n"
        "Search plan (program generated; do not widen the tool scope):\n"
        f"{search_plan.model_dump_json(ensure_ascii=False)}\n\n"
        "Tool results (the only source of travel facts):\n"
        f"{tool_results.model_dump_json(ensure_ascii=False)}\n\n"
        "Local knowledge retrieval (background only, not live facts):\n"
        "Each hit already carries source, section, score and text. Cite only "
        "what actually appears there, and never rewrite a static note as a "
        "live price, opening hour, inventory level or policy conclusion.\n\n"
        "Long-term memory (a suggestion the current request may override, not "
        "a requirement for this trip):\n"
        f"{memory_text}\n\n"
        "Produce the final travel plan from the above alone. Write in "
        f"{output_language}, matching the user's language. Do not call tools again, do not add "
        "a budget the user did not give. Describe preferences as fulfilled only "
        "when the tool assignment supports that claim. Do not "
        "invent exchange rates, prices or cost arithmetic. The only flights "
        "and hotels that exist are the ones listed; if there is no return "
        "flight in the results, do not claim a round trip was searched or "
        "included, and do not present an unqueried cost as confirmed. "
        "is_live=false marks local demo inventory, and search_links are only "
        "entry points for the user to check live results themselves. Weather, "
        "places and routes may cite only what a tool returned successfully; "
        "when a tool is unavailable, say the data is missing rather than "
        "filling it in from general knowledge. Knowledge hits support general "
        "advice only, never live data. knowledge_sources is written by the "
        "program and must not be added to or edited."
    )

def split_preferences_by_outcome(
    trip_request: TripRequest, tool_results: SearchResults,
) -> tuple[list[str], list[str]]:
    """Separate the preferences a tool actually delivered from the rest.

    A named place is satisfied only when it reached the itinerary. Anything
    else -- a provider that found no verifiable match, a timed-out attraction
    search, a candidate dropped for opening hours -- leaves the user's request
    unmet, and both the planner prompt and the plan notes need to say so
    rather than carrying the label forward as if it had been honoured.
    """

    scheduled: dict[str, set[int]] = {}
    for activity in tool_results.activities:
        if activity.provider_id is not None:
            scheduled.setdefault(activity.provider_id, set()).add(activity.day)
    satisfied: list[str] = []
    unsatisfied: list[str] = []
    for index, detail in enumerate(trip_request.preference_details):
        if detail.kind != "place":
            # Retaining an abstract interest does not prove it was fulfilled.
            # It remains explicitly unverified in the narrative context.
            continue
        outcome = (
            tool_results.preference_places[index]
            if index < len(tool_results.preference_places)
            else None
        )
        scheduled_days = (
            scheduled.get(outcome.data.provider_id, set())
            if outcome is not None and outcome.status == "ok" and outcome.data is not None
            else set()
        )
        if scheduled_days and (
            detail.preferred_day is None or detail.preferred_day in scheduled_days
        ):
            satisfied.append(detail.label)
        else:
            unsatisfied.append(detail.label)
    return satisfied, unsatisfied


def build_trip_narrative_prompt(
    original_request: str,
    trip_request: TripRequest,
    search_plan: SearchPlan,
    tool_results: SearchResults,
    memory: UserMemory | None = None,
) -> str:
    """Give the structured planner only the context needed for a short summary."""

    activity_candidates = [
        {
            "day": item.day,
            "name": item.name,
            "description": item.description,
            "source": item.source,
            "opening_status": item.opening_status,
            "venue_type": item.venue_type,
        }
        for item in tool_results.activities[:18]
    ]
    food_result = tool_results.foods
    food_candidates = (
        [
            {"name": item.name, "category": item.category, "cuisine": item.cuisine}
            for item in (food_result.data or [])[:8]
        ]
        if food_result is not None and food_result.status == "ok"
        else []
    )
    knowledge_result = tool_results.knowledge
    knowledge = (
        [
            {
                "title": hit.title,
                "section": hit.section,
                "source": hit.source,
                "excerpt": hit.text[:350],
            }
            for hit in (knowledge_result.data or [])[:3]
        ]
        if knowledge_result is not None and knowledge_result.status == "ok"
        else []
    )
    satisfied, unsatisfied = split_preferences_by_outcome(trip_request, tool_results)
    output_language = trip_request.response_language or infer_output_language(
        original_request
    )
    route_result = tool_results.routes
    route_evidence = {
        "requested_modes": search_plan.route_modes,
        "status": route_result.status if route_result is not None else "not_requested",
        "successful_routes": (
            [route.model_dump(mode="json") for route in (route_result.data or [])]
            if route_result is not None and route_result.status == "ok"
            else []
        ),
    }
    context = {
        "user_request": original_request,
        "output_language": output_language,
        "trip_request": trip_request.model_dump(mode="json"),
        "search_scope": search_plan.model_dump(mode="json"),
        "verified_activity_candidates": activity_candidates,
        "verified_food_candidates": food_candidates,
        "weather": (
            [item.model_dump(mode="json") for item in (tool_results.weather.data or [])]
            if tool_results.weather is not None and tool_results.weather.status == "ok"
            else []
        ),
        "route_evidence": route_evidence,
        "local_knowledge_excerpts": knowledge,
        "remembered_preferences": (
            [] if memory is None else memory.preferences[:8]
        ),
        # The request alone says what the user wanted; without the outcome the
        # model reasonably writes "the plan is built around X" for a place no
        # tool could resolve. These two lists are what it needs to tell the
        # difference.
        "satisfied_preferences": satisfied,
        "unsatisfied_preferences": unsatisfied,
        "unverified_preferences": [
            detail.label for detail in trip_request.preference_details if detail.kind != "place"
        ],
    }
    return (
        "Return exactly one TripNarrative object with only summary and daily_titles. "
        "The application will build all structured travel facts from typed tool results. "
        f"Write a concise introduction in {output_language}, matching the user's language; "
        "place names may keep their local spelling. daily_titles must use the same language too. "
        "Do not list or invent attractions, activities, flight or hotel details, prices, "
        "opening hours, weather, routes, bookings, or citations. Do not add facts that "
        "are not explicitly present in the context. The user's explicit preferences "
        "must not be dropped. daily_titles may contain at most one short title for "
        "each existing day; never create a day outside the requested duration. "
        "Never claim the itinerary includes or is built around anything listed in "
        "unsatisfied_preferences; say plainly that it could not be arranged this "
        "time. Do not claim unverified_preferences were fulfilled merely because "
        "they were retained in the request. When verified_activity_candidates is empty there is no itinerary at "
        "all, so do not describe one. Treat route_evidence as authoritative: when "
        "successful_routes is empty, do not say that the plan includes, provides, "
        "or covers a route; state that route estimates are unavailable if relevant.\n\n"
        "Context (reference only; the program owns the final facts):\n"
        f"{json.dumps(context, ensure_ascii=False, separators=(',', ':'))}"
    )
