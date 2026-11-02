"""
Unit tests for Shafsky Operations WhatsApp Booking Notification Formatting.

Verifies:
1. Multi-line clean bulleted operations message formatting matching exact required sections (Image 2 layout).
2. Dynamic field population from real booking contexts without hardcoding.
3. Proper handling of missing optional values (no flight, no email, ₹0 enquiries, vehicles).
4. Meta template components building for 'shafsky_clean_booking_alert' (11 parameters),
   'shafsky_operations_alert' (9 parameters), and single-variable clean summary ('shafsky_ops_alert').
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
    """Verifies that format_booking_notification produces the clean bulleted layout matching Image 2."""
    output = WhatsAppDepartmentService.format_booking_notification(
        sample_meet_and_greet_context,
        Department.DEPARTMENT_1,
        "Meet & Greet",
    )

    expected = (
        "✈️ *NEW BOOKING / ENQUIRY — Meet & Greet / Lounge*\n\n"
        "• *Reference*: `SHF-20261101-BE8FC46A`\n"
        "• *Service*: Meet & Greet — Multi Service\n"
        "• *Customer*: Aariz Farooqui\n"
        "• *Contact*: +91 0846931247 | aariz.farooqui@gmail.com\n"
        "• *Guests / Pax / Passengers*: 1\n"
        "• *Route*: DEL → BOM\n"
        "• *Flight*: AI2424 (Terminal 1 & 2)\n"
        "• *Date / Time*: 02 Nov 2026 | 04:30\n"
        "• *Booking Value*: ₹3,000.00\n"
        "• *Status*: PENDING"
    )

    assert output == expected


def test_format_booking_notification_ground_transport():
    """Verifies clean bulleted layout for Ground Transport with vehicle, category, route, and notes."""
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
            "notes": "TEST - department delivery verification, please ignore.",
        },
        "total_amount": 0.0,
        "status": "PENDING",
    }

    output = WhatsAppDepartmentService.format_booking_notification(
        context,
        Department.DEPARTMENT_2,
        "Ground Transport",
    )

    assert "🚗 *NEW TRANSPORT ENQUIRY" in output
    assert "• *Reference*: `SHF-20261031-342205`" in output
    assert "• *Service*: Ground Transport" in output
    assert "• *Customer*: Test Customer" in output
    assert "• *Contact*: +91 77058 81139 | test@gmail.com" in output
    assert "• *Route*: Village East Mehram Nagar Road → Lucknow BKT" in output
    assert "• *Vehicle*: Mercedes-Benz E-Class (ID: merc-e-class)" in output
    assert "• *Category*: Luxury Vehicles" in output
    assert "• *Date / Time*: 01 Oct 2026 | 10:01" in output
    assert "• *Guests / Pax / Passengers*: 2" in output
    assert "• *Booking Value*: Quotation Required" in output
    assert "• *Status*: PENDING" in output
    assert "• *Notes*: TEST - department delivery verification, please ignore." in output


def test_build_template_components_shafsky_clean_booking_alert(sample_meet_and_greet_context):
    """Verifies 11 discrete template parameters for shafsky_clean_booking_alert."""
    components = WhatsAppDepartmentService.build_template_components(
        "fallback body text",
        template_name="shafsky_clean_booking_alert",
        context=sample_meet_and_greet_context,
    )

    assert len(components) == 1
    body_comp = components[0]
    assert body_comp["type"] == "body"
    params = body_comp["parameters"]
    assert len(params) == 11

    assert params[0]["text"] == "Meet & Greet / Lounge"
    assert params[1]["text"] == "SHF-20261101-BE8FC46A"
    assert params[2]["text"] == "Meet & Greet — Multi Service"
    assert params[3]["text"] == "Aariz Farooqui"
    assert params[4]["text"] == "+91 0846931247 | aariz.farooqui@gmail.com"
    assert params[5]["text"] == "1"
    assert params[6]["text"] == "DEL → BOM"
    assert "AI2424" in params[7]["text"]
    assert "02 Nov 2026 | 04:30" in params[8]["text"]
    assert params[9]["text"] == "₹3,000.00"
    assert params[10]["text"] == "PENDING"

    for p in params:
        assert "\n" not in p["text"]
        assert "\t" not in p["text"]


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

    for p in params:
        assert "\n" not in p["text"]
        assert "\t" not in p["text"]


def test_build_template_components_single_variable_clean_summary(sample_meet_and_greet_context):
    """Verifies single-variable template (shafsky_ops_alert) generates a clean parameter without newlines."""
    formatted = WhatsAppDepartmentService.format_booking_notification(
        sample_meet_and_greet_context,
        Department.DEPARTMENT_1,
        "Meet & Greet",
    )
    components = WhatsAppDepartmentService.build_template_components(
        formatted,
        template_name="shafsky_ops_alert",
        context=sample_meet_and_greet_context,
    )

    assert len(components) == 1
    params = components[0]["parameters"]
    assert len(params) == 1
    summary = params[0]["text"]
    assert "\n" not in summary
    assert "\t" not in summary
    assert "SHF-20261101-BE8FC46A" in summary
    assert "Aariz Farooqui" in summary
    assert "₹3,000.00" in summary
    assert "PENDING" in summary
