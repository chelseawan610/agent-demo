from datetime import date
from urllib.parse import quote_plus

from agent_framework import tool

from travel_agent.schemas.evidence import SearchLink, ToolResult


@tool(approval_mode="never_require")
def build_flight_search_link(
    origin: str,
    destination: str,
    departure_date: date,
    return_date: date | None = None,
) -> ToolResult[SearchLink]:
    """Create a Google Travel link without scraping prices or inventory."""

    query = f"Flights from {origin} to {destination} on {departure_date}"
    if return_date:
        query += f" returning {return_date}"
    link = SearchLink(
        service="flights",
        provider="Google Travel",
        url=f"https://www.google.com/travel/flights?q={quote_plus(query)}",
        departure_date=departure_date,
        return_date=return_date,
    )
    print(
        f"[tool] build_flight_search_link(origin={origin!r}, "
        f"destination={destination!r}, departure_date={departure_date!r}, "
        f"return_date={return_date!r})"
    )
    return ToolResult[SearchLink].ok("Google Travel link builder", link)


@tool(approval_mode="never_require")
def build_hotel_search_link(
    destination: str,
    check_in_date: date,
    check_out_date: date,
) -> ToolResult[SearchLink]:
    """Create a Google Travel hotel link without scraping live data."""

    query = f"Hotels in {destination} from {check_in_date} to {check_out_date}"
    link = SearchLink(
        service="hotels",
        provider="Google Travel",
        url=f"https://www.google.com/travel/search?q={quote_plus(query)}",
        check_in_date=check_in_date,
        check_out_date=check_out_date,
    )
    print(
        f"[tool] build_hotel_search_link(destination={destination!r}, "
        f"check_in_date={check_in_date!r}, check_out_date={check_out_date!r})"
    )
    return ToolResult[SearchLink].ok("Google Travel link builder", link)
