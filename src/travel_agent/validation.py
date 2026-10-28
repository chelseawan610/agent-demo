from travel_agent.schemas.trip import TripPlan


def validate_trip_plan(plan: TripPlan) -> TripPlan:
    """Apply business rules after Pydantic has checked the data types."""

    if plan.status == "needs_clarification":
        return plan

    errors: list[str] = []
    if plan.days is not None and plan.days <= 0:
        errors.append("A full trip plan must have a positive day count")

    for day in plan.itinerary:
        if plan.days is not None and (day.day < 1 or day.day > plan.days):
            errors.append(f"Day {day.day} falls outside the trip length")

    if errors:
        raise ValueError("；".join(errors))

    return plan
