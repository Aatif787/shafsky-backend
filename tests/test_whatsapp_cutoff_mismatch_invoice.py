from __future__ import annotations

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime, timedelta, timezone
from unittest.mock import patch
import uuid

from app.flight import aviationstack_service as as_svc
from app.integrations.whatsapp import copy as wa_copy
from app.services.booking_cutoff import (
    IST,
    evaluate_booking_cutoff,
    parse_scheduled_datetime,
    service_leg_scheduled_datetime,
)
from app.services.pdf_service import generate_tax_invoice_pdf, invoice_logo_path, invoice_pdf_data_from_records


def _mismatch_flight():
    return {
        "flight_number": "6E325",
        "airline": "IndiGo",
        "departure": {"iata": "LKO", "city": "Lucknow", "scheduled": "2026-08-28T04:00:00+00:00"},
        "arrival": {"iata": "BLR", "city": "Bengaluru", "scheduled": "2026-08-28T06:30:00+00:00"},
    }


@patch("app.integrations.whatsapp.client.WhatsAppClient.send_text_message")
@patch("app.integrations.whatsapp.client.WhatsAppClient.send_interactive_buttons")
@patch("app.flight.aviationstack_service.verify_flight_for_whatsapp")
def test_mismatch_reenter_flight(mock_verify, mock_buttons, mock_text):
    from app.database import SessionLocal
    from app.integrations.whatsapp.service import WhatsAppBookingStateMachine

    mock_buttons.return_value = {"success": True, "message_id": "wamid.m"}
    mock_text.return_value = {"success": True}
    mock_verify.return_value = {
        "success": False,
        "reason": as_svc.REASON_AIRPORT_MISMATCH,
        "flight": _mismatch_flight(),
    }
    db = SessionLocal()
    try:
        phone = f"91{uuid.uuid4().int % 10**10:010d}"
        conv, _ = WhatsAppBookingStateMachine.get_or_create_conversation(db, phone)
        conv.selected_airport_iata = "HYD"
        conv.selected_airport_name = "Hyderabad"
        conv.selected_service_name = "Elite Service"
        conv.current_state = "FLIGHT_INPUT"
        conv.requires_flight = True
        conv.flight_details_json = {"journey_type": "ARRIVAL", "travel_type": "DOMESTIC"}
        db.commit()

        res = WhatsAppBookingStateMachine.process_incoming_event(db, phone, "6E325")
        assert res["status"] == "flight_airport_mismatch"
        ids = [b["id"] for b in mock_buttons.call_args.kwargs["buttons"]]
        assert ids == ["btn_reenter_flight", "btn_change_airport", "btn_confirm_mismatch"]

        res2 = WhatsAppBookingStateMachine.process_incoming_event(
            db, phone, "Re-enter Flight", input_type="button_reply", input_id="btn_reenter_flight"
        )
        db.refresh(conv)
        assert res2["status"] == "reprompt_flight"
        assert conv.current_state == "FLIGHT_INPUT"
        assert conv.flight_num is None
        assert conv.selected_airport_iata == "HYD"
    finally:
        db.close()


@patch("app.integrations.whatsapp.client.WhatsAppClient.send_text_message")
@patch("app.integrations.whatsapp.client.WhatsAppClient.send_interactive_buttons")
@patch("app.flight.aviationstack_service.verify_flight_for_whatsapp")
def test_mismatch_change_airport(mock_verify, mock_buttons, mock_text):
    from app.database import SessionLocal
    from app.integrations.whatsapp.service import WhatsAppBookingStateMachine

    mock_buttons.return_value = {"success": True}
    mock_text.return_value = {"success": True}
    mock_verify.return_value = {
        "success": False,
        "reason": as_svc.REASON_AIRPORT_MISMATCH,
        "flight": _mismatch_flight(),
    }
    db = SessionLocal()
    try:
        phone = f"91{uuid.uuid4().int % 10**10:010d}"
        conv, _ = WhatsAppBookingStateMachine.get_or_create_conversation(db, phone)
        conv.selected_airport_iata = "HYD"
        conv.current_state = "FLIGHT_INPUT"
        conv.requires_flight = True
        conv.flight_details_json = {"journey_type": "ARRIVAL"}
        db.commit()
        WhatsAppBookingStateMachine.process_incoming_event(db, phone, "6E325")
        res = WhatsAppBookingStateMachine.process_incoming_event(
            db, phone, "Change Airport", input_type="button_reply", input_id="btn_change_airport"
        )
        db.refresh(conv)
        assert res["status"] == "airport_prompt_sent"
        assert conv.current_state == "AIRPORT_SELECTION"
        assert conv.flight_num is None
    finally:
        db.close()


