from travel_agent.schemas.request import BudgetSpec
from travel_agent.schemas.search import FlightQuery, HotelQuery
from travel_agent.tools.adapters import (
    TravelAdapters,
    UnconfiguredFlightAdapter,
    UnconfiguredHotelAdapter,
)


def test_demo_adapters_return_explicitly_non_live_candidates():
    adapters = TravelAdapters.demo()

    flights = adapters.flights.search(
        FlightQuery(
            origin="Shanghai",
            destination="Tokyo",
            departure_date="2099-10-01",
        )
    )
    hotels = adapters.hotels.search(
        HotelQuery(
            destination="Tokyo",
            nights=2,
            budget=BudgetSpec(amount=10000),
        )
    )

    assert flights.status == "ok"
    assert all(not item.is_live for item in flights.data)
    assert hotels.status == "ok"
    assert all(not item.is_live for item in hotels.data)


def test_unconfigured_paid_adapters_fail_without_fake_data():
    flight_result = UnconfiguredFlightAdapter("Amadeus").search(
        FlightQuery(
            origin="Shanghai",
            destination="Tokyo",
            departure_date="2099-10-01",
        )
    )
    hotel_result = UnconfiguredHotelAdapter("Booking Demand API").search(
        HotelQuery(destination="Tokyo", nights=2)
    )

    assert flight_result.status == "unavailable"
    assert flight_result.data is None
    assert hotel_result.status == "unavailable"
    assert hotel_result.data is None
