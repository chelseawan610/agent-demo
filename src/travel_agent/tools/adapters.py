"""Replaceable supplier adapters for flight and hotel inventory.

The public workflow depends on these small contracts instead of a specific
paid supplier. The demo adapters are deterministic and explicitly marked as
non-live; a future Amadeus/Booking adapter can implement the same methods.
"""

from dataclasses import dataclass
from typing import Protocol

from travel_agent.schemas.evidence import ToolResult
from travel_agent.schemas.search import FlightQuery, HotelQuery
from travel_agent.schemas.trip import FlightOption, HotelOption
from travel_agent.tools.flights import search_flights
from travel_agent.tools.hotels import search_hotels


class FlightAdapter(Protocol):
    """Supplier contract for flight search."""

    def search(self, query: FlightQuery) -> ToolResult[list[FlightOption]]:
        ...


class HotelAdapter(Protocol):
    """Supplier contract for hotel search."""

    def search(self, query: HotelQuery) -> ToolResult[list[HotelOption]]:
        ...


class DemoFlightAdapter:
    """Local candidate adapter; it never claims live inventory."""

    def search(self, query: FlightQuery) -> ToolResult[list[FlightOption]]:
        return ToolResult[list[FlightOption]].ok(
            "local demo adapter",
            search_flights(query.origin, query.destination, query.departure_date),
        )


class DemoHotelAdapter:
    """Local candidate adapter; it never claims live inventory."""

    def search(self, query: HotelQuery) -> ToolResult[list[HotelOption]]:
        budget = "unspecified"
        if query.budget is not None:
            if query.budget.amount is not None:
                budget = (
                    f"{query.budget.amount:g} {query.budget.currency} "
                    f"({query.budget.period or 'total'})"
                )
            elif query.budget.level is not None:
                budget = query.budget.level
        return ToolResult[list[HotelOption]].ok(
            "local demo adapter",
            search_hotels(query.destination, query.nights, budget),
        )


class UnconfiguredFlightAdapter:
    """Safe placeholder for a paid supplier that has not been configured."""

    def __init__(self, provider: str = "paid flight provider"):
        self.provider = provider

    def search(self, _query: FlightQuery) -> ToolResult[list[FlightOption]]:
        return ToolResult[list[FlightOption]].unavailable(
            self.provider,
            "not_configured",
            "付费航班供应商尚未配置 API 凭据。",
        )


class UnconfiguredHotelAdapter:
    """Safe placeholder for a paid supplier that has not been configured."""

    def __init__(self, provider: str = "paid hotel provider"):
        self.provider = provider

    def search(self, _query: HotelQuery) -> ToolResult[list[HotelOption]]:
        return ToolResult[list[HotelOption]].unavailable(
            self.provider,
            "not_configured",
            "付费酒店供应商尚未配置 API 凭据。",
        )


@dataclass(frozen=True)
class TravelAdapters:
    """All replaceable travel suppliers used by one workflow run."""

    flights: FlightAdapter
    hotels: HotelAdapter

    @classmethod
    def demo(cls) -> "TravelAdapters":
        return cls(flights=DemoFlightAdapter(), hotels=DemoHotelAdapter())
