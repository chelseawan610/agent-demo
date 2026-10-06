from travel_agent.schemas.trip import TripPlan


def validate_trip_plan(plan: TripPlan) -> TripPlan:
    """Apply business rules after Pydantic has checked the data types."""

    if plan.status == "needs_clarification":
        return plan

    errors: list[str] = []
    if plan.days <= 0:
        errors.append("完整旅行计划的天数必须大于 0")

    for day in plan.itinerary:
        if day.day < 1 or day.day > plan.days:
            errors.append(f"Day {day.day} 超出了旅行天数范围")

    if errors:
        raise ValueError("；".join(errors))

    return plan