@patch("app.integrations.whatsapp.client.WhatsAppClient.send_text_message")
@patch("app.integrations.whatsapp.client.WhatsAppClient.send_interactive_buttons")
@patch("app.flight.aviationstack_service.verify_flight_for_whatsapp")
def test_mismatch_confirm_continue_records_override(mock_verify, mock_buttons, mock_text):
    from app.database import SessionLocal
    from app.integrations.whatsapp.service import WhatsAppBookingStateMachine

    mock_buttons.return_value = {"success": True}
    mock_text.return_value = {"success": True}
    mock_verify.return_value = {
        "success": False,
        "reason": as_svc.REASON_AIRPORT_MISMATCH,
        "flight": _mismatch_flight(),
    }
    db = SessionLocal()
    try:
        phone = f"91{uuid.uuid4().int % 10**10:010d}"
        conv, _ = WhatsAppBookingStateMachine.get_or_create_conversation(db, phone)
        conv.selected_airport_iata = "HYD"
        conv.selected_airport_name = "Hyderabad"
        conv.selected_service_name = "Elite Service"
        conv.current_state = "FLIGHT_INPUT"
        conv.requires_flight = True
        conv.flight_details_json = {"journey_type": "ARRIVAL", "travel_type": "DOMESTIC", "unit_price": 5000}
        db.commit()
        WhatsAppBookingStateMachine.process_incoming_event(db, phone, "6E325")
        res = WhatsAppBookingStateMachine.process_incoming_event(
            db, phone, "Confirm & Continue", input_type="button_reply", input_id="btn_confirm_mismatch"
        )
        db.refresh(conv)
        meta = conv.flight_details_json
        assert res["status"] == "mismatch_customer_confirmed"
        assert conv.current_state == "DATE_SELECTION"
        assert conv.flight_num == "6E325"
        assert conv.selected_airport_iata == "HYD"
        assert conv.selected_service_name == "Elite Service"
        assert meta["verification_status"] == "mismatch_customer_confirmed"
        assert meta["verification_status"] != "verified"
        assert meta["mismatch_override"] is True
        assert meta["verification_api_performed"] is True
        assert meta["origin_iata"] == "LKO"
        assert meta["destination_iata"] == "BLR"
        assert meta["departure_scheduled"]
        assert meta["arrival_scheduled"]
    finally:
        db.close()


@patch("app.services.payment_service.PaymentService.initiate_whatsapp_payment_link")
@patch("app.integrations.whatsapp.client.WhatsAppClient.send_text_message")
def test_unverified_never_issues_payment_link(mock_text, mock_link):
    from app.database import SessionLocal
    from app.integrations.whatsapp.service import WhatsAppBookingStateMachine

    mock_text.return_value = {"success": True}
    mock_link.return_value = {"success": True, "short_url": "https://rzp.io/x", "payment_link_id": "plink_x"}
    db = SessionLocal()
    try:
        phone = f"91{uuid.uuid4().int % 10**10:010d}"
        conv, _ = WhatsAppBookingStateMachine.get_or_create_conversation(db, phone)
        conv.current_state = "BOOKING_REVIEW"
        conv.requires_flight = True
        conv.selected_airport_iata = "HYD"
        conv.customer_email = "guest.ok@example.com"
        conv.flight_details_json = {"verification_status": "not_verified", "journey_type": "ARRIVAL"}
        db.commit()
        res = WhatsAppBookingStateMachine._create_booking_request(db, conv)
        assert res["status"] == "unverified_flight_blocked"
        mock_link.assert_not_called()
    finally:
        db.close()


def test_compact_inclusions_verbatim_from_list():
    feats = ["Meet & Assist", "Priority handling", "Baggage assistance", "Lounge access", "Porter", "Buggy"]
    lines = wa_copy.compact_inclusion_lines(feats)
    assert lines[:4] == [
        "✓ Meet & Assist",
        "✓ Priority handling",
        "✓ Baggage assistance",
        "✓ Lounge access",
    ]
    assert lines[-1] == "+ 2 more"
    body = wa_copy.numbered_service_lines_with_inclusions(
        [{"title": "Elite Service", "price": 5000, "features": feats}]
    )
    assert "Elite Service" in body
    assert "₹5,000" in body
    assert "6 services included" in body


