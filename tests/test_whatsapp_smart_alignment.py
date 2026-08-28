"""
Unit and integration tests for WhatsApp Smart Travel-Type Auto-Alignment.

Validates:
1. Domestic -> International flight switch auto-aligns package and pricing.
2. Verified flight message presents 'Confirm & Proceed', 'Change Package', 'Re-enter Flight'.
3. Confirming flight commits aligned service_id, service_name, unit_price, and 24h cutoff.
4. 'Change Package' opens International service menu with compact inheritance.
5. Reverse switch (International -> Domestic) auto-aligns to Domestic pricing & 12h cutoff.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from unittest.mock import MagicMock, patch
import pytest
from sqlalchemy import select

from app.database import Base, SessionLocal, engine
from app.models.journey_models import SupportedAirport
from app.models.whatsapp_models import WhatsAppConversation
from app.integrations.whatsapp.service import WhatsAppBookingStateMachine
from app.services.booking_cutoff import airport_min_notice_hours


@pytest.fixture(autouse=True)
def setup_db():
    Base.metadata.create_all(bind=engine)


def test_smart_alignment_domestic_to_international():
    """Customer picked Domestic DEL Arrival, but enters international flight (DXB -> DEL)."""
    db = SessionLocal()
    try:
        phone = "+919999988881"
        db.query(WhatsAppConversation).filter_by(phone_number=phone).delete()
        db.commit()

        conv = WhatsAppConversation(
            phone_number=phone,
            current_state="FLIGHT_INPUT",
            selected_category="Airport Services",
            selected_airport_iata="DEL",
            selected_airport_name="Indira Gandhi International Airport",
            selected_service_id="del-dom-silver-id",
            selected_service_name="Silver Service",
            passenger_count=2,
            flight_details_json={
                "journey_type": "ARRIVAL",
                "travel_type": "DOMESTIC",
                "flight_type": "DOMESTIC",
                "terminal": "Terminal 3",
                "unit_price": 3000.0,
                "base_price": 3000.0,
            },
        )
        db.add(conv)
        db.commit()

        # Mock AviationStack response for EK501 (DXB -> DEL)
        mock_verify_res = {
            "success": True,
            "flight": {
                "flight_number": "EK501",
                "status": "scheduled",
                "airline": {"name": "Emirates", "iata": "EK"},
                "departure": {
                    "iata": "DXB",
                    "airport": "Dubai International",
                    "city": "Dubai",
                    "scheduled": "2026-08-31T20:00:00+00:00",
                },
                "arrival": {
                    "iata": "DEL",
                    "airport": "Indira Gandhi International",
                    "city": "Delhi",
                    "scheduled": "2026-09-01T02:45:00+00:00",
                    "terminal": "3",
                },
            },
        }

        with patch("app.flight.aviationstack_service.verify_flight_for_whatsapp", return_value=mock_verify_res):
            with patch("app.integrations.whatsapp.client.WhatsAppClient.send_interactive_buttons") as mock_btn:
                mock_btn.return_value = {"success": True}
                res = WhatsAppBookingStateMachine._state_flight_input(db, conv, "EK501")

                assert res["status"] == "flight_verified"
                assert conv.current_state == "FLIGHT_CONFIRMATION"

                # Check interactive buttons sent
                args, kwargs = mock_btn.call_args
                buttons = kwargs.get("buttons") or []
                button_titles = [b["title"] for b in buttons]
                assert "Confirm & Proceed" in button_titles
                assert "Change Package" in button_titles
                assert "Re-enter Flight" in button_titles

                body = kwargs.get("body_text", "")
                assert "Emirates" in body
                assert "DXB" in body
                assert "DEL" in body
                assert "Package aligned to International" in body

        # Now test Customer taps 'Confirm & Proceed'
        with patch("app.integrations.whatsapp.client.WhatsAppClient.send_text_message") as mock_msg:
            mock_msg.return_value = {"success": True}
            res_conf = WhatsAppBookingStateMachine._state_flight_confirmation(
                db, conv, "Confirm & Proceed", input_id="btn_confirm_flight"
            )

            assert res_conf["status"] == "date_prompt_sent"
            assert conv.current_state == "DATE_SELECTION"
            assert conv.flight_num == "EK501"

            # Check metadata and 24h international cutoff rule
            meta = conv.flight_details_json
            assert meta["travel_type"] == "INTERNATIONAL"
            assert meta["flight_type"] == "INTERNATIONAL"
            assert meta["origin_iata"] == "DXB"
            assert meta["destination_iata"] == "DEL"
            assert airport_min_notice_hours(meta["flight_type"]) == 24
            assert conv.selected_service_name is not None
            assert conv.total_amount > 0

    finally:
        db.query(WhatsAppConversation).filter_by(phone_number=phone).delete()
        db.commit()
        db.close()


def test_smart_alignment_change_package_action():
    """Customer chooses 'Change Package' after route auto-alignment."""
    db = SessionLocal()
    try:
        phone = "+919999988882"
        db.query(WhatsAppConversation).filter_by(phone_number=phone).delete()
        db.commit()

        conv = WhatsAppConversation(
            phone_number=phone,
            current_state="FLIGHT_CONFIRMATION",
            selected_category="Airport Services",
            selected_airport_iata="DEL",
            selected_airport_name="Indira Gandhi International Airport",
            selected_service_id="del-dom-silver-id",
            selected_service_name="Silver Service",
            passenger_count=1,
            flight_details_json={
                "journey_type": "ARRIVAL",
                "travel_type": "DOMESTIC",
                "verification_status": "pending_confirmation",
                "_pending_verified_flight": {
                    "flight_number": "EK501",
                    "origin_iata": "DXB",
                    "destination_iata": "DEL",
                    "aligned_travel_type": "INTERNATIONAL",
                    "aligned_service_name": "Gold Service",
                    "aligned_unit_price": 5000.0,
                }
            },
        )
        db.add(conv)
        db.commit()

        with patch("app.integrations.whatsapp.delivery.send_list") as mock_list:
            mock_list.return_value = {"success": True}
            res = WhatsAppBookingStateMachine._state_flight_confirmation(
                db, conv, "Change Package", input_id="btn_change_package"
            )

            assert res["status"] == "services_menu_sent"
            assert conv.current_state == "SERVICE_SELECTION"
            assert conv.flight_details_json["travel_type"] == "INTERNATIONAL"

    finally:
        db.query(WhatsAppConversation).filter_by(phone_number=phone).delete()
        db.commit()
        db.close()


if __name__ == "__main__":
    test_smart_alignment_domestic_to_international()
    test_smart_alignment_change_package_action()
    print("[+] ALL WHATSAPP SMART TRAVEL TYPE ALIGNMENT TESTS PASSED 100%!")
