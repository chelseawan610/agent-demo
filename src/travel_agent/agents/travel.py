from agent_framework import Agent

from travel_agent.clients.api import create_client
from travel_agent.config import Settings


TRIP_FORMAT_INSTRUCTIONS = """
Return only one valid JSON object. Do not use Markdown fences and do not add
explanations before or after the JSON. The object must have these fields:
status (complete or needs_clarification), destination (string), days (integer),
summary (string), budget (object or null, with amount, currency, level, and
period), preferences (array of strings), missing_information (array of strings), flights (array), hotels
(array), food (array of place candidates), itinerary (array of objects with day, title, and activities), and notes
(array of strings). Each flight must include origin, destination, date,
airline, departure, arrival, and price. Each hotel must include name, area,
nights, price_per_night, and rating. Use empty arrays when information is
unavailable; do not omit fields from an item that is present. Listing candidate
flights or hotels is complete; do not request a selection. If the request does
not contain enough information, set status to needs_clarification, put the
missing details in missing_information, set days to 0, and do not invent data.
Each itinerary activity must be an object with name, description, time_of_day,
location, and notes. Preserve venue_type, cuisine, opening_hours, and website
when they are present. Never output schema field names as activity values.
Weather, places, routes, search_links, tool_status, data_sources, and
knowledge_sources are typed evidence fields. Use empty arrays when absent and
never invent their contents. knowledge_sources are citations copied from the
local retrieval result; do not create new citations.
""".strip()


def create_travel_agent(model: str | None = None) -> Agent:
    settings = Settings.from_env(model=model)
    client = create_client(settings, model=model)

    return Agent(
        client=client,
        name="TravelPlanner",
        instructions=(
            "You are a helpful travel planning assistant. "
            "Help users create clear and practical travel plans from the "
            "normalized request and tool results in the prompt. Flight and hotel "
            "candidates with is_live=false are local demo data; weather, places, "
            "and routes with successful tool status are public-source evidence. "
            "None of these results is a booking. "
            "Never claim that a flight or hotel has been booked. "
            "List candidate flight and hotel options without requiring the "
            "user to select one first. "
            "Respect the requested service scope and preserve every explicit "
            "preference, such as a named landmark. Use reasonable defaults for "
            "optional preferences, but never invent a budget level. "
            "If essential information is missing, ask for it instead of "
            "inventing values or calling tools with guessed arguments. "
            "Interpret dates written as YY.MM.DD, such as 26.10.1, as "
            "2026-10-01. If a date or trip length is ambiguous, ask the user "
            "to confirm before calling a search tool. "
            "When you use tools, organize the response with these Markdown "
            "sections when relevant: Flight Options, Hotel Options, "
            "Daily Itinerary, Food References, and Notes. "
            "If food candidates are present, show them as restaurant or cafe "
            "references; do not invent prices, ratings, reservations, or menus. "
            "If both driving and public-transit route evidence are present, show "
            "both separately; never describe a driving estimate as public transit."
        ),
        tools=[],
        default_options={
            "temperature": 0.0,
            "max_tokens": 3000,
            "extra_body": (
                {"reasoning": {"effort": settings.reasoning_effort}}
                if settings.reasoning_effort
                else {}
            ),
        },
    )


def create_trip_formatter(model: str | None = None) -> Agent:
    """Create the second-stage agent used by the convert output mode."""

    settings = Settings.from_env(model=model)
    client = create_client(settings, model=model)
    return Agent(
        client=client,
        name="TripPlanFormatter",
        instructions=(
            "You convert a travel planner's text into a machine-readable "
            "TripPlan object. Never invent booking confirmations. "
            + TRIP_FORMAT_INSTRUCTIONS
        ),
        default_options={
            "temperature": 0.0,
            "max_tokens": 3000,
            "extra_body": (
                {"reasoning": {"effort": settings.reasoning_effort}}
                if settings.reasoning_effort
                else {}
            ),
        },
    )


def create_trip_reviewer(model: str | None = None) -> Agent:
    """Create an optional read-only reviewer for metacognitive evaluation."""

    settings = Settings.from_env(model=model)
    client = create_client(settings, model=model)
    return Agent(
        client=client,
        name="TripPlanReviewer",
        instructions=(
            "You are a strict, read-only reviewer of a structured travel plan. "
            "Check request constraints, service scope, user preferences, and "
            "whether facts are supported by tool evidence. Return only a "
            "ReviewResult JSON. Never invent facts and never rewrite the plan."
        ),
        default_options={
            "temperature": 0.0,
            "max_tokens": 1200,
            "extra_body": (
                {"reasoning": {"effort": settings.reasoning_effort}}
                if settings.reasoning_effort
                else {}
            ),
        },
    )
