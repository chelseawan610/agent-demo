from datetime import date

from travel_agent.tools.opening_hours import opening_status


def test_opening_hours_supports_weekday_ranges_and_closed_days():
    monday = date(2026, 10, 5)
    saturday = date(2026, 10, 10)

    assert opening_status("Mo-Fr 10:00-18:00", monday) == "open"
    assert opening_status("Mo-Fr 10:00-18:00", saturday) == "unknown"


def test_opening_hours_handles_always_open_and_explicit_off():
    assert opening_status("24/7", date(2026, 10, 5)) == "open"
    assert opening_status("Mo-Su off", date(2026, 10, 5)) == "closed"


def test_opening_hours_does_not_guess_holidays_or_missing_data():
    visit_date = date(2026, 10, 5)

    assert opening_status(None, visit_date) == "unknown"
    assert opening_status("Mo-Fr 10:00-18:00 || PH off", visit_date) == "unknown"
    assert opening_status("Mo-Fr", visit_date) == "unknown"