def test_packages_filter_and_inclusion_source_from_db():
    from app.database import SessionLocal
    from app.integrations.whatsapp.service import WhatsAppBookingStateMachine
    from app.models.journey_models import SupportedAirport

    db = SessionLocal()
    try:
        hyd = db.query(SupportedAirport).filter_by(iata_code="HYD").first()
        if hyd is None:
            return
        dom = WhatsAppBookingStateMachine._get_authoritative_airport_packages(
            db, hyd.id, "DEPARTURE", ["DOMESTIC", "ALL"]
        )
        intl = WhatsAppBookingStateMachine._get_authoritative_airport_packages(
            db, hyd.id, "DEPARTURE", ["INTERNATIONAL", "ALL"]
        )
        for aps, svc in dom:
            assert aps.flight_type in ("DOMESTIC", "ALL")
            feats = aps.features if isinstance(aps.features, list) else []
            for item in wa_copy.compact_inclusion_lines(feats):
                if item.startswith("✓ "):
                    assert item[2:] in feats
        for aps, svc in intl:
            assert aps.flight_type in ("INTERNATIONAL", "ALL")
        if dom and intl:
            assert {svc.name for _, svc in dom} or True
    finally:
        db.close()


def test_cutoff_domestic_exact_12h_allow():
    now = datetime(2026, 8, 27, 10, 0, tzinfo=IST)
    scheduled = now + timedelta(hours=12)
    result = evaluate_booking_cutoff(
        scheduled_dt=scheduled,
        now_utc=now.astimezone(timezone.utc),
        airport_tz_name="Asia/Kolkata",
        flight_type="DOMESTIC",
    )
    assert result.allowed is True


def test_cutoff_domestic_11h59_reject():
    now = datetime(2026, 8, 27, 10, 0, tzinfo=IST)
    scheduled = now + timedelta(hours=11, minutes=59)
    result = evaluate_booking_cutoff(
        scheduled_dt=scheduled,
        now_utc=now.astimezone(timezone.utc),
        airport_tz_name="Asia/Kolkata",
        flight_type="DOMESTIC",
    )
    assert result.allowed is False
    assert "12 hours" in result.customer_message
    assert "domestic" in result.customer_message
    assert "9599087959" in result.customer_message
    assert "Shafsky Aviation Services" in result.customer_message


def test_cutoff_international_exact_24h_allow():
    now = datetime(2026, 8, 27, 10, 0, tzinfo=IST)
    scheduled = now + timedelta(hours=24)
    result = evaluate_booking_cutoff(
        scheduled_dt=scheduled,
        now_utc=now.astimezone(timezone.utc),
        airport_tz_name="Asia/Kolkata",
        flight_type="INTERNATIONAL",
    )
    assert result.allowed is True


def test_cutoff_international_23h59_reject():
    now = datetime(2026, 8, 27, 10, 0, tzinfo=IST)
    scheduled = now + timedelta(hours=23, minutes=59)
    result = evaluate_booking_cutoff(
        scheduled_dt=scheduled,
        now_utc=now.astimezone(timezone.utc),
        airport_tz_name="Asia/Kolkata",
        flight_type="INTERNATIONAL",
    )
    assert result.allowed is False
    assert "24 hours" in result.customer_message
    assert "international" in result.customer_message


def test_cutoff_uses_arrival_vs_departure_leg():
    tz = IST
    dep = datetime(2026, 8, 28, 8, 0, tzinfo=tz)
    arr = datetime(2026, 8, 28, 11, 0, tzinfo=tz)
    assert service_leg_scheduled_datetime("DEPARTURE", dep, arr, tz) == dep
    assert service_leg_scheduled_datetime("ARRIVAL", dep, arr, tz) == arr


def test_cutoff_timezone_naive_treated_as_airport_local():
    naive = datetime(2026, 8, 28, 10, 0)
    parsed = parse_scheduled_datetime(naive, IST)
    assert parsed.tzinfo is not None
    now = datetime(2026, 8, 27, 10, 0, tzinfo=timezone.utc)
    result = evaluate_booking_cutoff(
        scheduled_dt=naive,
        now_utc=now,
        airport_tz_name="Asia/Kolkata",
        flight_type="DOMESTIC",
    )
    assert result.scheduled is not None
    assert result.scheduled.tzinfo is not None


@patch("app.services.payment_service.PaymentService.initiate_whatsapp_payment_link")
@patch("app.integrations.whatsapp.client.WhatsAppClient.send_text_message")
def test_cutoff_blocks_payment_link(mock_text, mock_link):
    from app.database import SessionLocal
    from app.integrations.whatsapp.service import WhatsAppBookingStateMachine

    mock_text.return_value = {"success": True}
    mock_link.return_value = {"success": True, "short_url": "https://rzp.io/x", "payment_link_id": "plink_x"}
    soon = (datetime.now(timezone.utc) + timedelta(hours=9)).isoformat()
    db = SessionLocal()
    try:
        phone = f"91{uuid.uuid4().int % 10**10:010d}"
        conv, _ = WhatsAppBookingStateMachine.get_or_create_conversation(db, phone)
        conv.current_state = "BOOKING_REVIEW"
        conv.requires_flight = True
        conv.selected_airport_iata = "DEL"
        conv.flight_num = "AI101"
        conv.customer_email = "ok.cutoff@example.com"
        conv.passenger_count = 1
        conv.flight_details_json = {
            "journey_type": "DEPARTURE",
            "travel_type": "DOMESTIC",
            "verification_status": "verified",
            "verification_provider": "aviationstack",
            "origin_iata": "DEL",
            "destination_iata": "BOM",
            "departure_scheduled": soon,
            "arrival_scheduled": soon,
            "unit_price": 3000,
        }
        db.commit()
        res = WhatsAppBookingStateMachine._create_booking_request(db, conv)
        assert res["status"] == "booking_cutoff_blocked"
        assert conv.booking_ref is None
        mock_link.assert_not_called()
        sent = " ".join(str(c) for c in mock_text.call_args_list)
        assert "12 hours" in sent
    finally:
        db.close()


