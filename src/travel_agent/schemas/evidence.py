from datetime import date, datetime, timezone
from typing import Generic, Literal, TypeVar

from pydantic import BaseModel, Field, model_validator


T = TypeVar("T")

VenueType = Literal["indoor", "outdoor", "mixed", "unknown"]


def classify_error_code(error_code: str | None) -> str | None:
    """Map provider/tool errors to stable categories for evaluation.

    Error messages vary by provider, but these categories are part of the
    harness contract.  Evals can therefore compare runs without depending on
    English or Chinese exception text.
    """

    if not error_code:
        return None
    if error_code in {"timeout", "connection_error", "http_408", "http_425", "http_429"}:
        return "provider_transient"
    if error_code.startswith("http_5"):
        return "provider_transient"
    if error_code in {"not_configured", "authentication_error", "http_401", "http_403"}:
        return "provider_configuration"
    if error_code in {"invalid_json", "invalid_response", "schema_error"}:
        return "provider_contract"
    if error_code in {"not_found", "out_of_area", "not_relevant", "type_mismatch", "ambiguous_place", "no_route"}:
        return "provider_no_match"
    if error_code in {"missing_dependency", "unsupported"}:
        return "tool_configuration"
    if error_code in {"skipped_dependency", "no_input", "no_routes"}:
        return "workflow_dependency"
    if error_code == "unexpected_error":
        return "tool_implementation"
    return "provider_error"


def is_retryable_error(error_code: str | None) -> bool | None:
    """Return whether a normalized error is safe to retry."""

    if not error_code:
        return None
    if error_code in {"timeout", "connection_error", "http_408", "http_425", "http_429"}:
        return True
    return error_code.startswith("http_5")


def infer_tool_stage(tool_name: str) -> str:
    """Infer the stable workflow layer for a tool event."""

    if tool_name == "retrieve_knowledge":
        return "retrieval"
    if tool_name in {"resolve_destination", "resolve_preference_place"}:
        return "geocoding"
    if tool_name == "get_weather_forecast":
        return "weather"
    if tool_name in {"search_attractions", "search_food"}:
        return "places"
    if "route" in tool_name:
        return "routing"
    if tool_name.startswith("search_") or tool_name.startswith("build_"):
        return "inventory"
    return "tool"


class ToolResult(BaseModel, Generic[T]):
    """One typed tool outcome, including failures that allow partial results."""

    status: Literal["ok", "unavailable", "skipped"]
    provider: str
    data: T | None = None
    error_code: str | None = None
    message: str | None = None
    retrieved_at: datetime | None = None
    attribution: str | None = None
    error_category: str | None = None
    retryable: bool | None = None
    attempts: int | None = None
    cache_hit: bool | None = None
    stale: bool = False

    @classmethod
    def ok(
        cls,
        provider: str,
        data: T,
        *,
        attribution: str | None = None,
        attempts: int | None = None,
        cache_hit: bool | None = None,
        stale: bool = False,
    ) -> "ToolResult[T]":
        return cls(
            status="ok",
            provider=provider,
            data=data,
            retrieved_at=datetime.now(timezone.utc),
            attribution=attribution,
            attempts=attempts,
            cache_hit=cache_hit,
            stale=stale,
        )

    @classmethod
    def unavailable(
        cls,
        provider: str,
        error_code: str,
        message: str,
        *,
        attribution: str | None = None,
        error_category: str | None = None,
        retryable: bool | None = None,
        attempts: int | None = None,
        stale: bool = False,
    ) -> "ToolResult[T]":
        return cls(
            status="unavailable",
            provider=provider,
            error_code=error_code,
            message=message,
            retrieved_at=datetime.now(timezone.utc),
            attribution=attribution,
            error_category=error_category or classify_error_code(error_code),
            retryable=(
                is_retryable_error(error_code)
                if retryable is None
                else retryable
            ),
            attempts=attempts,
            stale=stale,
        )

    @classmethod
    def skipped(
        cls,
        provider: str,
        message: str,
        *,
        error_code: str = "skipped_dependency",
    ) -> "ToolResult[T]":
        return cls(
            status="skipped",
            provider=provider,
            message=message,
            error_code=error_code,
            error_category=classify_error_code(error_code),
            retryable=False,
        )


class GeoPoint(BaseModel):
    """A coordinate whose order is explicit everywhere except OSRM URLs."""

    latitude: float = Field(ge=-90, le=90, allow_inf_nan=False)
    longitude: float = Field(ge=-180, le=180, allow_inf_nan=False)


class PlaceCandidate(BaseModel):
    name: str
    category: str = "attraction"
    point: GeoPoint | None = None
    provider_id: str
    source: str
    verified: bool = True
    # A verified geographic feature can still be an area centroid, not an entrance.
    route_eligible: bool = True
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
    disclaimer: str = "A search link only; no live price or inventory was read."


class ToolCallRecord(BaseModel):
    run_id: str | None = None
    trace_id: str | None = None
    tool_name: str
    started_at: datetime | None = None
    inputs: dict[str, object] = Field(default_factory=dict)
    status: Literal["ok", "unavailable", "skipped"]
    elapsed_ms: float
    provider: str
    stage: str = "tool"
    error_code: str | None = None
    error_category: str | None = None
    retryable: bool | None = None
    attempts: int | None = None
    cache_hit: bool | None = None
    dedupe_key: str | None = None
    message: str | None = None

    @model_validator(mode="after")
    def normalize_diagnostics(self) -> "ToolCallRecord":
        """Fill diagnostics for records created by tests, fixtures, or adapters."""

        if self.stage == "tool":
            self.stage = infer_tool_stage(self.tool_name)
        if self.error_category is None:
            self.error_category = classify_error_code(self.error_code)
        if self.retryable is None:
            self.retryable = is_retryable_error(self.error_code)
        return self
