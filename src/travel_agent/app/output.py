"""User-facing rendering.

The model-authored summary follows the user's Chinese or English input. The
fixed labels and data-boundary notices in this module remain English so the
CLI stays predictable; evidence citations are still matched against the
user's original characters.
"""

from travel_agent.schemas.trip import TripPlan
from travel_agent.schemas.request import TripRequest


DEMO_NOTICE = (
    "> Note: flight and hotel candidates are local demo data. They are not live "
    "inventory, prices, or bookings. Weather, places, and routes come from the "
    "public sources listed above; verify before you travel."
)

VENUE_LABELS = {
    "indoor": "; indoor",
    "outdoor": "; outdoor",
    "mixed": "; indoor or outdoor",
    "unknown": "; venue type unverified",
}

MODE_LABELS = {"driving": "driving", "transit": "public transit"}


def render_service_menu() -> str:
    """Explain the supported entry types before the user writes a request."""

    return "\n".join(
        [
            "# What would you like help with?",
            "",
            "1. Full trip plan (origin, destination, departure date, days or return date)",
            "2. Round-trip flights (origin, destination, departure date, return date or length)",
            "3. Hotels (destination, check-in date, nights or check-out date)",
            "4. Guide and activities (destination; days optional)",
            "",
            'You can also just describe what you want, such as "flights and hotels"',
            'or "机票和酒店". Chinese and English both work.',
            "If unsure, pick 1; the scope is adjusted from what you actually write.",
        ]
    )


def render_trip_request(request: TripRequest) -> str:
    """Render missing intake fields before any travel tool is called."""

    lines = [
        "# More trip details needed",
        "",
        f"Requested scope: {', '.join(request.requested_services)}",
        "",
        "Before any travel tool runs, please provide:",
    ]
    lines.extend(f"- {field}" for field in request.missing_fields)
    lines.extend(
        [
            "",
            "Optional: budget, transport mode, and interests. The duration "
            "boundary depends on the request type above.",
            "",
            DEMO_NOTICE,
        ]
    )
    return "\n".join(lines)


def format_plan(text: str) -> str:
    """Wrap the model response in a consistent, user-facing Markdown format."""

    body = text.strip()
    if not body:
        body = "The model did not produce a travel plan."

    if DEMO_NOTICE not in body:
        body = f"{body}\n\n{DEMO_NOTICE}"

    return f"# Travel plan\n\n{body}"