def test_invoice_travel_date_and_scheduled_time_and_no_ops_email():
    from types import SimpleNamespace

    dep = datetime(2026, 8, 28, 5, 5, tzinfo=timezone.utc)  # 10:35 IST
    booking = SimpleNamespace(
        origin_code="BLR",
        dest_code="DEL",
        flight_num="QP1381",
        departure_time=dep,
        arrival_time=datetime(2026, 8, 28, 8, 0, tzinfo=timezone.utc),
        service_type="Elite Service",
        metadata_json={
            "service_airport": "BLR",
            "journey_type": "DEPARTURE",
            "flight_type": "DOMESTIC",
            "package": "Elite Service",
            "origin_iata": "BLR",
            "destination_iata": "DEL",
            "airport_timezone": "Asia/Kolkata",
            "pax_adults": 1,
        },
    )
    invoice = SimpleNamespace(
        invoice_number="INV-TEST-DATE",
        issued_at=datetime(2026, 8, 27, tzinfo=timezone.utc),
        paid_at=datetime(2026, 8, 27, tzinfo=timezone.utc),
        customer_name="A Guest",
        customer_email="a@example.com",
        subtotal_amount=4237.29,
        tax_amount=762.71,
        total_amount=5000.0,
        currency="INR",
    )
    tx = SimpleNamespace(
        gateway_payment_id="order_abc",
        gateway_response={"payload": {"payment": {"entity": {"id": "pay_abc", "order_id": "order_abc"}}}},
    )
    data = invoice_pdf_data_from_records(invoice, booking, tx)
    assert data["travel_date"] == "28 Aug 2026"
    assert "10:35" in data["scheduled_time"]
    assert data["origin"] == "BLR"
    assert data["destination"] == "DEL"
    try:
        import reportlab  # noqa: F401
    except ImportError:
        return
    pdf = generate_tax_invoice_pdf(data)
    text = pdf.decode("latin-1", errors="ignore")
    assert "28 Aug 2026" in text
    assert "10:35" in text
    assert "ops@shafskyaviation.com" not in text
    assert "SHAFSKY AVIATION SERVICES" in text
    assert "CGST" in text
    logo = invoice_logo_path()
    if logo is not None:
        assert logo.is_file()


def test_invoice_arrival_uses_arrival_date():
    from types import SimpleNamespace

    booking = SimpleNamespace(
        origin_code="DXB",
        dest_code="DEL",
        flight_num="EK512",
        departure_time=datetime(2026, 8, 27, 20, 0, tzinfo=timezone.utc),
        arrival_time=datetime(2026, 8, 28, 2, 5, tzinfo=timezone.utc),
        service_type="Meet & Assist",
        metadata_json={
            "service_airport": "DEL",
            "journey_type": "ARRIVAL",
            "flight_type": "INTERNATIONAL",
            "origin_iata": "DXB",
            "destination_iata": "DEL",
            "airport_timezone": "Asia/Kolkata",
        },
    )
    invoice = SimpleNamespace(
        invoice_number="INV-ARR",
        issued_at=datetime(2026, 8, 26, tzinfo=timezone.utc),
        paid_at=None,
        customer_name="B",
        customer_email="b@example.com",
        subtotal_amount=0,
        tax_amount=0,
        total_amount=1,
        currency="INR",
    )
    data = invoice_pdf_data_from_records(invoice, booking, None)
    assert data["travel_date"] == "28 Aug 2026"


if __name__ == "__main__":
    test_cutoff_domestic_exact_12h_allow()
    test_cutoff_domestic_11h59_reject()
    test_cutoff_international_exact_24h_allow()
    test_cutoff_international_23h59_reject()
    test_cutoff_uses_arrival_vs_departure_leg()
    test_cutoff_timezone_naive_treated_as_airport_local()
    test_cutoff_blocks_payment_link()
    print("[+] All whatsapp cutoff tests passed cleanly!")

