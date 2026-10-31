"""Check fixture expectations against the shared request contract, not an LLM."""

from datetime import date

from evals.cases import CASES, HELD_OUT_CASES
from travel_agent.schemas.request import TripRequest


def test_explicit_eval_dates_are_not_already_expired():
    assert all(
        case.start_date is None or date.fromisoformat(case.start_date) >= date.today()
        for case in [*CASES, *HELD_OUT_CASES]
    )


def test_missing_field_expectations_include_the_required_duration_boundary():
    cases = {case.name: case for case in CASES}
    bare = TripRequest(destination="Tokyo")
    flights = TripRequest(origin="Shanghai", destination="Tokyo", requested_services=["flights"])
    assert tuple(bare.missing_fields) == cases["bare_destination_needs_clarification"].missing_fields
    assert tuple(flights.missing_fields) == cases["flight_request_without_date"].missing_fields


def test_explicit_return_interval_fixture_keeps_the_actual_hotel_nights():
    case = next(case for case in CASES if case.name == "full_zh_with_return_date")
    request = TripRequest(
        origin=case.origin, destination=case.destination, start_date=case.start_date,
        return_date=case.return_date, days=case.days,
    )
    assert request.nights == case.nights == 3
