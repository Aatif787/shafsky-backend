"""
Unit and regression test verifying that future travel dates (e.g. 31/08/2026 when booked on 28/08/2026)
align flight scheduled datetimes properly and pass booking cutoff validation without false rejections.
"""

from __future__ import annotations

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch
import uuid

from app.services.booking_cutoff import (
    IST,
    evaluate_booking_cutoff,
    parse_scheduled_datetime,
    service_leg_scheduled_datetime,
)
from app.integrations.whatsapp.service import WhatsAppBookingStateMachine


def test_service_leg_scheduled_datetime_aligns_future_travel_date():
    tz = IST
    # Stale lookup timestamp from AviationStack on 28 Aug 2026
    dep_api = "2026-08-28T18:00:00+00:00"
    arr_api = "2026-08-28T20:00:00+00:00"

    # User chooses travel date on 31 August 2026
    travel_date_str = "31 August 2026"
    sched_dt = service_leg_scheduled_datetime("DEPARTURE", dep_api, arr_api, tz, travel_date=travel_date_str)
    assert sched_dt is not None
    assert sched_dt.date() == date(2026, 8, 31)
    assert sched_dt.hour == 18 or sched_dt.hour == 23  # UTC vs IST hour depending on timezone

    # Evaluate cutoff as of 28 Aug 2026
    now_utc = datetime(2026, 8, 28, 12, 30, 0, tzinfo=timezone.utc)
    cutoff = evaluate_booking_cutoff(
        scheduled_dt=sched_dt,
        now_utc=now_utc,
        airport_tz_name="Asia/Kolkata",
        flight_type="DOMESTIC",
    )
    assert cutoff.allowed is True
    assert cutoff.remaining is not None
    assert cutoff.remaining.total_seconds() > 70 * 3600  # Over 70 hours remaining


def test_align_scheduled_datetimes_to_date():
    metadata = {
        "departure_scheduled": "2026-08-28T14:30:00+05:30",
        "arrival_scheduled": "2026-08-28T17:00:00+05:30",
    }
    travel_date = date(2026, 8, 31)
    aligned = WhatsAppBookingStateMachine._align_scheduled_datetimes_to_date(metadata, travel_date, IST)

    assert "2026-08-31T14:30:00" in aligned["departure_scheduled"]
    assert "2026-08-31T17:00:00" in aligned["arrival_scheduled"]
    assert aligned["travel_date"] == "2026-08-31"


@patch("app.services.payment_service.PaymentService.initiate_whatsapp_payment_link")
@patch("app.integrations.whatsapp.client.WhatsAppClient.send_text_message")
def test_whatsapp_future_date_booking_flow_allows_confirmation(mock_text, mock_link):
    from app.database import SessionLocal

    mock_text.return_value = {"success": True}
    mock_link.return_value = {"success": True, "short_url": "https://rzp.io/test-pay", "payment_link_id": "plink_test"}

    db = SessionLocal()
    try:
        phone = f"91{uuid.uuid4().int % 10**10:010d}"
        conv, _ = WhatsAppBookingStateMachine.get_or_create_conversation(db, phone)
        conv.current_state = "DATE_SELECTION"
        conv.requires_flight = True
        conv.requires_date = True
        conv.selected_airport_iata = "DEL"
        conv.selected_airport_name = "Indira Gandhi International Airport"
        conv.selected_service_name = "Silver Service"
        conv.flight_num = "IX1617"
        conv.customer_name = "Farooqui"
        conv.customer_email = "aarizfarooqui786@gmail.com"
        conv.customer_phone = "918864931247"
        conv.passenger_count = 2
        conv.flight_details_json = {
            "journey_type": "DEPARTURE",
            "travel_type": "DOMESTIC",
            "verification_status": "verified",
            "verification_provider": "aviationstack",
            "origin_iata": "DEL",
            "destination_iata": "IXR",
            "departure_scheduled": "2026-08-28T18:00:00+00:00",
            "arrival_scheduled": "2026-08-28T20:00:00+00:00",
            "unit_price": 3000,
        }
        db.commit()

        # Step 1: Customer enters travel date 31/08/2026
        res_date = WhatsAppBookingStateMachine.process_incoming_event(db, phone, "31/08/2026")
        assert res_date["status"] == "passenger_count_prompt_sent"
        db.refresh(conv)
        assert conv.booking_date == "31 August 2026"
        assert "2026-08-31" in conv.flight_details_json.get("departure_scheduled", "")

        # Step 2: Transition to review and confirm
        conv.current_state = "BOOKING_REVIEW"
        conv.total_amount = 6000.0
        db.commit()

        res_confirm = WhatsAppBookingStateMachine.process_incoming_event(
            db, phone, "Confirm Booking", input_type="button_reply", input_id="btn_confirm_booking"
        )
        db.refresh(conv)

        # Ensure booking was NOT blocked by cutoff
        assert res_confirm.get("status") != "booking_cutoff_blocked"
        assert conv.booking_ref is not None
        assert conv.current_state in ("WAITING_PAYMENT", "PAYMENT_LINK_SENT")
        assert conv.booking_ref.startswith("SHF-")

        # Verify booking in database has departure_time in August 31
        from app.models.schema import Booking
        booking_row = db.query(Booking).filter(Booking.booking_ref == conv.booking_ref).first()
        assert booking_row is not None
        assert booking_row.departure_time.date() == date(2026, 8, 31)
        assert booking_row.passenger_name == "Farooqui"
        assert booking_row.total_amount == 6000.0
    finally:
        db.close()


if __name__ == "__main__":
    test_service_leg_scheduled_datetime_aligns_future_travel_date()
    print("[+] test_service_leg_scheduled_datetime_aligns_future_travel_date passed!")
    test_align_scheduled_datetimes_to_date()
    print("[+] test_align_scheduled_datetimes_to_date passed!")
    test_whatsapp_future_date_booking_flow_allows_confirmation()
    print("[+] test_whatsapp_future_date_booking_flow_allows_confirmation passed!")
    print("\n[+] ALL WHATSAPP FUTURE TRAVEL DATE CUTOFF TESTS PASSED 100%!")


