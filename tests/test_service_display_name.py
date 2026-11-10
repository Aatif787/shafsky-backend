from app.utils.service_display import resolve_service_display_name
from app.services.whatsapp_department_service import WhatsAppDepartmentService


def test_multi_service_placeholder_resolves_to_selected_package():
    meta = {"available_services": [{"service_type": "DEPARTURE", "package": "platinum"}]}
    assert resolve_service_display_name("multi_service", meta) == "Platinum"
    meta = {"available_services": [{"service_type": "ARRIVAL", "package": "elite-plus"}]}
    assert resolve_service_display_name("multi_service", meta) == "Elite Plus"


def test_multi_service_with_different_packages_labels_each_journey():
    meta = {
        "available_services": [
            {"service_type": "DEPARTURE", "package": "gold"},
            {"service_type": "ARRIVAL", "package": "elite"},
        ]
    }
    assert resolve_service_display_name("multi_service", meta) == "Departure Gold, Arrival Elite"


def test_regular_and_unknown_values_are_untouched():
    assert resolve_service_display_name("gold", {}) == "gold"
    assert resolve_service_display_name("multi_service", {}) == "multi_service"


def test_department_alert_never_shows_multi_service():
    context = {
        "booking_ref": "SHF-X-1",
        "passenger_name": "Test",
        "service_category": "Airport Assistance",
        "service_type": "multi_service",
        "service_name": resolve_service_display_name(
            "multi_service", {"available_services": [{"service_type": "DEPARTURE", "package": "gold"}]}
        ),
        "status": "CONFIRMED",
    }
    body = WhatsAppDepartmentService.format_booking_notification(context, *WhatsAppDepartmentService.resolve_department(
        context["service_category"], context["service_type"], context
    ))
    assert "multi_service" not in body
    assert "Gold" in body

