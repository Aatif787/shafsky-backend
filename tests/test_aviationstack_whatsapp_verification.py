"""AviationStack secondary verification for WhatsApp flight confirmation."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.flight import aviationstack_service as as_svc


SAMPLE_DEL_BOM = {
    "flight_date": "2026-08-26",
    "flight_status": "scheduled",
    "departure": {"airport": "Indira Gandhi International", "iata": "DEL", "city": "New Delhi"},
    "arrival": {"airport": "Chhatrapati Shivaji Maharaj International", "iata": "BOM", "city": "Mumbai"},
    "airline": {"name": "IndiGo", "iata": "6E"},
    "flight": {"iata": "6E224", "number": "224"},
}

SAMPLE_BOM_DEL = {
    "flight_date": "2026-08-26",
    "flight_status": "scheduled",
    "departure": {"airport": "Chhatrapati Shivaji Maharaj International", "iata": "BOM", "city": "Mumbai"},
    "arrival": {"airport": "Indira Gandhi International", "iata": "DEL", "city": "New Delhi"},
    "airline": {"name": "IndiGo", "iata": "6E"},
    "flight": {"iata": "6E225", "number": "225"},
}

SAMPLE_BOM_HYD = {
    "flight_date": "2026-08-26",
    "flight_status": "scheduled",
    "departure": {"airport": "Mumbai", "iata": "BOM", "city": "Mumbai"},
    "arrival": {"airport": "Hyderabad", "iata": "HYD", "city": "Hyderabad"},
    "airline": {"name": "IndiGo", "iata": "6E"},
    "flight": {"iata": "6E224", "number": "224"},
}


@pytest.fixture(autouse=True)
def clear_as_cache():
    from app.database import SessionLocal
    from app.models.schema import FlightAPICache
    try:
        with SessionLocal() as session:
            session.query(FlightAPICache).filter_by(provider="aviationstack").delete()
            session.commit()
    except Exception:
        pass
    yield
    try:
        with SessionLocal() as session:
            session.query(FlightAPICache).filter_by(provider="aviationstack").delete()
            session.commit()
    except Exception:
        pass


def _mock_http_json(payload, status_code=200):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = payload
    return resp


def test_normalize_flight_number():
    assert as_svc.normalize_flight_number_input(" 6e-224 ") == "6E224"
    assert as_svc.normalize_flight_number_input("ai 2424") == "AI2424"
    assert as_svc.normalize_flight_number_input("bad") is None


def test_valid_departure_match(monkeypatch):
    monkeypatch.setattr(as_svc.settings, "AVIATIONSTACK_API_KEY", "test-key")

    with patch("app.flight.aviationstack_service.httpx.Client") as client_cls:
        client = MagicMock()
        client.__enter__.return_value = client
        client.__exit__.return_value = False
        client.get.return_value = _mock_http_json({"data": [SAMPLE_DEL_BOM]})
        client_cls.return_value = client

        result = as_svc.verify_flight_for_whatsapp(
            "6E224",
            selected_airport_iata="DEL",
            journey_type="DEPARTURE",
            selected_airport_name="Indira Gandhi International Airport",
        )

    assert result["success"] is True
    assert result["reason"] == as_svc.REASON_OK
    assert result["flight"]["departure"]["iata"] == "DEL"
    assert result["flight"]["arrival"]["iata"] == "BOM"
    assert "IndiGo" in (result["flight"].get("airline") or "")
    msg = as_svc.build_whatsapp_verified_message(
        flight=result["flight"],
        selected_airport_iata="DEL",
        selected_airport_name="Indira Gandhi International Airport",
        journey_type="DEPARTURE",
    )
    assert "Flight Details" in msg
    assert "Flight verified successfully" in msg
    assert "Flight Number Received" not in msg
    assert "DEL" in msg and "BOM" in msg
    assert "Origin:" in msg and "Destination:" in msg


def test_valid_arrival_match(monkeypatch):
    monkeypatch.setattr(as_svc.settings, "AVIATIONSTACK_API_KEY", "test-key")

    with patch("app.flight.aviationstack_service.httpx.Client") as client_cls:
        client = MagicMock()
        client.__enter__.return_value = client
        client.__exit__.return_value = False
        client.get.return_value = _mock_http_json({"data": [SAMPLE_BOM_DEL]})
        client_cls.return_value = client

        result = as_svc.verify_flight_for_whatsapp(
            "6E225",
            selected_airport_iata="DEL",
            journey_type="ARRIVAL",
        )

    assert result["success"] is True
    assert result["flight"]["arrival"]["iata"] == "DEL"
    msg = as_svc.build_whatsapp_verified_message(
        flight=result["flight"],
        selected_airport_iata="DEL",
        selected_airport_name="IGI",
        journey_type="ARRIVAL",
    )
    assert "Flight Details" in msg
    assert "DEL" in msg
    assert "Flight Number Received" not in msg


def test_wrong_airport_mismatch(monkeypatch):
    monkeypatch.setattr(as_svc.settings, "AVIATIONSTACK_API_KEY", "test-key")

    with patch("app.flight.aviationstack_service.httpx.Client") as client_cls:
        client = MagicMock()
        client.__enter__.return_value = client
        client.__exit__.return_value = False
        client.get.return_value = _mock_http_json({"data": [SAMPLE_BOM_HYD]})
        client_cls.return_value = client

        result = as_svc.verify_flight_for_whatsapp(
            "6E224",
            selected_airport_iata="DEL",
            journey_type="ARRIVAL",
            selected_airport_name="Delhi",
        )

    assert result["success"] is False
    assert result["reason"] == as_svc.REASON_AIRPORT_MISMATCH
    msg = as_svc.build_whatsapp_mismatch_message(
        flight=result.get("flight"),
        flight_number="6E224",
        selected_airport_iata="DEL",
        selected_airport_name="Delhi",
    )
    assert "do not match the selected airport" in msg
    assert "BOM" in msg and "HYD" in msg
    assert "Flight Number Received" not in msg


def test_flight_not_found(monkeypatch):
    monkeypatch.setattr(as_svc.settings, "AVIATIONSTACK_API_KEY", "test-key")

    with patch("app.flight.aviationstack_service.httpx.Client") as client_cls:
        client = MagicMock()
        client.__enter__.return_value = client
        client.__exit__.return_value = False
        client.get.return_value = _mock_http_json({"data": []})
        client_cls.return_value = client

        result = as_svc.verify_flight_for_whatsapp(
            "ZZ9999",
            selected_airport_iata="DEL",
            journey_type="DEPARTURE",
        )

    assert result["success"] is False
    assert result["reason"] == as_svc.REASON_FLIGHT_NOT_FOUND


def test_api_error_controlled(monkeypatch):
    monkeypatch.setattr(as_svc.settings, "AVIATIONSTACK_API_KEY", "test-key")
    monkeypatch.setattr(as_svc.settings, "AVIATIONSTACK_MAX_RETRIES", 1)

    with patch("app.flight.aviationstack_service.httpx.Client") as client_cls:
        client = MagicMock()
        client.__enter__.return_value = client
        client.__exit__.return_value = False
        client.get.return_value = _mock_http_json({"error": {"code": "invalid_access_key"}}, status_code=200)
        client_cls.return_value = client

        result = as_svc.verify_flight_for_whatsapp(
            "6E224",
            selected_airport_iata="DEL",
            journey_type="DEPARTURE",
        )

    assert result["success"] is False
    assert result["reason"] == as_svc.REASON_API_ERROR


def test_api_key_never_in_result_or_logs(monkeypatch, caplog):
    secret = "super-secret-aviationstack-key"
    monkeypatch.setattr(as_svc.settings, "AVIATIONSTACK_API_KEY", secret)

    with patch("app.flight.aviationstack_service.httpx.Client") as client_cls:
        client = MagicMock()
        client.__enter__.return_value = client
        client.__exit__.return_value = False
        client.get.return_value = _mock_http_json({"data": [SAMPLE_DEL_BOM]})
        client_cls.return_value = client

        with caplog.at_level("INFO"):
            result = as_svc.verify_flight_for_whatsapp(
                "6E224",
                selected_airport_iata="DEL",
                journey_type="DEPARTURE",
            )

    assert result["success"] is True
    assert secret not in str(result)
    assert secret not in caplog.text
    assert "access_key" not in caplog.text or "***HIDDEN***" in caplog.text


def test_cache_avoids_duplicate_http(monkeypatch):
    monkeypatch.setattr(as_svc.settings, "AVIATIONSTACK_API_KEY", "test-key")

    with patch("app.flight.aviationstack_service.httpx.Client") as client_cls:
        client = MagicMock()
        client.__enter__.return_value = client
        client.__exit__.return_value = False
        client.get.return_value = _mock_http_json({"data": [SAMPLE_DEL_BOM]})
        client_cls.return_value = client

        r1 = as_svc.verify_flight_for_whatsapp("6E224", selected_airport_iata="DEL", journey_type="DEPARTURE")
        r2 = as_svc.verify_flight_for_whatsapp("6E224", selected_airport_iata="DEL", journey_type="DEPARTURE")

    assert r1["success"] and r2["success"]
    assert client.get.call_count == 1


@patch("app.integrations.whatsapp.client.WhatsAppClient.send_text_message")
@patch("app.integrations.whatsapp.client.WhatsAppClient.send_interactive_buttons")
@patch("app.flight.aviationstack_service.verify_flight_for_whatsapp")
def test_whatsapp_state_sends_verified_message(mock_verify, mock_buttons, mock_text):
    from app.database import SessionLocal
    from app.integrations.whatsapp.service import WhatsAppBookingStateMachine
    import uuid

    mock_buttons.return_value = {"success": True, "message_id": "wamid.x"}
    mock_text.return_value = {"success": True}
    mock_verify.return_value = {
        "success": True,
        "reason": "OK",
        "flight": {
            "flight_number": "6E224",
            "airline": "IndiGo",
            "airline_iata": "6E",
            "departure": {"iata": "DEL", "city": "New Delhi", "airport": "IGI"},
            "arrival": {"iata": "BOM", "city": "Mumbai", "airport": "BOM Airport"},
            "status": "scheduled",
            "provider": "aviationstack",
        },
    }

    db = SessionLocal()
    try:
        phone = f"91{uuid.uuid4().int % 10**10:010d}"
        conv, _ = WhatsAppBookingStateMachine.get_or_create_conversation(db, phone)
        conv.selected_airport_iata = "DEL"
        conv.selected_airport_name = "Indira Gandhi International Airport"
        conv.current_state = "FLIGHT_INPUT"
        conv.requires_flight = True
        conv.flight_details_json = {"journey_type": "DEPARTURE", "travel_type": "DOMESTIC"}
        db.commit()

        res = WhatsAppBookingStateMachine.process_incoming_event(db, phone, "6E224")
        db.refresh(conv)

        assert res["status"] == "flight_verified"
        assert conv.current_state == "FLIGHT_CONFIRMATION"
        assert conv.flight_num is None
        assert conv.flight_details_json["verification_status"] == "pending_confirmation"
        pending = conv.flight_details_json["_pending_verified_flight"]
        assert pending["origin_iata"] == "DEL"
        assert pending["destination_iata"] == "BOM"
        assert "origin_iata" not in conv.flight_details_json or conv.flight_details_json.get("origin_iata") is None
        body = mock_buttons.call_args.kwargs.get("body_text") or mock_buttons.call_args[1].get("body_text")
        assert "Flight Details" in body
        assert "Flight verified successfully" in body
        assert "6E224" in body
        assert "Flight Number Received" not in body
    finally:
        db.close()


@patch("app.integrations.whatsapp.client.WhatsAppClient.send_text_message")
@patch("app.integrations.whatsapp.client.WhatsAppClient.send_interactive_buttons")
@patch("app.flight.aviationstack_service.verify_flight_for_whatsapp")
def test_whatsapp_mismatch_does_not_advance(mock_verify, mock_buttons, mock_text):
    from app.database import SessionLocal
    from app.integrations.whatsapp.service import WhatsAppBookingStateMachine
    import uuid

    mock_buttons.return_value = {"success": True, "message_id": "wamid.mismatch"}
    mock_text.return_value = {"success": True}
    mock_verify.return_value = {
        "success": False,
        "reason": as_svc.REASON_AIRPORT_MISMATCH,
        "flight": {
            "flight_number": "6E224",
            "airline": "IndiGo",
            "departure": {"iata": "BOM", "city": "Mumbai"},
            "arrival": {"iata": "HYD", "city": "Hyderabad"},
        },
    }

    db = SessionLocal()
    try:
        phone = f"91{uuid.uuid4().int % 10**10:010d}"
        conv, _ = WhatsAppBookingStateMachine.get_or_create_conversation(db, phone)
        conv.selected_airport_iata = "DEL"
        conv.selected_airport_name = "Delhi"
        conv.current_state = "FLIGHT_INPUT"
        conv.requires_flight = True
        conv.flight_details_json = {"journey_type": "ARRIVAL"}
        db.commit()

        res = WhatsAppBookingStateMachine.process_incoming_event(db, phone, "6E224")
        db.refresh(conv)

        assert res["success"] is False
        assert res["status"] == "flight_airport_mismatch"
        assert conv.current_state == "FLIGHT_INPUT"
        assert conv.flight_num is None
        body = ""
        if mock_buttons.call_args:
            body = mock_buttons.call_args.kwargs.get("body_text") or ""
            if not body and len(mock_buttons.call_args) > 1:
                body = (mock_buttons.call_args[1] or {}).get("body_text") or ""
        assert "do not match the selected airport" in body
        assert "Flight Number Received" not in body
    finally:
        db.close()


def _http_client_patch(client_cls, payload, status_code=200, exc=None):
    client = MagicMock()
    client.__enter__.return_value = client
    client.__exit__.return_value = False
    if exc:
        client.get.side_effect = exc
    else:
        client.get.return_value = _mock_http_json(payload, status_code=status_code)
    client_cls.return_value = client
    return client


def test_timeout_not_accepted(monkeypatch):
    monkeypatch.setattr(as_svc.settings, "AVIATIONSTACK_API_KEY", "test-key")
    monkeypatch.setattr(as_svc.settings, "AVIATIONSTACK_MAX_RETRIES", 1)
    import httpx

    with patch("app.flight.aviationstack_service.httpx.Client") as client_cls:
        _http_client_patch(client_cls, {}, exc=httpx.TimeoutException("timed out"))
        result = as_svc.verify_flight_for_whatsapp(
            "AI2424", selected_airport_iata="DEL", journey_type="DEPARTURE"
        )
    assert result["success"] is False
    assert result["reason"] == as_svc.REASON_TIMEOUT
    assert result.get("flight") is None


def test_incomplete_api_record_not_verified(monkeypatch):
    monkeypatch.setattr(as_svc.settings, "AVIATIONSTACK_API_KEY", "test-key")
    incomplete = {
        "flight_date": "2026-08-26",
        "flight": {"iata": "AI2424"},
        "airline": {"name": "Air India", "iata": "AI"},
        "departure": {"iata": "DEL"},
        "arrival": {},
    }
    with patch("app.flight.aviationstack_service.httpx.Client") as client_cls:
        _http_client_patch(client_cls, {"data": [incomplete]})
        result = as_svc.verify_flight_for_whatsapp(
            "AI2424", selected_airport_iata="DEL", journey_type="DEPARTURE"
        )
    assert result["success"] is False
    assert result["reason"] in (as_svc.REASON_MALFORMED_RESPONSE, as_svc.REASON_FLIGHT_NOT_FOUND)


def test_del_arrival_dxb_rejected(monkeypatch):
    monkeypatch.setattr(as_svc.settings, "AVIATIONSTACK_API_KEY", "test-key")
    dxb_arr = {
        "flight_date": "2026-08-26",
        "flight_status": "scheduled",
        "departure": {"airport": "Delhi", "iata": "DEL", "city": "New Delhi"},
        "arrival": {"airport": "Dubai", "iata": "DXB", "city": "Dubai"},
        "airline": {"name": "Air India", "iata": "AI"},
        "flight": {"iata": "AI2424", "number": "2424"},
    }
    with patch("app.flight.aviationstack_service.httpx.Client") as client_cls:
        _http_client_patch(client_cls, {"data": [dxb_arr]})
        result = as_svc.verify_flight_for_whatsapp(
            "AI2424", selected_airport_iata="DEL", journey_type="ARRIVAL"
        )
    assert result["success"] is False
    assert result["reason"] == as_svc.REASON_AIRPORT_MISMATCH


def test_del_departure_dxb_rejected(monkeypatch):
    monkeypatch.setattr(as_svc.settings, "AVIATIONSTACK_API_KEY", "test-key")
    dxb_dep = {
        "flight_date": "2026-08-26",
        "flight_status": "scheduled",
        "departure": {"airport": "Dubai", "iata": "DXB", "city": "Dubai"},
        "arrival": {"airport": "Delhi", "iata": "DEL", "city": "New Delhi"},
        "airline": {"name": "Air India", "iata": "AI"},
        "flight": {"iata": "AI2424", "number": "2424"},
    }
    with patch("app.flight.aviationstack_service.httpx.Client") as client_cls:
        _http_client_patch(client_cls, {"data": [dxb_dep]})
        result = as_svc.verify_flight_for_whatsapp(
            "AI2424", selected_airport_iata="DEL", journey_type="DEPARTURE"
        )
    assert result["success"] is False
    assert result["reason"] == as_svc.REASON_AIRPORT_MISMATCH


def test_format_valid_string_still_requires_api(monkeypatch):
    monkeypatch.setattr(as_svc.settings, "AVIATIONSTACK_API_KEY", "test-key")
    with patch("app.flight.aviationstack_service.httpx.Client") as client_cls:
        _http_client_patch(client_cls, {"data": []})
        result = as_svc.verify_flight_for_whatsapp(
            "AI242", selected_airport_iata="DEL", journey_type="DEPARTURE"
        )
    assert result["success"] is False
    assert result["reason"] == as_svc.REASON_FLIGHT_NOT_FOUND


def test_ai242_and_ai2424_verified_independently(monkeypatch):
    monkeypatch.setattr(as_svc.settings, "AVIATIONSTACK_API_KEY", "test-key")
    ai242 = dict(SAMPLE_DEL_BOM)
    ai242["flight"] = {"iata": "AI242", "number": "242"}
    ai2424 = dict(SAMPLE_DEL_BOM)
    ai2424["flight"] = {"iata": "AI2424", "number": "2424"}

    with patch("app.flight.aviationstack_service.httpx.Client") as client_cls:
        client = MagicMock()
        client.__enter__.return_value = client
        client.__exit__.return_value = False
        client.get.side_effect = [
            _mock_http_json({"data": [ai242]}),
            _mock_http_json({"data": [ai2424]}),
        ]
        client_cls.return_value = client
        r1 = as_svc.verify_flight_for_whatsapp("AI242", selected_airport_iata="DEL", journey_type="DEPARTURE")
        r2 = as_svc.verify_flight_for_whatsapp("AI2424", selected_airport_iata="DEL", journey_type="DEPARTURE")

    assert r1["success"] is True and r1["flight"]["flight_number"] == "AI242"
    assert r2["success"] is True and r2["flight"]["flight_number"] == "AI2424"
    assert client.get.call_count == 2


def test_verified_international_uses_canonical_resolver(monkeypatch):
    from app.database import SessionLocal
    from app.services.service_airport_rules import derive_flight_type_from_route

    monkeypatch.setattr(as_svc.settings, "AVIATIONSTACK_API_KEY", "test-key")
    intl = {
        "flight_date": "2026-08-26",
        "flight_status": "scheduled",
        "departure": {"airport": "Dubai", "iata": "DXB", "city": "Dubai"},
        "arrival": {"airport": "Delhi", "iata": "DEL", "city": "New Delhi"},
        "airline": {"name": "Emirates", "iata": "EK"},
        "flight": {"iata": "EK511", "number": "511"},
    }
    with patch("app.flight.aviationstack_service.httpx.Client") as client_cls:
        _http_client_patch(client_cls, {"data": [intl]})
        result = as_svc.verify_flight_for_whatsapp(
            "EK511", selected_airport_iata="DEL", journey_type="ARRIVAL"
        )
    assert result["success"] is True
    db = SessionLocal()
    try:
        derived = derive_flight_type_from_route(
            db,
            result["flight"]["departure"]["iata"],
            result["flight"]["arrival"]["iata"],
            "ARRIVAL",
        )
        assert derived == "INTERNATIONAL"
    finally:
        db.close()


def test_verified_domestic_uses_canonical_resolver(monkeypatch):
    from app.database import SessionLocal
    from app.services.service_airport_rules import derive_flight_type_from_route

    monkeypatch.setattr(as_svc.settings, "AVIATIONSTACK_API_KEY", "test-key")
    with patch("app.flight.aviationstack_service.httpx.Client") as client_cls:
        _http_client_patch(client_cls, {"data": [SAMPLE_DEL_BOM]})
        result = as_svc.verify_flight_for_whatsapp(
            "6E224", selected_airport_iata="DEL", journey_type="DEPARTURE"
        )
    assert result["success"] is True
    db = SessionLocal()
    try:
        derived = derive_flight_type_from_route(
            db,
            result["flight"]["departure"]["iata"],
            result["flight"]["arrival"]["iata"],
            "DEPARTURE",
        )
        assert derived == "DOMESTIC"
    finally:
        db.close()


@patch("app.integrations.whatsapp.client.WhatsAppClient.send_text_message")
@patch("app.integrations.whatsapp.client.WhatsAppClient.send_interactive_buttons")
def test_unverified_flight_cannot_create_booking(mock_buttons, mock_text):
    from app.database import SessionLocal
    from app.integrations.whatsapp.service import WhatsAppBookingStateMachine
    import uuid

    mock_buttons.return_value = {"success": True}
    mock_text.return_value = {"success": True}
    db = SessionLocal()
    try:
        phone = f"91{uuid.uuid4().int % 10**10:010d}"
        conv, _ = WhatsAppBookingStateMachine.get_or_create_conversation(db, phone)
        conv.requires_flight = True
        conv.current_state = "BOOKING_REVIEW"
        conv.flight_num = "AI242"
        conv.flight_details_json = {
            "journey_type": "DEPARTURE",
            "verification_status": "not_verified",
            "unit_price": 1000,
        }
        conv.passenger_count = 1
        conv.customer_name = "Test User"
        conv.customer_email = "test.user@example.com"
        db.commit()

        res = WhatsAppBookingStateMachine._create_booking_request(db, conv)
        db.refresh(conv)
        assert res["status"] == "unverified_flight_blocked"
        assert conv.booking_ref is None
        assert conv.current_state == "FLIGHT_INPUT"
    finally:
        db.close()


@patch("app.integrations.whatsapp.client.WhatsAppClient.send_text_message")
@patch("app.integrations.whatsapp.client.WhatsAppClient.send_interactive_buttons")
@patch("app.flight.aviationstack_service.verify_flight_for_whatsapp")
def test_unknown_flight_asks_retry(mock_verify, mock_buttons, mock_text):
    from app.database import SessionLocal
    from app.integrations.whatsapp.service import WhatsAppBookingStateMachine
    import uuid

    mock_buttons.return_value = {"success": True}
    mock_text.return_value = {"success": True}
    mock_verify.return_value = {"success": False, "reason": as_svc.REASON_FLIGHT_NOT_FOUND, "flight": None}

    db = SessionLocal()
    try:
        phone = f"91{uuid.uuid4().int % 10**10:010d}"
        conv, _ = WhatsAppBookingStateMachine.get_or_create_conversation(db, phone)
        conv.selected_airport_iata = "DEL"
        conv.current_state = "FLIGHT_INPUT"
        conv.requires_flight = True
        conv.flight_details_json = {"journey_type": "DEPARTURE"}
        db.commit()
        res = WhatsAppBookingStateMachine.process_incoming_event(db, phone, "AI242")
        db.refresh(conv)
        assert res["success"] is False
        assert res["status"] == "flight_not_found"
        assert conv.current_state == "FLIGHT_INPUT"
        assert conv.flight_num is None
        body = mock_buttons.call_args.kwargs.get("body_text") or ""
        assert "could not verify this flight number" in body.lower()
    finally:
        db.close()


@patch("app.integrations.whatsapp.client.WhatsAppClient.send_text_message")
@patch("app.integrations.whatsapp.client.WhatsAppClient.send_interactive_buttons")
@patch("app.flight.aviationstack_service.verify_flight_for_whatsapp")
def test_whatsapp_timeout_not_silently_accepted(mock_verify, mock_buttons, mock_text):
    from app.database import SessionLocal
    from app.integrations.whatsapp.service import WhatsAppBookingStateMachine
    import uuid

    mock_buttons.return_value = {"success": True}
    mock_text.return_value = {"success": True}
    mock_verify.return_value = {"success": False, "reason": as_svc.REASON_TIMEOUT, "flight": None}

    db = SessionLocal()
    try:
        phone = f"91{uuid.uuid4().int % 10**10:010d}"
        conv, _ = WhatsAppBookingStateMachine.get_or_create_conversation(db, phone)
        conv.selected_airport_iata = "DEL"
        conv.current_state = "FLIGHT_INPUT"
        conv.requires_flight = True
        conv.flight_details_json = {"journey_type": "DEPARTURE"}
        db.commit()
        res = WhatsAppBookingStateMachine.process_incoming_event(db, phone, "AI2424")
        db.refresh(conv)
        assert res["success"] is False
        assert res["status"] == "flight_verify_timeout"
        assert conv.flight_num is None
        assert conv.current_state == "FLIGHT_INPUT"
    finally:
        db.close()

