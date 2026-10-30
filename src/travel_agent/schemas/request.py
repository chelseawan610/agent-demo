from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


ServiceName = Literal["flights", "hotels", "activities", "guide", "food"]
FlightType = Literal["round_trip", "one_way"]
TransportMode = Literal["driving", "transit"]
DurationProfile = Literal[
    "day_trip",
    "weekend",
    "short_break",
    "standard",
    "extended",
    "unknown",
]
DEFAULT_SERVICES: list[ServiceName] = ["flights", "hotels", "activities", "guide"]
SERVICE_ORDER: list[ServiceName] = [
    "flights",
    "hotels",
    "activities",
    "guide",
    "food",
]
PlaceType = Literal[
    "landmark", "waterfront", "museum", "park", "restaurant", "hotel", "other"
]


class PreferenceSpec(BaseModel):
    """A user preference plus a provider-friendly lookup query."""

    label: str
    search_query: str
    kind: Literal["place", "interest", "food", "accessibility", "service", "other"] = "other"
    place_name: str | None = Field(
        default=None, description="Canonical named place without city qualifiers; null for non-place preferences."
    )
    place_type: PlaceType = "other"
    # Intake maps natural language to a stable planning enum. The workflow
    # consumes this field instead of searching Chinese/English keywords.
    planning_hint: Literal[
        "venue_mix", "indoor_only", "outdoor_only", "pace", "none"
    ] = "none"
    # The trip day the user asked this place to fall on ("第3天想去双子塔").
    # 1-based. Without it the request is silently flattened to "sometime
    # during the trip", and the user cannot tell the difference in the output.
    preferred_day: int | None = Field(
        default=None,
        ge=1,
        le=14,
        description="1-based trip day the user explicitly asked for; null when unspecified.",
    )


class BudgetSpec(BaseModel):
    """A normalized budget preference extracted from natural language."""

    amount: float | None = None
    currency: str = "CNY"
    level: Literal["low", "medium", "high"] | None = None
    period: Literal["total", "per_day", "per_night"] | None = None

    @model_validator(mode="after")
    def default_period_for_amount(self) -> "BudgetSpec":
        """A bare numeric budget means total budget unless stated otherwise."""

        if self.amount is not None and self.period is None:
            self.period = "total"
        return self


def parse_calendar_date(value: object, *, today: date | None = None) -> date | None:
    """Parse an explicit calendar value returned by an intake model.

    This is deliberately format-based, not language-keyword matching. Natural
    language interpretation stays in the model; this helper only protects the
    typed boundary from common separators and omitted-year calendar notation.
    """

    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if not isinstance(value, str):
        raise ValueError("date must be a calendar string")

    text = value.strip()
    full_year_formats = (
        "%Y-%m-%d",
        "%Y.%m.%d",
        "%Y/%m/%d",
        "%Y年%m月%d日",
        "%Y年%m月%d号",
        "%y.%m.%d",
        "%y-%m-%d",
    )
    for date_format in full_year_formats:
        try:
            parsed = datetime.strptime(text, date_format).date()
            if date_format.startswith("%y"):
                parsed = parsed.replace(year=2000 + parsed.year % 100)
            return parsed
        except ValueError:
            continue

    reference = today or date.today()
    month_day_formats = ("%m.%d", "%m/%d", "%m月%d日", "%m月%d号")
    for date_format in month_day_formats:
        try:
            parsed = datetime.strptime(text, date_format)
            candidate = date(reference.year, parsed.month, parsed.day)
            if candidate < reference:
                candidate = date(reference.year + 1, parsed.month, parsed.day)
            return candidate
        except ValueError:
            continue
    raise ValueError("date must use a supported calendar format")


# User-facing names for the fields intake still needs. They are also the
# keys the clarification questions and the evidence exemptions are looked up
# by, so they are defined once here rather than written out at each use.
DESTINATION = "destination"
ORIGIN = "origin city"
DEPARTURE_DATE = "departure date"
CHECK_IN_DATE = "check-in date"
TRIP_LENGTH_OR_RETURN = "trip length, or return/check-out date"
RETURN_OR_TRIP_LENGTH = "return date, or trip length"
TRIP_LENGTH_OR_NIGHTS = "trip length, or nights/check-out date"
NIGHTS_OR_CHECK_OUT = "number of nights, or check-out date"


