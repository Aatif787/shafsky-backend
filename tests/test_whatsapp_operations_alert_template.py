"""
Unit tests for the WhatsApp Operations Alert Template Generator.
Verifies dynamic extraction, ordering, fallbacks, and HTML rendering across multiple records.
"""

import pytest
from app.services.whatsapp_notification_template_service import (
    extract_operations_alert_fields,
    render_whatsapp_operations_alert_html,
)
from app.services.notification_templates import NotificationTemplateEngine


def test_user_ground_transport_sample_rendering():
    """Verifies the exact realistic sample data provided by the user."""
    sample = {
        "booking_ref": "SHF-20261031-342205",
        "service_name": "Ground Transport",
        "passenger_name": "Test Customer",
        "passenger_phone": "+91 77058 81139",
        "passenger_email": "test@gmail.com",
        "passenger_count": 2,
        "details": {
            "vehicle_name": "Mercedes-Benz E-Class",
            "vehicle_id": "merc-e-class",
            "vehicle_category": "Luxury Vehicles",
            "origin": "Village East Mehram Nagar Road",
            "destination": "Lucknow BKT",
            "notes": "TEST - department delivery verification, please ignore. Please action this booking from the operations dashboard.",
        },
        "departure_time": "2026-10-01 10:01",
        "status": "PENDING",
    }

    fields = extract_operations_alert_fields(sample)
    assert fields["reference_no"] == "SHF-20261031-342205"
    assert fields["service"] == "Ground Transport"
    assert fields["customer"] == "Test Customer"
    assert fields["contact"] == "+91 77058 81139"
    assert fields["email"] == "test@gmail.com"
    assert fields["passengers"] == "2"
    assert "Mercedes-Benz E-Class" in fields["vehicle"]
    assert "merc-e-class" in fields["vehicle"]
    assert fields["category"] == "Luxury Vehicles"
    assert "Village East Mehram Nagar Road → Lucknow BKT" in fields["route"]
    assert "01 Oct 2026 at 10:01" in fields["date_time"]
    assert "TEST - department delivery verification" in fields["notes"]
    assert fields["status"] == "PENDING"

    html = render_whatsapp_operations_alert_html(sample)

    # Required layout elements
    assert "New Operations Alert" in html
    assert "Booking / Enquiry Received" in html
    assert "PENDING" in html
    assert "SHF-20261031-342205" in html
    assert "Ground Transport" in html
    assert "Test Customer" in html
    assert "+91 77058 81139" in html
    assert "test@gmail.com" in html
    assert "Mercedes-Benz E-Class" in html
    assert "Luxury Vehicles" in html
    assert "Village East Mehram Nagar Road → Lucknow BKT" in html
    assert "01 Oct 2026 at 10:01" in html
    assert "Special Notes &amp; Instructions" in html or "Special Notes & Instructions" in html
    assert "View in Dashboard" in html

    # Verify alternating row colors
    assert "background-color: #ffffff;" in html
    assert "background-color: #f8fafc;" in html


def test_meet_and_greet_record_dynamic_mapping():
    """Verifies dynamic mapping for a different service (Meet & Greet) without vehicle."""
    booking = {
        "booking_ref": "SHF-20261101-891024",
        "service_category": "Airport Assistance",
        "service_type": "VIP Meet & Greet",
        "passenger_name": "Rajesh Malhotra",
        "passenger_phone": "+91 98110 54321",
        "passenger_email": "rajesh.m@corp.in",
        "passenger_count": 4,
        "airport_code": "DEL",
        "dest_code": "DEL",
        "departure_time": "2026-11-15T14:30:00Z",
        "notes": "VIP delegation guest arriving at Terminal 3 aerobridge.",
        "status": "PENDING",
    }

    fields = extract_operations_alert_fields(booking)
    assert fields["reference_no"] == "SHF-20261101-891024"
    assert fields["customer"] == "Rajesh Malhotra"
    assert fields["passengers"] == "4"
    assert fields["vehicle"] is None
    assert fields["route"] == "DEL"
    assert "15 Nov 2026 at 14:30" in fields["date_time"]

    html = render_whatsapp_operations_alert_html(booking)
    assert "SHF-20261101-891024" in html
    assert "Rajesh Malhotra" in html
    # Vehicle row should be cleanly omitted when not applicable
    assert ">Vehicle<" not in html


def test_private_jet_charter_record_dynamic_mapping():
    """Verifies dynamic mapping for an international charter booking."""
    charter = {
        "booking_ref": "SHF-20261102-120934",
        "service_type": "Private Jet Charter",
        "passenger_name": "Dr. Sarah Jenkins",
        "passenger_phone": "+44 7911 123456",
        "passenger_email": "sarah.j@global.org",
        "passenger_count": 6,
        "origin_code": "DEL",
        "dest_code": "DXB",
        "details": {
            "vehicle_name": "Bombardier Challenger 605",
            "vehicle_category": "Heavy Jets",
            "notes": "VIP catering and customs airside escort requested.",
        },
        "departure_time": "2026-11-20 09:00:00",
        "status": "CONFIRMED",
    }

    fields = extract_operations_alert_fields(charter)
    assert fields["reference_no"] == "SHF-20261102-120934"
    assert fields["service"] == "Private Jet Charter"
    assert fields["customer"] == "Dr. Sarah Jenkins"
    assert fields["passengers"] == "6"
    assert fields["vehicle"] == "Bombardier Challenger 605"
    assert fields["category"] == "Heavy Jets"
    assert "DEL → DXB" in fields["route"]
    assert "20 Nov 2026 at 09:00" in fields["date_time"]
    assert fields["status"] == "CONFIRMED"

    html = render_whatsapp_operations_alert_html(charter)
    assert "DEL → DXB" in html
    assert "Bombardier Challenger 605" in html
    assert "CONFIRMED" in html


def test_empty_and_missing_field_fallbacks():
    """Verifies clean fallbacks when fields are missing or not provided."""
    sparse_data = {
        "booking_ref": "SHF-EMPTY-001",
    }

    fields = extract_operations_alert_fields(sparse_data)
    assert fields["reference_no"] == "SHF-EMPTY-001"
    assert fields["service"] == "Not provided"
    assert fields["customer"] == "Not provided"
    assert fields["contact"] == "Not provided"
    assert fields["email"] == "Not provided"
    assert fields["passengers"] == "Not provided"
    assert fields["route"] == "Not provided"
    assert fields["date_time"] == "Not provided"

    html = render_whatsapp_operations_alert_html(sparse_data)
    assert "SHF-EMPTY-001" in html
    assert "Not provided" in html
    # Never render undefined or None
    assert "None" not in html
    assert "undefined" not in html


def test_notification_template_engine_integration():
    """Verifies integration with NotificationTemplateEngine."""
    payload = {
        "bookingRef": "SHF-INTEG-999",
        "serviceName": "Ground Transport",
        "passengerName": "Integration Test",
        "passengerPhone": "+91 99999 88888",
    }
    rendered = NotificationTemplateEngine.render_template("WHATSAPP_OPERATIONS_ALERT", payload)
    assert "[Shafsky Ops]" in rendered["subject"]
    assert "SHF-INTEG-999" in rendered["html"]
    assert "New Operations Alert" in rendered["html"]
    assert "Integration Test" in rendered["whatsapp_text"]
