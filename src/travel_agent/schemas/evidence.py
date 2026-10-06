from datetime import date, datetime, timezone
from typing import Generic, Literal, TypeVar

from pydantic import BaseModel, Field


T = TypeVar("T")

VenueType = Literal["indoor", "outdoor", "mixed", "unknown"]


class ToolResult(BaseModel, Generic[T]):
    """One typed tool outcome, including failures that allow partial results."""

    status: Literal["ok", "unavailable", "skipped"]
    provider: str
    data: T | None = None
    error_code: str | None = None
    message: str | None = None
    retrieved_at: datetime | None = None
    attribution: str | None = None

    @classmethod
    def ok(
        cls,
        provider: str,
        data: T,
        *,
        attribution: str | None = None,
    ) -> "ToolResult[T]":
        return cls(
            status="ok",
            provider=provider,
            data=data,
            retrieved_at=datetime.now(timezone.utc),
            attribution=attribution,
        )

    @classmethod
    def unavailable(
        cls,
        provider: str,
        error_code: str,
        message: str,
        *,
        attribution: str | None = None,
    ) -> "ToolResult[T]":
        return cls(
            status="unavailable",
            provider=provider,
            error_code=error_code,
            message=message,
            retrieved_at=datetime.now(timezone.utc),
            attribution=attribution,
        )

    @classmethod
    def skipped(cls, provider: str, message: str) -> "ToolResult[T]":
        return cls(status="skipped", provider=provider, message=message)


class GeoPoint(BaseModel):
    """A coordinate whose order is explicit everywhere except OSRM URLs."""

    latitude: float
    longitude: float


class PlaceCandidate(BaseModel):
    name: str
    category: str = "attraction"
    point: GeoPoint | None = None
    provider_id: str
    source: str
    verified: bool = True
    description: str = ""
    opening_hours: str | None = None
    website: str | None = None
    address: str | None = None
    phone: str | None = None
    wheelchair_accessible: bool | None = None
    opening_hours_source: str | None = None
    estimated_visit_minutes: int | None = None
    venue_type: VenueType = "unknown"
    cuisine: str | None = None


class WeatherDay(BaseModel):
    date: date
    weather_code: int
    temperature_max_c: float
    temperature_min_c: float
    precipitation_probability_max: int | None = None


class RouteSummary(BaseModel):
    day: int
    ordered_stops: list[str] = Field(default_factory=list)
    distance_meters: float
    duration_seconds: float
    profile: Literal["driving", "transit"] = "driving"


class SearchLink(BaseModel):
    service: Literal["flights", "hotels"]
    provider: str
    url: str
    departure_date: date | None = None
    return_date: date | None = None
    check_in_date: date | None = None
    check_out_date: date | None = None
    disclaimer: str = "仅生成搜索入口，未自动读取实时价格或库存。"


class ToolCallRecord(BaseModel):
    run_id: str | None = None
    trace_id: str | None = None
    tool_name: str
    started_at: datetime | None = None
    inputs: dict[str, object] = Field(default_factory=dict)
    status: Literal["ok", "unavailable", "skipped"]
    elapsed_ms: float
    provider: str
    error_code: str | None = None
    message: str | None = None
