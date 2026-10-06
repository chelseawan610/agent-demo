from datetime import date
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from travel_agent.schemas.request import BudgetSpec
from travel_agent.schemas.review import ReviewResult
from travel_agent.schemas.evidence import (
    GeoPoint,
    PlaceCandidate,
    RouteSummary,
    SearchLink,
    ToolCallRecord,
    WeatherDay,
)
from travel_agent.rag.models import KnowledgeCitation


class FlightOption(BaseModel):
    """A flight option returned by the demo flight tool."""

    origin: str
    destination: str
    date: date
    airline: str
    departure: str
    arrival: str
    price: str | int | float
    source_type: Literal["demo", "live"] = "demo"
    is_live: bool = False


class HotelOption(BaseModel):
    """A hotel option returned by the demo hotel tool."""

    name: str
    area: str
    nights: int
    price_per_night: str | int | float
    rating: str | int | float
    source_type: Literal["demo", "live"] = "demo"
    is_live: bool = False


class ActivityItem(BaseModel):
    """One concrete activity shown in a daily itinerary."""

    name: str
    description: str = ""
    time_of_day: str = ""
    location: str = ""
    notes: str = ""
    point: GeoPoint | None = None
    provider_id: str | None = None
    source: str = ""
    verified: bool = False
    opening_hours: str | None = None
    opening_status: Literal["open", "closed", "unknown"] = "unknown"
    website: str | None = None
    estimated_visit_minutes: int | None = None
    venue_type: Literal["indoor", "outdoor", "mixed", "unknown"] = "unknown"
    cuisine: str | None = None


class ActivityOption(ActivityItem):
    """One activity candidate returned by the search tool."""

    destination: str
    day: int


class DayPlan(BaseModel):
    """Activities planned for one travel day."""

    day: int
    title: str
    activities: list[ActivityItem] = Field(default_factory=list)


class TripPlan(BaseModel):
    """The structured final result produced by the travel agent."""

    status: Literal["complete", "needs_clarification"] = "complete"
    destination: str
    days: int
    summary: str
    budget: BudgetSpec | None = None
    transport_mode: Literal["driving", "transit"] = "driving"
    transport_modes: list[Literal["driving", "transit"]] = Field(
        default_factory=lambda: ["driving"]
    )
    preferences: list[str] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)
    flights: list[FlightOption] = Field(default_factory=list)
    hotels: list[HotelOption] = Field(default_factory=list)
    itinerary: list[DayPlan] = Field(default_factory=list)
    food: list[PlaceCandidate] = Field(default_factory=list)
    weather: list[WeatherDay] = Field(default_factory=list)
    places: list[PlaceCandidate] = Field(default_factory=list)
    routes: list[RouteSummary] = Field(default_factory=list)
    search_links: list[SearchLink] = Field(default_factory=list)
    tool_status: list[ToolCallRecord] = Field(default_factory=list)
    data_sources: list[str] = Field(default_factory=list)
    knowledge_sources: list[KnowledgeCitation] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    review: ReviewResult | None = None

    @model_validator(mode="after")
    def infer_clarification_status(self) -> "TripPlan":
        """Mark incomplete requests explicitly instead of treating them as plans."""

        if self.days <= 0:
            self.status = "needs_clarification"
        else:
            self.status = "complete"
        modes = list(dict.fromkeys(self.transport_modes or []))
        if self.transport_mode == "transit" and "transit" not in modes:
            modes.append("transit")
        if "transit" in modes and "driving" not in modes:
            modes.insert(0, "driving")
        if not modes:
            modes = ["driving"]
        self.transport_modes = modes
        self.transport_mode = "transit" if "transit" in modes else "driving"
        return self
