"""Small, conservative reader for common OpenStreetMap opening-hours tags.

OpenStreetMap's full ``opening_hours`` grammar is intentionally rich.  The
workflow only needs a safe answer for simple weekly schedules; unsupported
holiday or date-specific expressions remain ``unknown`` instead of being
treated as open.
"""

from datetime import date
from typing import Literal


OpeningStatus = Literal["open", "closed", "unknown"]

_DAY_INDEX = {
    "Mo": 0,
    "Tu": 1,
    "We": 2,
    "Th": 3,
    "Fr": 4,
    "Sa": 5,
    "Su": 6,
}


def opening_status(opening_hours: str | None, visit_date: date | None) -> OpeningStatus:
    """Return ``open``, ``closed`` or ``unknown`` for one visit date.

    The function deliberately does not guess when the provider omitted hours
    or used a rule we do not support.  This keeps missing OSM data visible to
    the user and prevents an itinerary from claiming a venue is open.
    """

    if not opening_hours or visit_date is None:
        return "unknown"
    value = opening_hours.strip()
    if value == "24/7":
        return "open"
    if "||" in value or "PH" in value or "SH" in value:
        return "unknown"

    matched_status: OpeningStatus | None = None
    for raw_rule in value.split(";"):
        rule = raw_rule.strip()
        if not rule:
            continue
        parts = rule.split(None, 1)
        if not _matches_weekday(parts[0], visit_date.weekday()):
            continue
        detail = parts[1].strip().casefold() if len(parts) == 2 else ""
        if detail.startswith(("off", "closed")):
            matched_status = "closed"
        elif detail and detail != "unknown":
            matched_status = "open"
        else:
            return "unknown"
    return matched_status or "unknown"


def _matches_weekday(expression: str, weekday: int) -> bool:
    """Match simple ``Mo-Fr`` or comma-separated weekday expressions."""

    for item in expression.split(","):
        token = item.strip()
        if "-" in token:
            first, last = (part.strip() for part in token.split("-", 1))
            if first not in _DAY_INDEX or last not in _DAY_INDEX:
                return False
            start = _DAY_INDEX[first]
            end = _DAY_INDEX[last]
            if start <= end and start <= weekday <= end:
                return True
            if start > end and (weekday >= start or weekday <= end):
                return True
        elif token in _DAY_INDEX and _DAY_INDEX[token] == weekday:
            return True
    return False
