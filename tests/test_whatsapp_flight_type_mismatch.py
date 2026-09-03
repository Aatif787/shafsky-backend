"""
Unit and integration tests for WhatsApp Flight Type Mismatch consistency validation.

Covers all 12 core scenarios:
1. Domestic selected + Domestic flight -> normal flow advances to FLIGHT_CONFIRMATION
2. International selected + International flight -> normal flow advances to FLIGHT_CONFIRMATION
3. Domestic selected + International flight -> STOPS immediately, FLIGHT_TYPE_MISMATCH
4. International selected + Domestic flight -> STOPS immediately, FLIGHT_TYPE_MISMATCH with 'Switch to Domestic'
5. Domestic selected + unverified/not-found flight -> existing unverified fallback remains intact
6. Option 1 ('Switch to International') -> switches travel_type, preserves flight, clears old service, shows new menu, on package select advances directly to DATE_SELECTION without re-asking flight
7. Option 2 ('Re-enter Flight') -> keeps travel_type & airport, clears mismatch flight, returns to FLIGHT_INPUT
8. Option 3 ('Change Airport') -> clears airport/flight context, returns to AIRPORT_SELECTION
9. Option 1 Reverse ('Switch to Domestic') -> switches travel_type to DOMESTIC, preserves flight, shows domestic menu
10. Mismatch state cannot be bypassed by arbitrary user text -> re-sends mismatch choices
11. BACK action while in FLIGHT_TYPE_MISMATCH -> returns to SERVICE_SELECTION and clears mismatch flight
12. Defensive safeguard at booking creation -> rejects inconsistent travel type vs verified route
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from unittest.mock import MagicMock, patch
import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.database import Base, SessionLocal, engine
from app.models.journey_models import SupportedAirport
from app.models.whatsapp_models import WhatsAppConversation
from app.integrations.whatsapp.service import WhatsAppBookingStateMachine
from app.services.booking_service import BookingService
from app.schemas.booking import BookingCreate


@pytest.fixture(autouse=True)
def setup_db():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        from app.models.schema import AirportManagement
        dxb = db.scalar(select(AirportManagement).where(AirportManagement.code == "DXB"))
        if not dxb:
            dxb = AirportManagement(
                code="DXB",
                name="Dubai International Airport",
                city="Dubai",
                country="AE",
                is_active=True,
            )
            db.add(dxb)
            db.commit()
    finally:
        db.close()


def _mock_ek501_flight():
    """Mock AviationStack international flight (DXB -> DEL)."""
    return {
        "success": True,
        "flight": {
            "flight_number": "EK501",
            "airline": {"name": "Emirates", "iata": "EK"},
            "departure": {
                "iata": "DXB",
                "airport": "Dubai International",
                "city": "Dubai",
                "scheduled": "2026-09-10T20:00:00+00:00",
                "terminal": "3",
            },
            "arrival": {
                "iata": "DEL",
                "airport": "Indira Gandhi International",
                "city": "Delhi",
                "scheduled": "2026-09-11T02:45:00+00:00",
                "terminal": "3",
            },
            "status": "scheduled",
            "provider": "aviationstack",
        },
    }


def _mock_6e224_flight():
    """Mock AviationStack domestic flight (DEL -> BOM)."""
    return {
        "success": True,
        "flight": {
            "flight_number": "6E224",
            "airline": {"name": "IndiGo", "iata": "6E"},
            "departure": {
                "iata": "DEL",
                "airport": "Indira Gandhi International",
                "city": "Delhi",
                "scheduled": "2026-09-10T06:00:00+00:00",
                "terminal": "2",
            },
            "arrival": {
                "iata": "BOM",
                "airport": "Chhatrapati Shivaji Maharaj International",
                "city": "Mumbai",
                "scheduled": "2026-09-10T08:15:00+00:00",
                "terminal": "1",
            },
            "status": "scheduled",
            "provider": "aviationstack",
        },
    }


def test_scenario_1_domestic_selected_and_domestic_flight_proceeds():
    """Scenario 1: Domestic selected + Domestic flight -> Normal verification continues to FLIGHT_CONFIRMATION."""
    db = SessionLocal()
    try:
        phone = "919000000001"
        db.query(WhatsAppConversation).filter_by(phone_number=phone).delete()
        db.commit()

        conv = WhatsAppConversation(
            phone_number=phone,
            current_state="FLIGHT_INPUT",
            selected_category="Airport Services",
            selected_airport_iata="DEL",
            selected_airport_name="Indira Gandhi International Airport",
            selected_service_id="del-dom-silver",
            selected_service_name="Silver Service",
            passenger_count=1,
            flight_details_json={
                "journey_type": "DEPARTURE",
                "travel_type": "DOMESTIC",
                "flight_type": "DOMESTIC",
                "terminal": "Terminal 2",
                "unit_price": 2500.0,
            },
        )
        db.add(conv)
        db.commit()

        with patch("app.flight.aviationstack_service.verify_flight_for_whatsapp", return_value=_mock_6e224_flight()):
            with patch("app.integrations.whatsapp.client.WhatsAppClient.send_interactive_buttons") as mock_btn:
                mock_btn.return_value = {"success": True}
                res = WhatsAppBookingStateMachine._state_flight_input(db, conv, "6E224")

                assert res["status"] == "flight_verified"
                assert conv.current_state == "FLIGHT_CONFIRMATION"
                assert isinstance(conv.flight_details_json, dict)
                assert conv.flight_details_json["travel_type"] == "DOMESTIC"
                assert "_pending_verified_flight" in conv.flight_details_json

                # Verify buttons sent
                args, kwargs = mock_btn.call_args
                buttons = kwargs.get("buttons") or []
                titles = [b["title"] for b in buttons]
                assert "Confirm Flight" in titles
                assert "Re-enter Flight" in titles
                assert "Change Airport" in titles
    finally:
        db.close()


def test_scenario_2_international_selected_and_international_flight_proceeds():
    """Scenario 2: International selected + International flight -> Normal verification continues to FLIGHT_CONFIRMATION."""
    db = SessionLocal()
    try:
        phone = "919000000002"
        db.query(WhatsAppConversation).filter_by(phone_number=phone).delete()
        db.commit()

        conv = WhatsAppConversation(
            phone_number=phone,
            current_state="FLIGHT_INPUT",
            selected_category="Airport Services",
            selected_airport_iata="DEL",
            selected_airport_name="Indira Gandhi International Airport",
            selected_service_id="del-intl-silver",
            selected_service_name="Silver Service",
            passenger_count=1,
            flight_details_json={
                "journey_type": "ARRIVAL",
                "travel_type": "INTERNATIONAL",
                "flight_type": "INTERNATIONAL",
                "terminal": "Terminal 3",
                "unit_price": 3500.0,
            },
        )
        db.add(conv)
        db.commit()

        with patch("app.flight.aviationstack_service.verify_flight_for_whatsapp", return_value=_mock_ek501_flight()):
            with patch("app.integrations.whatsapp.client.WhatsAppClient.send_interactive_buttons") as mock_btn:
                mock_btn.return_value = {"success": True}
                res = WhatsAppBookingStateMachine._state_flight_input(db, conv, "EK501")

                assert res["status"] == "flight_verified"
                assert conv.current_state == "FLIGHT_CONFIRMATION"
                assert isinstance(conv.flight_details_json, dict)
                assert conv.flight_details_json["travel_type"] == "INTERNATIONAL"
    finally:
        db.close()


def test_scenario_3_domestic_selected_international_flight_blocked_immediately():
    """Scenario 3: Domestic selected + International flight -> STOP FLOW IMMEDIATELY, state is FLIGHT_TYPE_MISMATCH."""
    db = SessionLocal()
    try:
        phone = "919000000003"
        db.query(WhatsAppConversation).filter_by(phone_number=phone).delete()
        db.commit()

        conv = WhatsAppConversation(
            phone_number=phone,
            current_state="FLIGHT_INPUT",
            selected_category="Airport Services",
            selected_airport_iata="DEL",
            selected_airport_name="Indira Gandhi International Airport",
            selected_service_id="del-dom-silver",
            selected_service_name="Silver Service",
            passenger_count=1,
            flight_details_json={
                "journey_type": "ARRIVAL",
                "travel_type": "DOMESTIC",
                "flight_type": "DOMESTIC",
                "terminal": "Terminal 3",
                "unit_price": 2500.0,
            },
        )
        db.add(conv)
        db.commit()

        with patch("app.flight.aviationstack_service.verify_flight_for_whatsapp", return_value=_mock_ek501_flight()):
            with patch("app.integrations.whatsapp.client.WhatsAppClient.send_interactive_buttons") as mock_btn:
                mock_btn.return_value = {"success": True}
                res = WhatsAppBookingStateMachine._state_flight_input(db, conv, "EK501")

                # Must NOT advance to FLIGHT_CONFIRMATION
                assert res["status"] == "flight_type_mismatch"
                assert res["success"] is False
                assert conv.current_state == "FLIGHT_TYPE_MISMATCH"
                assert conv.flight_num is None
                assert isinstance(conv.flight_details_json, dict)
                assert "_pending_mismatch_flight" in conv.flight_details_json
                assert conv.flight_details_json["_pending_mismatch_flight"]["actual_flight_type"] == "INTERNATIONAL"
                assert conv.flight_details_json["_pending_mismatch_flight"]["selected_flight_type"] == "DOMESTIC"

                # Check interactive buttons sent
                args, kwargs = mock_btn.call_args
                buttons = kwargs.get("buttons") or []
                button_ids = [b["id"] for b in buttons]
                button_titles = [b["title"] for b in buttons]
                assert "btn_switch_travel_type" in button_ids
                assert "btn_reenter_flight" in button_ids
                assert "btn_change_airport" in button_ids
                assert "Switch to Intl" in button_titles

                body = kwargs.get("body_text", "")
                assert "Flight Type Mismatch" in body
                assert "You selected Domestic" in body
                assert "International" in body
                assert "EK501" in body
    finally:
        db.close()


def test_scenario_4_international_selected_domestic_flight_blocked_immediately():
    """Scenario 4: International selected + Domestic flight -> STOP FLOW IMMEDIATELY, shows 'Switch to Domestic'."""
    db = SessionLocal()
    try:
        phone = "919000000004"
        db.query(WhatsAppConversation).filter_by(phone_number=phone).delete()
        db.commit()

        conv = WhatsAppConversation(
            phone_number=phone,
            current_state="FLIGHT_INPUT",
            selected_category="Airport Services",
            selected_airport_iata="DEL",
            selected_airport_name="Indira Gandhi International Airport",
            selected_service_id="del-intl-silver",
            selected_service_name="Silver Service",
            passenger_count=1,
            flight_details_json={
                "journey_type": "DEPARTURE",
                "travel_type": "INTERNATIONAL",
                "flight_type": "INTERNATIONAL",
                "terminal": "Terminal 3",
                "unit_price": 4500.0,
            },
        )
        db.add(conv)
        db.commit()

        with patch("app.flight.aviationstack_service.verify_flight_for_whatsapp", return_value=_mock_6e224_flight()):
            with patch("app.integrations.whatsapp.client.WhatsAppClient.send_interactive_buttons") as mock_btn:
                mock_btn.return_value = {"success": True}
                res = WhatsAppBookingStateMachine._state_flight_input(db, conv, "6E224")

                assert res["status"] == "flight_type_mismatch"
                assert conv.current_state == "FLIGHT_TYPE_MISMATCH"
                args, kwargs = mock_btn.call_args
                buttons = kwargs.get("buttons") or []
                button_titles = [b["title"] for b in buttons]
                assert "Switch to Domestic" in button_titles
    finally:
        db.close()


def test_scenario_5_unverified_flight_fallback_intact():
    """Scenario 5: Unverified flight number -> Existing retry options without entering mismatch state."""
    db = SessionLocal()
    try:
        phone = "919000000005"
        db.query(WhatsAppConversation).filter_by(phone_number=phone).delete()
        db.commit()

        conv = WhatsAppConversation(
            phone_number=phone,
            current_state="FLIGHT_INPUT",
            selected_category="Airport Services",
            selected_airport_iata="DEL",
            selected_airport_name="Indira Gandhi International Airport",
            selected_service_id="del-dom-silver",
            flight_details_json={"journey_type": "DEPARTURE", "travel_type": "DOMESTIC"},
        )
        db.add(conv)
        db.commit()

        mock_failed = {"success": False, "reason": "not_found"}
        with patch("app.flight.aviationstack_service.verify_flight_for_whatsapp", return_value=mock_failed):
            with patch("app.integrations.whatsapp.client.WhatsAppClient.send_interactive_buttons") as mock_btn:
                mock_btn.return_value = {"success": True}
                res = WhatsAppBookingStateMachine._state_flight_input(db, conv, "ZZ9999")

                assert res["status"] in ("flight_not_found", "flight_verify_failed")
                # Conversation stays in FLIGHT_INPUT
                assert conv.current_state == "FLIGHT_INPUT"
    finally:
        db.close()


def test_scenario_6_option_1_switch_to_international():
    """
    Scenario 6:
    - User in FLIGHT_TYPE_MISMATCH taps 'Switch to Intl'
    - travel_type switches to INTERNATIONAL
    - _pending_verified_flight is preserved
    - previous domestic service & pricing cleared
    - transitions to SERVICE_SELECTION
    - user picks an international package -> preserved flight automatically committed, moves to DATE_SELECTION.
    """
    db = SessionLocal()
    try:
        phone = "919000000006"
        db.query(WhatsAppConversation).filter_by(phone_number=phone).delete()
        db.commit()

        conv = WhatsAppConversation(
            phone_number=phone,
            current_state="FLIGHT_TYPE_MISMATCH",
            selected_category="Airport Services",
            selected_airport_iata="DEL",
            selected_airport_name="Indira Gandhi International Airport",
            selected_service_id="old-dom-svc",
            selected_service_name="Old Domestic Package",
            total_amount=5000.0,
            passenger_count=2,
            flight_details_json={
                "journey_type": "ARRIVAL",
                "travel_type": "DOMESTIC",
                "flight_type": "DOMESTIC",
                "unit_price": 2500.0,
                "_pending_mismatch_flight": {
                    "flight_number": "EK501",
                    "origin_iata": "DXB",
                    "destination_iata": "DEL",
                    "origin_city": "Dubai",
                    "destination_city": "Delhi",
                    "origin_airport": "Dubai International",
                    "destination_airport": "Indira Gandhi International",
                    "airline_name": "Emirates",
                    "airline_code": "EK",
                    "departure_scheduled": "2026-09-10T20:00:00+00:00",
                    "arrival_scheduled": "2026-09-11T02:45:00+00:00",
                    "actual_flight_type": "INTERNATIONAL",
                    "selected_flight_type": "DOMESTIC",
                    "api_flight": _mock_ek501_flight()["flight"],
                },
            },
        )
        db.add(conv)
        db.commit()

        with patch("app.integrations.whatsapp.client.WhatsAppClient.send_interactive_buttons") as mock_btn, \
             patch("app.integrations.whatsapp.client.WhatsAppClient.send_interactive_list") as mock_list, \
             patch("app.integrations.whatsapp.client.WhatsAppClient.send_text_message") as mock_txt:
            mock_btn.return_value = {"success": True}
            mock_list.return_value = {"success": True}
            mock_txt.return_value = {"success": True}

            # Step 1: User chooses Option 1
            res = WhatsAppBookingStateMachine.process_incoming_event(db, phone, "btn_switch_travel_type")
            db.refresh(conv)

            # Verification of state after switch
            assert conv.current_state == "SERVICE_SELECTION"
            assert isinstance(conv.flight_details_json, dict)
            assert conv.flight_details_json["travel_type"] == "INTERNATIONAL"
            assert conv.flight_details_json["flight_type"] == "INTERNATIONAL"
            assert conv.selected_service_id is None
            assert conv.selected_service_name is None
            assert conv.total_amount is None
            assert "_pending_verified_flight" in conv.flight_details_json
            assert conv.flight_details_json["_pending_verified_flight"]["flight_number"] == "EK501"

            # Step 2: In SERVICE_SELECTION, user chooses an international package
            # Let's mock a package in stored_menu
            stored_svc = {
                "id": "del-intl-silver-id",
                "name": "Silver Service",
                "price": 3500.0,
                "tier": "silver",
            }
            conv.whatsapp_state_json = {"menu_options": [stored_svc]}
            db.commit()

            res2 = WhatsAppBookingStateMachine.process_incoming_event(db, phone, "1")
            db.refresh(conv)

            # Must have committed flight EK501 and advanced directly to DATE_SELECTION
            assert conv.current_state == "DATE_SELECTION"
            assert conv.flight_num == "EK501"
            assert isinstance(conv.flight_details_json, dict)
            assert conv.flight_details_json["verification_status"] == "verified"
            assert conv.selected_service_id is not None
            assert conv.total_amount is not None

            # Text message sent should prompt for date of travel, NOT flight number
            last_txt_call = mock_txt.call_args[0][1]
            assert "Date of Travel" in last_txt_call
            assert "EK501" in last_txt_call
    finally:
        db.close()


def test_scenario_7_option_2_reenter_flight():
    """Scenario 7: Option 2 ('Re-enter Flight') -> keeps travel_type & airport, clears flight, transitions to FLIGHT_INPUT."""
    db = SessionLocal()
    try:
        phone = "919000000007"
        db.query(WhatsAppConversation).filter_by(phone_number=phone).delete()
        db.commit()

        conv = WhatsAppConversation(
            phone_number=phone,
            current_state="FLIGHT_TYPE_MISMATCH",
            selected_category="Airport Services",
            selected_airport_iata="DEL",
            selected_airport_name="Indira Gandhi International Airport",
            selected_service_id="del-dom-silver",
            selected_service_name="Silver Service",
            flight_details_json={
                "journey_type": "DEPARTURE",
                "travel_type": "DOMESTIC",
                "flight_type": "DOMESTIC",
                "_pending_mismatch_flight": {
                    "flight_number": "EK501",
                    "actual_flight_type": "INTERNATIONAL",
                },
            },
        )
        db.add(conv)
        db.commit()

        with patch("app.integrations.whatsapp.client.WhatsAppClient.send_text_message") as mock_txt:
            mock_txt.return_value = {"success": True}
            res = WhatsAppBookingStateMachine.process_incoming_event(db, phone, "btn_reenter_flight")
            db.refresh(conv)

            assert conv.current_state == "FLIGHT_INPUT"
            assert conv.flight_num is None
            assert isinstance(conv.flight_details_json, dict)
            assert "_pending_mismatch_flight" not in conv.flight_details_json
            assert conv.flight_details_json["travel_type"] == "DOMESTIC"
            assert conv.selected_airport_iata == "DEL"
            assert "Flight Number" in mock_txt.call_args[0][1]
    finally:
        db.close()


def test_scenario_8_option_3_change_airport():
    """Scenario 8: Option 3 ('Change Airport') -> clears airport/flight context, returns to AIRPORT_SELECTION."""
    db = SessionLocal()
    try:
        phone = "919000000008"
        db.query(WhatsAppConversation).filter_by(phone_number=phone).delete()
        db.commit()

        conv = WhatsAppConversation(
            phone_number=phone,
            current_state="FLIGHT_TYPE_MISMATCH",
            selected_category="Airport Services",
            selected_airport_iata="DEL",
            selected_airport_name="Indira Gandhi International Airport",
            flight_details_json={
                "journey_type": "DEPARTURE",
                "travel_type": "DOMESTIC",
                "_pending_mismatch_flight": {"flight_number": "EK501"},
            },
        )
        db.add(conv)
        db.commit()

        with patch("app.integrations.whatsapp.client.WhatsAppClient.send_text_message") as mock_txt:
            mock_txt.return_value = {"success": True}
            res = WhatsAppBookingStateMachine.process_incoming_event(db, phone, "btn_change_airport")
            db.refresh(conv)

            assert conv.current_state == "AIRPORT_SELECTION"
            assert conv.selected_airport_iata is None
            assert conv.flight_num is None
            assert "_pending_mismatch_flight" not in (conv.flight_details_json or {})
    finally:
        db.close()


def test_scenario_9_reverse_switch_to_domestic():
    """Scenario 9: International selected + Domestic flight + Option 1 -> Switches to DOMESTIC, preserves flight."""
    db = SessionLocal()
    try:
        phone = "919000000009"
        db.query(WhatsAppConversation).filter_by(phone_number=phone).delete()
        db.commit()

        conv = WhatsAppConversation(
            phone_number=phone,
            current_state="FLIGHT_TYPE_MISMATCH",
            selected_category="Airport Services",
            selected_airport_iata="DEL",
            selected_airport_name="Indira Gandhi International Airport",
            selected_service_id="del-intl-gold",
            flight_details_json={
                "journey_type": "DEPARTURE",
                "travel_type": "INTERNATIONAL",
                "flight_type": "INTERNATIONAL",
                "_pending_mismatch_flight": {
                    "flight_number": "6E224",
                    "origin_iata": "DEL",
                    "destination_iata": "BOM",
                    "actual_flight_type": "DOMESTIC",
                    "selected_flight_type": "INTERNATIONAL",
                    "api_flight": _mock_6e224_flight()["flight"],
                },
            },
        )
        db.add(conv)
        db.commit()

        with patch("app.integrations.whatsapp.client.WhatsAppClient.send_interactive_buttons") as mock_btn, \
             patch("app.integrations.whatsapp.client.WhatsAppClient.send_interactive_list") as mock_list, \
             patch("app.integrations.whatsapp.client.WhatsAppClient.send_text_message") as mock_txt:
            mock_btn.return_value = {"success": True}
            mock_list.return_value = {"success": True}
            mock_txt.return_value = {"success": True}

            res = WhatsAppBookingStateMachine.process_incoming_event(db, phone, "btn_switch_travel_type")
            db.refresh(conv)

            assert conv.current_state == "SERVICE_SELECTION"
            assert isinstance(conv.flight_details_json, dict)
            assert conv.flight_details_json["travel_type"] == "DOMESTIC"
            assert conv.flight_details_json["flight_type"] == "DOMESTIC"
            assert conv.flight_details_json["_pending_verified_flight"]["flight_number"] == "6E224"
    finally:
        db.close()


def test_scenario_10_mismatch_state_cannot_be_bypassed():
    """Scenario 10: Arbitrary input while in FLIGHT_TYPE_MISMATCH is rejected and re-prompts choices."""
    db = SessionLocal()
    try:
        phone = "919000000010"
        db.query(WhatsAppConversation).filter_by(phone_number=phone).delete()
        db.commit()

        conv = WhatsAppConversation(
            phone_number=phone,
            current_state="FLIGHT_TYPE_MISMATCH",
            selected_category="Airport Services",
            selected_airport_iata="DEL",
            flight_details_json={
                "journey_type": "DEPARTURE",
                "travel_type": "DOMESTIC",
                "_pending_mismatch_flight": {
                    "flight_number": "EK501",
                    "actual_flight_type": "INTERNATIONAL",
                    "selected_flight_type": "DOMESTIC",
                },
            },
        )
        db.add(conv)
        db.commit()

        with patch("app.integrations.whatsapp.client.WhatsAppClient.send_interactive_buttons") as mock_btn, \
             patch("app.integrations.whatsapp.client.WhatsAppClient.send_text_message") as mock_txt:
            mock_btn.return_value = {"success": True}

            res = WhatsAppBookingStateMachine.process_incoming_event(db, phone, "I want to confirm this booking please")
            db.refresh(conv)

            # State MUST NOT change
            assert conv.current_state == "FLIGHT_TYPE_MISMATCH"
            # Buttons must have been re-sent
            assert mock_btn.called
    finally:
        db.close()


def test_scenario_11_mismatch_back_action():
    """Scenario 11: 'BACK' while in FLIGHT_TYPE_MISMATCH returns to SERVICE_SELECTION and clears mismatch flight."""
    db = SessionLocal()
    try:
        phone = "919000000011"
        db.query(WhatsAppConversation).filter_by(phone_number=phone).delete()
        db.commit()

        conv = WhatsAppConversation(
            phone_number=phone,
            current_state="FLIGHT_TYPE_MISMATCH",
            selected_category="Airport Services",
            selected_airport_iata="DEL",
            selected_airport_name="Indira Gandhi International Airport",
            requires_airport=True,
            flight_details_json={
                "journey_type": "DEPARTURE",
                "travel_type": "DOMESTIC",
                "flight_type": "DOMESTIC",
                "_pending_mismatch_flight": {"flight_number": "EK501"},
            },
        )
        db.add(conv)
        db.commit()

        with patch("app.integrations.whatsapp.client.WhatsAppClient.send_interactive_buttons") as mock_btn, \
             patch("app.integrations.whatsapp.client.WhatsAppClient.send_interactive_list") as mock_list, \
             patch("app.integrations.whatsapp.client.WhatsAppClient.send_text_message") as mock_txt:
            mock_btn.return_value = {"success": True}
            mock_list.return_value = {"success": True}
            mock_txt.return_value = {"success": True}

            res = WhatsAppBookingStateMachine.process_incoming_event(db, phone, "BACK")
            db.refresh(conv)

            assert conv.current_state == "SERVICE_SELECTION"
            assert conv.flight_num is None
            assert isinstance(conv.flight_details_json, dict)
            assert "_pending_mismatch_flight" not in conv.flight_details_json
            assert conv.flight_details_json["travel_type"] == "DOMESTIC"
    finally:
        db.close()


def test_scenario_12_defensive_booking_creation_safeguard():
    """Scenario 12: Defensive safeguard in BookingService.create_booking rejects inconsistent travel type vs verified route."""
    db = SessionLocal()
    try:
        payload = BookingCreate(
            passenger_name="Aariz Test",
            passenger_email="aariz@gmail.com",
            passenger_phone="919876543210",
            service_category="Airport Services",
            service_type="Meet & Greet",
            flight_num="EK501",
            origin_code="DXB",       # Dubai (International)
            dest_code="DEL",         # Delhi
            booking_date="25/11/2026",
            departure_time="2026-11-25T20:00:00Z",
            passenger_count=1,
            total_amount=2500.0,
            currency="INR",
            metadata_json={
                "journey_type": "ARRIVAL",
                "travel_type": "DOMESTIC",  # INCONSISTENT with DXB->DEL route!
            },
        )

        with pytest.raises(HTTPException) as exc_info:
            BookingService.create_booking(db, payload)

        assert exc_info.value.status_code == 400
        assert "Flight type mismatch" in exc_info.value.detail
    finally:
        db.close()
