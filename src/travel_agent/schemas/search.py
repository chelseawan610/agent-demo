from datetime import date
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from travel_agent.schemas.evidence import (
    GeoPoint,
    PlaceCandidate,
    RouteSummary,
    SearchLink,
    ToolCallRecord,
    ToolResult,
    WeatherDay,
)
from travel_agent.schemas.request import BudgetSpec, PreferenceSpec
from travel_agent.schemas.trip import ActivityOption, FlightOption, HotelOption
from travel_agent.rag.models import KnowledgeHit


class FlightQuery(BaseModel):
    origin: str
    destination: str
    departure_date: date
    return_date: date | None = None


class HotelQuery(BaseModel):
    destination: str
    nights: int
    budget: BudgetSpec | None = None


class ActivityQuery(BaseModel):
    destination: str
    days: int
    start_date: date | None = None
    preferences: list[str] = Field(default_factory=list)
    preference_details: list[PreferenceSpec] = Field(default_factory=list)


class FoodQuery(BaseModel):
    destination: str
    days: int = 1
    radius_meters: int = 5000
    limit: int = 6
    preferences: list[str] = Field(default_factory=list)


class DestinationQuery(BaseModel):
    destination: str


class WeatherQuery(BaseModel):
    start_date: date
    days: int


class AttractionQuery(BaseModel):
    destination: str
    days: int
    radius_meters: int = 8000
    limit: int = 9


class FlightLinkQuery(BaseModel):
    origin: str
    destination: str
    departure_date: date
    return_date: date | None = None


class HotelLinkQuery(BaseModel):
    destination: str
    check_in_date: date
    check_out_date: date


class SearchPlan(BaseModel):
    """The bounded, inspectable search work derived from a TripRequest."""

    flight_queries: list[FlightQuery] = Field(default_factory=list)
    hotel_query: HotelQuery | None = None
    activity_query: ActivityQuery | None = None
    destination_query: DestinationQuery | None = None
    weather_query: WeatherQuery | None = None
    attraction_query: AttractionQuery | None = None
    food_query: FoodQuery | None = None
    route_enabled: bool = False
    # Driving is the baseline. Transit is an additional profile when
    # explicitly requested; it never replaces the driving route.
    route_modes: list[Literal["driving", "transit"]] = Field(
        default_factory=lambda: ["driving"]
    )
    # Compatibility label for older callers. New code should use route_modes.
    route_mode: Literal["driving", "transit"] = "driving"
    flight_link_query: FlightLinkQuery | None = None
    hotel_link_query: HotelLinkQuery | None = None
    knowledge_query: str | None = None
    knowledge_top_k: int = Field(default=5, ge=1, le=10)
    knowledge_max_chars: int = Field(default=4500, ge=500, le=12000)

    @model_validator(mode="after")
    def normalize_route_modes(self) -> "SearchPlan":
        modes = list(dict.fromkeys(self.route_modes or []))
        if self.route_mode == "transit" and "transit" not in modes:
            modes.append("transit")
        if "transit" in modes and "driving" not in modes:
            modes.insert(0, "driving")
        if not modes:
            modes = ["driving"]
        self.route_modes = modes
        self.route_mode = "transit" if "transit" in modes else "driving"
        return self


class SearchResults(BaseModel):
    """Normalized evidence returned by the tools."""

    flights: list[FlightOption] = Field(default_factory=list)
    hotels: list[HotelOption] = Field(default_factory=list)
    activities: list[ActivityOption] = Field(default_factory=list)
    destination: ToolResult[GeoPoint] | None = None
    weather: ToolResult[list[WeatherDay]] | None = None
    attractions: ToolResult[list[PlaceCandidate]] | None = None
    foods: ToolResult[list[PlaceCandidate]] | None = None
    preference_places: list[ToolResult[PlaceCandidate]] = Field(default_factory=list)
    routes: ToolResult[list[RouteSummary]] | None = None
    route_modes: list[Literal["driving", "transit"]] = Field(
        default_factory=lambda: ["driving"]
    )
    search_links: list[SearchLink] = Field(default_factory=list)
    tool_calls: list[ToolCallRecord] = Field(default_factory=list)
    knowledge: ToolResult[list[KnowledgeHit]] | None = None
