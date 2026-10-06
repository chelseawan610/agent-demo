from agent_framework import tool
from travel_agent.schemas.trip import HotelOption


@tool(approval_mode="never_require")
def search_hotels(
    destination: str,
    nights: int,
    budget: str = "unspecified",
) -> list[HotelOption]:
    """Find demo hotel options for a destination, number of nights, and budget level."""

    print(
        f"[tool] search_hotels(destination={destination!r}, "
        f"nights={nights!r}, budget={budget!r})"
    )

    return [
        HotelOption(
            name="Demo Central Hotel",
            area="市中心",
            nights=nights,
            price_per_night="¥650",
            rating="4.3/5",
        ),
        HotelOption(
            name="Sample Station Inn",
            area="交通枢纽附近",
            nights=nights,
            price_per_night="¥420",
            rating="4.0/5",
        ),
    ]
