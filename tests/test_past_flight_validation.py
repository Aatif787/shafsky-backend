import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient
from fastapi import HTTPException

from app.main import app
from app.flight.schemas import (
    FlightStatusData, AirlineDetails, FlightInfo, LocationEndpointDetails,
    DurationDetails, AircraftDetails
)
from app.services.booking_service import BookingService
from app.schemas.booking import BookingCreate

client = TestClient(app)


def build_mock_flight(
    flight_num: str = "AI2424",
    status: str = "Scheduled",
    dep_time: str = "07:00",
    arr_time: str = "09:15",
    dep_ap: str = "BOM",
    arr_ap: str = "DEL",
    dep_tz: str = "Asia/Kolkata",
    arr_tz: str = "Asia/Kolkata",
) -> FlightStatusData:
    return FlightStatusData(
        airline=AirlineDetails(name="Air India", iata="AI"),
        flight=FlightInfo(number=flight_num[2:], iata=flight_num),
        departure=LocationEndpointDetails(
            airport=dep_ap,
            airport_name="Mumbai Airport",
            city="Mumbai",
            scheduled=dep_time,
            timezone=dep_tz,
        ),
        arrival=LocationEndpointDetails(
            airport=arr_ap,
            airport_name="Delhi Airport",
            city="Delhi",
            scheduled=arr_time,
            timezone=arr_tz,
        ),
        duration=DurationDetails(minutes=135, formatted="2h 15m"),
        aircraft=AircraftDetails(),
        status=status,
    )


class TestPastFlightApiValidation:
    """Tests for POST /api/flight/validate past flight detection."""

    @patch("app.flight.service.FlightIntelligenceService.validate_flight")
    def test_past_departure_flight_blocked(self, mock_validate):
        # AI2424 on 2026-11-01 at 07:00 BOM (past relative to current time 11:40)
        mock_validate.return_value = build_mock_flight(dep_time="2026-11-01T07:00:00")

        res = client.post(
            "/api/flight/validate",
            json={
                "flightNum": "AI2424",
                "departDate": "2026-11-01",
                "direction": "departure",
                "originCode": "BOM",
                "destCode": "DEL",
                "airportCode": "BOM",
            },
        )
        assert res.status_code == 400
        data = res.json()
        assert data["success"] is False
        assert data["code"] == "FLIGHT_EXPIRED"
        assert "already departed" in data["error"]
        assert data["data"]["is_expired"] is True
        assert data["data"]["valid"] is False

    @patch("app.flight.service.FlightIntelligenceService.validate_flight")
    def test_past_arrival_flight_blocked(self, mock_validate):
        # AI2424 on 2026-11-01 arrived at 09:15 DEL
        mock_validate.return_value = build_mock_flight(arr_time="2026-11-01T09:15:00")

        res = client.post(
            "/api/flight/validate",
            json={
                "flightNum": "AI2424",
                "departDate": "2026-11-01",
                "direction": "arrival",
                "originCode": "BOM",
                "destCode": "DEL",
                "airportCode": "DEL",
            },
        )
        assert res.status_code == 400
        data = res.json()
        assert data["success"] is False
        assert data["code"] == "FLIGHT_EXPIRED"
        assert "already arrived" in data["error"]
        assert data["data"]["is_expired"] is True

    @patch("app.flight.service.FlightIntelligenceService.validate_flight")
    def test_landed_status_blocks_flight(self, mock_validate):
        # Even if scheduled time looks future, if live status is Landed, it is blocked
        future_str = (datetime.now(timezone.utc) + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%S")
        mock_validate.return_value = build_mock_flight(status="Landed", dep_time=future_str, arr_time=future_str)

        res = client.post(
            "/api/flight/validate",
            json={
                "flightNum": "AI2424",
                "departDate": datetime.now().strftime("%Y-%m-%d"),
                "direction": "arrival",
                "airportCode": "DEL",
            },
        )
        assert res.status_code == 400
        data = res.json()
        assert data["code"] == "FLIGHT_EXPIRED"
        assert "already arrived" in data["error"]

    @patch("app.flight.service.FlightIntelligenceService.validate_flight")
    def test_future_flight_allowed(self, mock_validate):
        # Flight departing 2 days in future
        future_dt = datetime.now(timezone.utc) + timedelta(days=2)
        future_str = future_dt.strftime("%Y-%m-%dT14:00:00")
        mock_validate.return_value = build_mock_flight(dep_time=future_str, arr_time=future_str)

        res = client.post(
            "/api/flight/validate",
            json={
                "flightNum": "AI2424",
                "departDate": future_dt.strftime("%Y-%m-%d"),
                "direction": "departure",
                "originCode": "BOM",
                "destCode": "DEL",
                "airportCode": "BOM",
            },
        )
        assert res.status_code == 200
        data = res.json()
        assert data["success"] is True
        assert data["data"]["valid"] is True
        assert data["data"]["is_bookable"] is True
        assert data["data"]["is_expired"] is False

    def test_manual_flight_past_departure_blocked(self):
        # Manual entry with past time
        res = client.post(
            "/api/flight/validate",
            json={
                "isManual": True,
                "flightNum": "AI2424",
                "departDate": "2026-10-01", # Past date
                "departTime": "08:00",
                "direction": "departure",
                "originCode": "BOM",
                "destCode": "DEL",
                "airportCode": "BOM",
            },
        )
        assert res.status_code == 400
        data = res.json()
        assert data["code"] == "FLIGHT_EXPIRED"
        assert "already departed" in data["error"]


class TestPastFlightBookingCreationValidation:
    """Tests for BookingService.create_booking past flight rejection."""

    def test_booking_creation_rejects_past_departure(self):
        db_mock = MagicMock()
        past_time = datetime.now(timezone.utc) - timedelta(hours=3)
        payload = BookingCreate(
            passenger_name="Rahul Sharma",
            passenger_email="rahul.sharma99@gmail.com",
            passenger_phone="+919876543210",
            service_category="Airport Assistance",
            service_type="elite",
            flight_num="AI2424",
            origin_code="BOM",
            dest_code="DEL",
            departure_time=past_time,
            total_amount=4950.0,
            metadata_json={
                "journey_type": "DEPARTURE",
                "service_airport": "BOM",
                "service_date": past_time.strftime("%Y-%m-%d"),
            },
        )

        with pytest.raises(HTTPException) as exc_info:
            BookingService.create_booking(db_mock, payload)

        assert exc_info.value.status_code == 400
        assert "already departed" in exc_info.value.detail

    def test_booking_creation_rejects_past_arrival(self):
        db_mock = MagicMock()
        past_time = datetime.now(timezone.utc) - timedelta(hours=2)
        payload = BookingCreate(
            passenger_name="Rahul Sharma",
            passenger_email="rahul.sharma99@gmail.com",
            passenger_phone="+919876543210",
            service_category="Airport Assistance",
            service_type="elite",
            flight_num="AI2424",
            origin_code="BOM",
            dest_code="DEL",
            arrival_time=past_time,
            total_amount=4950.0,
            metadata_json={
                "journey_type": "ARRIVAL",
                "service_airport": "DEL",
                "service_date": past_time.strftime("%Y-%m-%d"),
            },
        )

        with pytest.raises(HTTPException) as exc_info:
            BookingService.create_booking(db_mock, payload)

        assert exc_info.value.status_code == 400
        assert "already arrived" in exc_info.value.detail
