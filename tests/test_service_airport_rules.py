from app.services.service_airport_rules import (
    flight_route_matches_service_airport,
    resolve_service_airport_iata,
)


def test_arrival_uses_destination():
    assert resolve_service_airport_iata("ARRIVAL", origin="LHR", destination="DEL") == "DEL"


def test_departure_uses_origin():
    assert resolve_service_airport_iata("DEPARTURE", origin="DEL", destination="LHR") == "DEL"


def test_transit_uses_transit_airport():
    assert resolve_service_airport_iata("TRANSIT", origin="DXB", destination="LHR", transit="DEL") == "DEL"


def test_arrival_does_not_require_origin_in_network():
    assert resolve_service_airport_iata("arrival", origin="LHR", destination="BOM") == "BOM"


def test_flight_mismatch_arrival():
    ok, msg = flight_route_matches_service_airport("ARRIVAL", "DEL", actual_origin="LHR", actual_destination="CDG")
    assert ok is False
    assert "CDG" in msg
    assert "DEL" in msg


def test_flight_match_arrival():
    ok, msg = flight_route_matches_service_airport("ARRIVAL", "DEL", actual_origin="LHR", actual_destination="DEL")
    assert ok is True
    assert msg == ""


def test_flight_mismatch_departure():
    ok, msg = flight_route_matches_service_airport("DEPARTURE", "DEL", actual_origin="CDG", actual_destination="LHR")
    assert ok is False
    assert "CDG" in msg