def render_trip_plan(plan: TripPlan) -> str:
    """Render one validated TripPlan as user-facing Markdown."""

    if plan.status == "needs_clarification":
        lines = [
            "# More trip details needed",
            "",
            plan.summary or "Please provide the following:",
        ]
        missing = plan.missing_information or plan.notes
        lines.extend(["", "## Still needed"])
        lines.extend(f"- {item}" for item in missing)
        lines.extend(["", DEMO_NOTICE])
        return "\n".join(lines)

    title_suffix = " (some data unavailable)" if plan.status == "partial" else ""
    title = (
        f"{plan.destination} — {plan.days}-day trip plan"
        if plan.days is not None
        else f"{plan.destination} — flight candidates"
    )
    lines = [f"# {title}{title_suffix}", "", plan.summary]
    if plan.generation_source == "deterministic_fallback":
        lines.extend(
            [
                "",
                "> Narrative generation was unavailable. Every fact on this page "
                "was assembled directly from the tool results that succeeded.",
            ]
        )
    elif plan.generation_source == "offline":
        lines.extend(
            [
                "",
                "> Offline mode: no language model was called. This page was "
                "assembled by local deterministic logic.",
            ]
        )
    if plan.status == "partial":
        lines.extend(
            [
                "",
                "> The workflow completed, but some data sources were "
                "unavailable. Only results that were retrieved and verified "
                "are shown below.",
            ]
        )
    if plan.budget is not None:
        # The budget is recorded but no supplier is filtered by it: flight and
        # hotel candidates come from the demo adapter, which has no inventory
        # to price against. Saying so prevents a reader from assuming the plan
        # was checked against this number.
        budget_caveat = (
            "This budget is recorded only; it was not used to filter "
            "candidates. Flight and hotel prices are demo data."
        )
        if plan.budget.amount is not None:
            lines.extend(
                [
                    "",
                    "## Budget",
                    f"{plan.budget.amount:g} {plan.budget.currency} "
                    f"({plan.budget.period or 'total'})",
                    budget_caveat,
                ]
            )
        elif plan.budget.level is not None:
            lines.extend(
                ["", "## Budget", f"Level: {plan.budget.level}", budget_caveat]
            )
    if plan.flights:
        lines.extend(["", "## Flight options"])
        lines.extend(
            f"- {flight.airline}: {flight.origin} → {flight.destination}, "
            f"{flight.departure}-{flight.arrival}, {flight.price}"
            for flight in plan.flights
        )
    if plan.hotels:
        lines.extend(["", "## Hotel options"])
        lines.extend(
            f"- {hotel.name} ({hotel.area}): {hotel.nights} nights, "
            f"{hotel.price_per_night}/night, rated {hotel.rating}"
            for hotel in plan.hotels
        )
    if plan.search_links:
        lines.extend(["", "## Live search links"])
        for link in plan.search_links:
            label = "Flights" if link.service == "flights" else "Hotels"
            lines.append(f"- [{label}: {link.provider}]({link.url}) — {link.disclaimer}")
    if plan.transport_modes:
        lines.extend(
            [
                "",
                "## Transport",
                "Requested: "
                + " + ".join(MODE_LABELS[mode] for mode in plan.transport_modes),
            ]
        )
    if plan.weather:
        lines.extend(["", "## Weather"])
        for weather in plan.weather:
            rain = (
                f", max precipitation probability {weather.precipitation_probability_max}%"
                if weather.precipitation_probability_max is not None
                else ""
            )
            lines.append(
                f"- {weather.date}: {weather.temperature_min_c:g}–"
                f"{weather.temperature_max_c:g}°C{rain}"
            )
    if plan.itinerary:
        lines.extend(["", "## Daily itinerary"])
        for day in plan.itinerary:
            lines.extend([f"### Day {day.day}: {day.title}"])
            if not day.activities:
                lines.append(
                    "- No verified activity candidates for this day; adjust on "
                    "the ground as you prefer."
                )
            for activity in day.activities:
                detail = f": {activity.description}" if activity.description else ""
                source = f" ({activity.source})" if activity.source else ""
                if activity.opening_status == "open" and activity.opening_hours:
                    hours = f"; open {activity.opening_hours}"
                elif activity.opening_status == "closed":
                    hours = "; closed on the planned date, not suggested"
                else:
                    hours = "; opening hours unverified"
                duration = (
                    f"; about {activity.estimated_visit_minutes} min"
                    if activity.estimated_visit_minutes is not None
                    else ""
                )
                venue = VENUE_LABELS[activity.venue_type]
                cuisine = f"; cuisine {activity.cuisine}" if activity.cuisine else ""
                website = f"; [website]({activity.website})" if activity.website else ""
                route_note = (
                    "; area centroid, not a verified entrance, excluded from routing"
                    if not activity.route_eligible
                    else ""
                )
                lines.append(
                    f"- {activity.name}{detail}{source}{venue}{cuisine}"
                    f"{hours}{duration}{website}{route_note}"
                )
    if plan.food:
        lines.extend(["", "## Dining candidates"])
        for place in plan.food:
            cuisine = f"; cuisine {place.cuisine}" if place.cuisine else ""
            venue = VENUE_LABELS[place.venue_type]
            hours = (
                f"; open {place.opening_hours}"
                if place.opening_hours
                else "; opening hours unverified"
            )
            website = f"; [website]({place.website})" if place.website else ""
            source = f" ({place.source})" if place.source else ""
            lines.append(f"- {place.name}{source}{cuisine}{venue}{hours}{website}")
    if plan.routes:
        lines.extend(["", "## Route estimates"])
        for route in plan.routes:
            mode = MODE_LABELS["transit" if route.profile == "transit" else "driving"]
            lines.append(
                f"- Day {route.day}: {' → '.join(route.ordered_stops)}; "
                f"about {route.distance_meters / 1000:.1f} km, "
                f"{route.duration_seconds / 60:.0f} min by {mode}"
            )
    unavailable = [record for record in plan.tool_status if record.status == "unavailable"]
    if unavailable:
        lines.extend(["", "## Unavailable data"])
        lines.extend(
            f"- {record.tool_name} ({record.provider}): "
            f"{record.message or record.error_code or 'no data returned'}"
            for record in unavailable
        )
    if plan.data_sources:
        lines.extend(["", "## Data sources", "- " + "; ".join(plan.data_sources)])
    if plan.knowledge_sources:
        lines.extend(["", "## Knowledge references"])
        lines.extend(
            f"- {item.title} / {item.section} ({item.source}, score {item.score:.2f})"
            for item in plan.knowledge_sources
        )
    if plan.review is not None:
        lines.extend(["", "## Quality review"])
        lines.append(f"- Verdict: {plan.review.status}")
        if plan.review.strengths:
            lines.append("- Satisfied: " + "; ".join(plan.review.strengths))
        if plan.review.issues:
            lines.append("- Concerns: " + "; ".join(plan.review.issues))
        if plan.review.checked_constraints:
            lines.append("- Checked: " + "; ".join(plan.review.checked_constraints))
    if plan.notes:
        lines.extend(["", "## Notes"])
        lines.extend(f"- {note}" for note in plan.notes)
    lines.extend(["", DEMO_NOTICE])
    return "\n".join(lines)
