from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


ServiceName = Literal["flights", "hotels", "activities", "guide", "food"]
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


class PreferenceSpec(BaseModel):
    """A user preference plus a provider-friendly lookup query."""

    label: str
    search_query: str
    kind: Literal["place", "interest", "food", "accessibility", "service", "other"] = "other"


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


class TripRequest(BaseModel):
    """Normalized travel requirements extracted from a user message."""

    requested_services: list[ServiceName] = Field(
        default_factory=lambda: DEFAULT_SERVICES.copy()
    )
    status: Literal["ready", "needs_clarification"] = "needs_clarification"
    origin: str | None = None
    destination: str | None = None
    start_date: date | None = None
    return_date: date | None = None
    transport_mode: Literal["driving", "transit"] | None = None
    days: int | None = None
    nights: int | None = None
    duration_profile: DurationProfile = "unknown"
    budget: BudgetSpec | None = None
    preferences: list[str] = Field(default_factory=list)
    preference_details: list[PreferenceSpec] = Field(default_factory=list)
    missing_fields: list[str] = Field(default_factory=list)

    def required_missing_fields(self, *, services: set[str] | None = None) -> list[str]:
        """Return only fields that must be supplied before external search.

        The model may leave optional planning preferences unknown. This method
        defines the hard boundary shared by intake, clarification, and tests:
        destination is always required; flights add origin and departure date;
        hotel-only requests add check-in date. Duration, budget, return date,
        transport mode, and preferences are deliberately optional.
        """

        active_services = services or set(self.requested_services)
        missing: list[str] = []
        if not self.destination:
            missing.append("目的地")
        if "flights" in active_services:
            if not self.origin:
                missing.append("出发城市")
            if not self.start_date:
                missing.append("出发日期")
        elif "hotels" in active_services and not self.start_date:
            missing.append("入住日期")
        return missing

    def _resolve_duration(self, services: set[str]) -> None:
        """Resolve a planning horizon before SearchPlan creates tool queries."""

        itinerary_scope = bool(services & {"activities", "guide", "food"})
        if not itinerary_scope:
            # A one-way flights-only request does not need a fake duration;
            # hotel-only still needs one concrete stay query.
            if "hotels" in services and self.nights is None:
                if self.start_date is not None and self.return_date is not None:
                    span = (self.return_date - self.start_date).days
                    self.nights = span if span > 0 else 1
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
            span = (self.return_date - self.start_date).days + 1
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

        if "hotels" in services and self.nights is None:
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
                field for field in missing if field not in {"出发日期", "入住日期"}
            ]
            date_label = "出发日期" if "flights" in services else "入住日期"
            missing.append(f"{date_label}（不能早于今天）")
        else:
            missing.extend(self.required_missing_fields(services=services))

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
