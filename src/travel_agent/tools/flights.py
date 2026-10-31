from datetime import date

from agent_framework import tool
from travel_agent.schemas.trip import FlightOption


@tool(approval_mode="never_require")
def search_flights(
    origin: str,
    destination: str,
    departure_date: date,
) -> list[FlightOption]:
    """Find demo flight options between two cities for a travel date."""

    print(
        f"[tool] search_flights(origin={origin!r}, "
        f"destination={destination!r}, date={departure_date!r})"
    )

    return [
        FlightOption(
            origin=origin,
            destination=destination,
            date=departure_date,
            airline="Demo Air",
            departure="09:00",
            arrival="12:00",
            price="¥1200",
        ),
        FlightOption(
            origin=origin,
            destination=destination,
            date=departure_date,
            airline="Sample Airways",
            departure="14:30",
            arrival="17:30",
            price="¥980",
        ),
    ]
