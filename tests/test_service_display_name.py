from app.utils.service_display import (
    is_multi_service_placeholder,
    resolve_service_display_name,
)
from app.services.whatsapp_department_service import WhatsAppDepartmentService


def test_multi_service_placeholder_resolves_to_selected_package():
    meta_plat = {"available_services": [{"service_type": "DEPARTURE", "package": "platinum"}]}
    assert resolve_service_display_name("multi_service", meta_plat) == "Platinum"

    meta_gold = {"available_services": [{"service_type": "DEPARTURE", "package": "gold"}]}
    assert resolve_service_display_name("multi_service", meta_gold) == "Gold"

    meta_elite = {"available_services": [{"service_type": "DEPARTURE", "package": "elite"}]}
    assert resolve_service_display_name("multi_service", meta_elite) == "Elite"

    meta_ep = {"available_services": [{"service_type": "ARRIVAL", "package": "elite_plus"}]}
    assert resolve_service_display_name("multi_service", meta_ep) == "Elite Plus"


def test_multi_service_with_different_packages_labels_each_journey():
    meta = {
        "available_services": [
            {"service_type": "DEPARTURE", "package": "gold"},
            {"service_type": "ARRIVAL", "package": "elite"},
        ]
    }
    assert resolve_service_display_name("multi_service", meta) == "Departure Gold, Arrival Elite"


def test_multi_service_direct_package_resolution():
    assert resolve_service_display_name("multi_service", {"package": "platinum"}) == "Platinum"
    assert resolve_service_display_name("multi_service", {"package": "elite_plus"}) == "Elite Plus"
    assert resolve_service_display_name("Meet & Greet — multi_service", {"package": "platinum"}) == "Meet & Greet — Platinum"


def test_regular_and_unknown_values_are_untouched():
    assert resolve_service_display_name("Gold", {}) == "Gold"
    # When placeholder has no package info, falls back to stripped or original
    assert resolve_service_display_name("Meet & Greet — multi_service", {}) == "Meet & Greet"


def test_shafsky_operations_alert_template_shows_actual_package():
    # Exact scenario from user's booking alert
    context = {
        "booking_ref": "SHF-20261110-46644656",
        "passenger_name": "ERAM FATIMA",
        "passenger_phone": "+917869427864",
        "passenger_email": "ZAMAHOLIDAYS@gmail.com",
        "service_category": "Meet & Greet",
        "service_type": "multi_service",
        "flight_num": "XY320",
        "origin_code": "LKO",
        "dest_code": "DMM",
        "terminal": "1 & 2",
        "departure_time": "2026-11-13T17:15:00",
        "passenger_count": 2,
        "total_amount": 3300.0,
        "currency": "INR",
        "status": "CONFIRMED",
        "metadata_json": {
            "available_services": [
                {
                    "service_type": "DEPARTURE",
                    "airport_code": "LKO",
                    "package": "platinum",
                    "status": "AVAILABLE",
                }
            ]
        },
    }
    comps = WhatsAppDepartmentService.build_template_components(
        message_body="",
        template_name="shafsky_operations_alert",
        context=context,
    )
    assert len(comps) == 1
    params = comps[0]["parameters"]
    assert params[0]["text"] == "SHF-20261110-46644656"
    assert params[1]["text"] == "Meet & Greet — Platinum"
    assert "multi_service" not in params[1]["text"]
    assert params[2]["text"] == "ERAM FATIMA"
    assert params[8]["text"] == "CONFIRMED"


def test_clean_booking_alert_and_summary_never_show_multi_service():
    context = {
        "booking_ref": "SHF-X-1",
        "passenger_name": "Test Guest",
        "service_category": "Airport Assistance",
        "service_type": "multi_service",
        "total_amount": 2500.0,
        "currency": "INR",
        "status": "CONFIRMED",
        "metadata_json": {
            "available_services": [
                {"service_type": "DEPARTURE", "package": "gold"}
            ]
        },
    }
    clean_comps = WhatsAppDepartmentService.build_template_components(
        message_body="",
        template_name="shafsky_clean_booking_alert",
        context=context,
    )
    svc_clean = clean_comps[0]["parameters"][2]["text"]
    assert "multi_service" not in svc_clean
    assert "Gold" in svc_clean

    summary = WhatsAppDepartmentService._build_single_variable_summary(context)
    assert "multi_service" not in summary
    assert "Gold" in summary


def test_multi_service_with_service_breakdown_metadata():
    """Verifies that service_breakdown list from journey bookings correctly resolves package."""
    meta = {
        "service_breakdown": [
            {
                "service_type": "DEPARTURE",
                "package_slug": "platinum",
                "package_name": "Platinum Service",
            }
        ]
    }
    assert resolve_service_display_name("multi_service", meta) == "Platinum"
    assert resolve_service_display_name("Meet & Greet — multi_service", meta) == "Meet & Greet — Platinum"


def test_multi_service_never_leaks_even_with_dirty_context_strings():
    """Verifies that even if service_name or service_type contains raw multi_service, it is cleaned out."""
    context = {
        "booking_ref": "SHF-DIRTY-1",
        "service_category": "Meet & Greet",
        "service_type": "multi_service",
        "service_name": "Meet & Greet — multi_service",
    }
    comps = WhatsAppDepartmentService.build_template_components(
        message_body="",
        template_name="shafsky_operations_alert",
        context=context,
    )
    param_text = comps[0]["parameters"][1]["text"]
    assert "multi_service" not in param_text
    assert "multi-service" not in param_text
    assert param_text == "Meet & Greet"

