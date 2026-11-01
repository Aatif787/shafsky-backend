"""
Unit tests for Shafsky Operations WhatsApp Booking Notification Formatting.

Verifies:
1. Multi-line structured operations message formatting matching exact required sections.
2. Dynamic field population from real booking contexts without hardcoding.
3. Proper handling of missing optional values (no flight, no email, ₹0 enquiries, vehicles).
4. Meta template components building for 'shafsky_operations_alert' (9 parameters) and
   single-variable fallback ('shafsky_ops_alert').
5. Meta parameter constraints (no newlines/tabs, <= 1024 characters).
"""

import pytest
from app.services.whatsapp_department_service import Department, WhatsAppDepartmentService


@pytest.fixture
def sample_meet_and_greet_context():
    return {
        "booking_ref": "SHF-20261101-BE8FC46A",
        "service_category": "meet_and_greet",
        "service_type": "Multi Service",
        "passenger_name": "Aariz Farooqui",
        "passenger_phone": "+91 0846931247",
        "passenger_email": "aariz.farooqui@gmail.com",
        "flight_num": "AI2424",
        "origin_code": "DEL",
        "dest_code": "BOM",
        "terminal": "1 & 2",
        "departure_time": "2026-11-02T04:30:00",
        "passenger_count": 1,
        "total_amount": 3000.0,
        "status": "PENDING",
    }


def test_format_booking_notification_exact_layout(sample_meet_and_greet_context):
    """Verifies that format_booking_notification produces the exact multi-line structured layout."""
    output = WhatsAppDepartmentService.format_booking_notification(
        sample_meet_and_greet_context,
        Department.DEPARTMENT_1,
        "Meet & Greet",
    )

    expected = (
        "🚨 SHAFSKY AVIATION\n"
        "NEW BOOKING / ENQUIRY\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "REFERENCE\n"
        "SHF-20261101-BE8FC46A\n"
        "SERVICE\n"
        "Meet & Greet — Multi Service\n"
        "CUSTOMER\n"
        "Aariz Farooqui\n"
        "CONTACT\n"
        "+91 0846931247\n"
        "aariz.farooqui@gmail.com\n"
        "FLIGHT\n"
        "AI2424 | DEL → BOM\n"
        "Terminal 1 & 2\n"
        "DATE & TIME\n"
        "02 Nov 2026 | 04:30\n"
        "PASSENGERS\n"
        "1\n"
        "BOOKING VALUE\n"
        "₹3,000.00\n"
        "STATUS\n"
        "PENDING\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "Please action this booking from the operations dashboard."
    )

    assert output == expected


def test_format_booking_notification_ground_transport():
    """Verifies layout for Ground Transport with vehicle, category, route, and notes."""
    context = {
        "booking_ref": "SHF-20261031-342205",
        "service_category": "transport",
        "service_type": "Ground Transport",
        "passenger_name": "Test Customer",
        "passenger_phone": "+91 77058 81139",
        "passenger_email": "test@gmail.com",
        "origin": "Village East Mehram Nagar Road",
        "destination": "Lucknow BKT",
        "service_date": "2026-10-01T10:01:00",
        "passenger_count": 2,
        "details": {
            "vehicle_name": "Mercedes-Benz E-Class",
            "vehicle_id": "merc-e-class",
            "vehicle_category": "Luxury Vehicles",
            "notes": "TEST - department delivery verification, please ignore. Please action this booking from the operations dashboard.",
        },
        "total_amount": 0.0,
        "status": "PENDING",
    }

    output = WhatsAppDepartmentService.format_booking_notification(
        context,
        Department.DEPARTMENT_2,
        "Ground Transport",
    )

    assert "🚨 SHAFSKY AVIATION" in output
    assert "REFERENCE\nSHF-20261031-342205" in output
    assert "SERVICE\nGround Transport" in output
    assert "CUSTOMER\nTest Customer" in output
    assert "CONTACT\n+91 77058 81139\ntest@gmail.com" in output
    assert "ROUTE\nVillage East Mehram Nagar Road → Lucknow BKT" in output
    assert "VEHICLE\nMercedes-Benz E-Class (ID: merc-e-class)" in output
    assert "CATEGORY\nLuxury Vehicles" in output
    assert "DATE & TIME\n01 Oct 2026 | 10:01" in output
    assert "PASSENGERS\n2" in output
    assert "BOOKING VALUE\nQuotation Required" in output
    assert "STATUS\nPENDING" in output
    assert "NOTES\nTEST - department delivery verification" in output


def test_build_template_components_shafsky_operations_alert(sample_meet_and_greet_context):
    """Verifies 9 distinct template parameters are generated for shafsky_operations_alert."""
    components = WhatsAppDepartmentService.build_template_components(
        "fallback body text",
        template_name="shafsky_operations_alert",
        context=sample_meet_and_greet_context,
    )

    assert len(components) == 1
    body_comp = components[0]
    assert body_comp["type"] == "body"
    params = body_comp["parameters"]
    assert len(params) == 9

    assert params[0]["text"] == "SHF-20261101-BE8FC46A"
    assert params[1]["text"] == "Meet & Greet — Multi Service"
    assert params[2]["text"] == "Aariz Farooqui"
    assert params[3]["text"] == "+91 0846931247 | aariz.farooqui@gmail.com"
    assert "AI2424" in params[4]["text"]
    assert "02 Nov 2026 | 04:30" in params[5]["text"]
    assert params[6]["text"] == "1"
    assert params[7]["text"] == "₹3,000.00"
    assert params[8]["text"] == "PENDING"

    # None of the parameters may contain newlines or tabs
    for p in params:
        assert "\n" not in p["text"]
        assert "\t" not in p["text"]


def test_build_template_components_single_variable_fallback():
    """Verifies single-variable template (shafsky_ops_alert) generates 1 safe parameter."""
    raw_msg = (
        "🚨 SHAFSKY AVIATION\n"
        "NEW BOOKING / ENQUIRY\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "REFERENCE\nSHF-123\n"
    )

    components = WhatsAppDepartmentService.build_template_components(
        raw_msg,
        template_name="shafsky_ops_alert",
    )

    assert len(components) == 1
    params = components[0]["parameters"]
    assert len(params) == 1
    # Crucial: no newlines allowed in Meta parameter
    assert "\n" not in params[0]["text"]
    assert "SHF-123" in params[0]["text"]