class TripRequest(BaseModel):
    """Normalized travel requirements extracted from a user message."""

    requested_services: list[ServiceName] = Field(
        default_factory=lambda: DEFAULT_SERVICES.copy()
    )
    status: Literal["ready", "needs_clarification"] = "needs_clarification"
    # The intake model classifies the user's prose so the planner can answer
    # in the same language. It is optional for old checkpoints and hand-built
    # test requests; those use the original text as a fallback.
    response_language: Literal["Chinese", "English"] | None = None
    origin: str | None = None
    destination: str | None = None
    start_date: date | None = None
    return_date: date | None = None
    flight_type: FlightType | None = None
    # ``transport_mode`` is kept for compatibility with older callers. New
    # code should use ``transport_modes`` so a request can ask for both
    # driving and public-transit estimates.
    transport_mode: TransportMode | None = None
    transport_modes: list[TransportMode] = Field(
        default_factory=lambda: ["driving"]
    )
    days: int | None = None
    nights: int | None = None
    nights_explicit: bool = False
    duration_profile: DurationProfile = "unknown"
    budget: BudgetSpec | None = None
    preferences: list[str] = Field(default_factory=list)
    preference_details: list[PreferenceSpec] = Field(default_factory=list)
    missing_fields: list[str] = Field(default_factory=list)
    # Field names the user actually spoke in the message this object was
    # extracted from.  Clarification merging uses it to tell a deliberate user
    # correction ("改成4天") apart from a model drifting on an earlier fact.
    # An empty list is the safe default: merging then keeps confirmed values.
    stated_fields: list[str] = Field(default_factory=list)
    # For each filled field, the verbatim span of the user's message that
    # supports it.  The program verifies the span really occurs in the user's
    # text, which is an exact check rather than a second model judgement; a
    # value that cannot be cited is cleared and returned to the ordinary
    # ``required_missing_fields`` clarification flow.  Services are keyed
    # ``service:<name>``.
    field_evidence: dict[str, str] = Field(default_factory=dict)

    def required_missing_fields(self, *, services: set[str] | None = None) -> list[str]:
        """Return only fields that must be supplied before external search.

        The model may leave optional planning preferences unknown. This method
        defines the hard boundary shared by intake, clarification, and tests:
        destination is always required; flights add origin and departure date;
        hotel-only requests add check-in date. A round-trip flight or a plan
        containing flights/hotels also needs one duration boundary: days,
        nights, or a return/check-out date. Budget, transport mode, and
        preferences remain optional.
        """

        active_services = services or set(self.requested_services)
        missing: list[str] = []
        if not self.destination:
            missing.append(DESTINATION)
        if "flights" in active_services:
            if not self.origin:
                missing.append(ORIGIN)
            if not self.start_date:
                missing.append(DEPARTURE_DATE)
        elif "hotels" in active_services and not self.start_date:
            missing.append(CHECK_IN_DATE)

        itinerary_scope = bool(active_services & {"activities", "guide", "food"})
        has_duration_boundary = (
            self.days is not None
            or self.nights is not None
            or self.return_date is not None
        )
        if not has_duration_boundary:
            if {"flights", "hotels"} <= active_services:
                boundary_label = TRIP_LENGTH_OR_RETURN
            elif "flights" in active_services and self.flight_type != "one_way":
                boundary_label = RETURN_OR_TRIP_LENGTH
            elif "hotels" in active_services and itinerary_scope:
                boundary_label = TRIP_LENGTH_OR_NIGHTS
            elif "hotels" in active_services and self.start_date:
                boundary_label = NIGHTS_OR_CHECK_OUT
            elif "flights" in active_services and itinerary_scope:
                boundary_label = RETURN_OR_TRIP_LENGTH
            else:
                boundary_label = None
            if boundary_label is not None:
                missing.append(boundary_label)
        return missing

    def _resolve_duration(self, services: set[str]) -> None:
        """Resolve a planning horizon before SearchPlan creates tool queries."""

        itinerary_scope = bool(services & {"activities", "guide", "food"})
        if "hotels" not in services and not self.nights_explicit:
            # A model may copy days into nights even for a flight or guide
            # request. Nights only affect search when they are a user-supplied
            # boundary or an accommodation is in scope.
            self.nights = None
        has_explicit_boundary = any(
            value is not None for value in (self.days, self.nights, self.return_date)
        )
        requires_boundary = (
            "hotels" in services
            or ("flights" in services and self.flight_type != "one_way")
            or (bool(services & {"flights", "hotels"}) and itinerary_scope)
        )
        if requires_boundary and not has_explicit_boundary:
            # Do not manufacture a duration while the intake state machine is
            # still asking the user for one. Keeping these fields null also
            # lets a later clarification answer replace the missing value.
            return
        if not itinerary_scope:
            # A one-way flights-only request does not need a fake duration.
            if "hotels" in services and self.nights is None:
                if self.start_date is not None and self.return_date is not None:
                    span = (self.return_date - self.start_date).days
                    self.nights = span if span > 0 else 1
                elif self.days is not None and not self.nights_explicit:
                    self.nights = max(self.days - 1, 0)
                else:
                    self.nights = 1
            return

        profile_days = {
            "day_trip": 1,
            "weekend": 2,
            "short_break": 3,
            "standard": 4,
            "extended": 7,
        }.get(self.duration_profile)
        date_derived_days = None
        if self.start_date is not None and self.return_date is not None:
            # The project treats a date interval as its elapsed duration:
            # Oct 20 -> Oct 23 is 3 days, not 4 calendar labels.
            span = (self.return_date - self.start_date).days
            if span > 0:
                date_derived_days = span

        if self.days is None:
            if self.nights is not None:
                self.days = self.nights + 1
            elif date_derived_days is not None:
                self.days = date_derived_days
            elif profile_days is not None:
                self.days = profile_days
            else:
                # A complete flight+hotel plan needs a bounded search window;
                # a guide-only request can remain a one-day experience.
                self.days = 2 if {"flights", "hotels"} <= services else 1

        if "hotels" in services and not self.nights_explicit:
            if self.start_date is not None and self.return_date is not None:
                self.nights = max((self.return_date - self.start_date).days, 0)
            else:
                self.nights = max(self.days - 1, 0)

    @field_validator("start_date", "return_date", mode="before")
    @classmethod
    def normalize_calendar_date(cls, value: object) -> date | None:
        """Normalize the agent's ISO-like date without parsing user language.

        Natural-language interpretation belongs to the intake agent. This
        validator only accepts the structured value produced by that agent and
        makes zero-padded and non-zero-padded ISO dates share one representation.
        """

        if value is None:
            return None
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, date):
            return value
        return parse_calendar_date(value)

    @field_validator("transport_modes", mode="before")
    @classmethod
    def normalize_transport_modes(cls, value: object) -> object:
        """Accept omitted or legacy single-mode model output."""

        if value is None:
            return ["driving"]
        if isinstance(value, str):
            return [value]
        return value

    @model_validator(mode="before")
    @classmethod
    def normalize_preferences(cls, value: object) -> object:
        """Keep the old string list while accepting richer model output."""

        if not isinstance(value, dict):
            return value
        values = dict(value)
        raw_preferences = values.get("preferences") or []
        details = values.get("preference_details") or []
        normalized_labels: list[str] = []
        normalized_details: list[dict[str, str] | PreferenceSpec] = []
        for item in raw_preferences:
            if isinstance(item, str):
                normalized_labels.append(item)
            elif isinstance(item, dict) and item.get("label"):
                if item.get("kind") != "service":
                    normalized_details.append(item)
        for item in details:
            if isinstance(item, PreferenceSpec):
                if item.kind != "service":
                    normalized_details.append(item)
            elif isinstance(item, dict) and item.get("label"):
                if item.get("kind") != "service":
                    normalized_details.append(item)
        if normalized_details:
            # Typed details preserve the user's wording; translated provider
            # queries stay in search_query instead of becoming extra labels.
            normalized_labels = [
                item.label
                if isinstance(item, PreferenceSpec)
                else str(item["label"])
                for item in normalized_details
            ]
        values["preferences"] = list(dict.fromkeys(normalized_labels))
        if not normalized_details:
            normalized_details = [
                {"label": label, "search_query": label, "kind": "other"}
                for label in values["preferences"]
            ]
        values["preference_details"] = normalized_details
        return values

    @field_validator("budget", mode="before")
    @classmethod
    def normalize_budget(cls, value: object) -> object:
        """Accept model outputs such as 10000 or '中等' as well as objects."""

        if value is None or isinstance(value, BudgetSpec):
            return value
        if isinstance(value, (int, float)):
            return {"amount": float(value), "period": "total"}
        return value

    @model_validator(mode="after")
    def infer_status(self) -> "TripRequest":
        """Compute readiness from required fields instead of trusting the model."""

        missing: list[str] = []
        services = set(self.requested_services)
        if not services:
            services = set(DEFAULT_SERVICES)
        # Service order is a presentation detail; canonicalize it so every
        # downstream plan and evaluation sees the same typed sequence.
        self.requested_services = [
            service for service in SERVICE_ORDER if service in services
        ]
        modes = list(dict.fromkeys(self.transport_modes or []))
        # Older callers set only the compatibility field ``transport_mode``;
        # their default list is ``["driving"]``. Preserve the old behavior for
        # that shape, while respecting an explicit ``["transit"]`` request.
        if self.transport_mode == "transit" and modes == ["driving"]:
            modes.append("transit")
        if not modes:
            modes = ["transit" if self.transport_mode == "transit" else "driving"]
        self.transport_modes = modes
        self.transport_mode = "transit" if "transit" in modes else "driving"
        if "flights" in services:
            # Round trip is the safe default for a flight request. The intake
            # model may set one_way only when the user explicitly says so.
            self.flight_type = self.flight_type or "round_trip"
        else:
            self.flight_type = None
        if "hotels" in services and "guide" in services:
            # A guide is an itinerary request, so hotel + guide always carries
            # the activities service even if the model omitted that enum.
            services.add("activities")
            self.requested_services = [
                service
                for service in SERVICE_ORDER
                if service in services
            ]
        date_required = "flights" in services or "hotels" in services
        invalid_start_date = (
            date_required
            and self.start_date is not None
            and self.start_date < date.today()
        )
        if invalid_start_date:
            # A model-extracted past date must never reach live search tools.
            # Clear it so the existing clarification state machine asks again.
            self.start_date = None
            self.return_date = None
        if invalid_start_date:
            # The invalid-date message is more useful than a generic missing
            # date message, so keep it as the only date requirement below.
            missing.extend(self.required_missing_fields(services=services))
            missing = [
                field for field in missing if field not in {DEPARTURE_DATE, CHECK_IN_DATE}
            ]
            date_label = DEPARTURE_DATE if "flights" in services else CHECK_IN_DATE
            missing.append(f"{date_label} (cannot be in the past)")
        else:
            missing.extend(self.required_missing_fields(services=services))

        invalid_return_date = (
            self.start_date is not None
            and self.return_date is not None
            and self.return_date <= self.start_date
        )
        if invalid_return_date:
            missing.append("return date (must be after departure date)")

        self._resolve_duration(services)

        detail_labels = {detail.label for detail in self.preference_details}
        for label in self.preferences:
            if label not in detail_labels:
                self.preference_details.append(
                    PreferenceSpec(label=label, search_query=label)
                )

        self.missing_fields = list(dict.fromkeys(missing))
        self.status = "ready" if not missing else "needs_clarification"
        return self
