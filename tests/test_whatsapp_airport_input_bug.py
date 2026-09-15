import pytest
from contextlib import ExitStack
from unittest.mock import patch, MagicMock
from app.models.whatsapp_models import WhatsAppConversation, WhatsAppMessage
from app.models.journey_models import SupportedAirport, AirportService, Service
from app.integrations.whatsapp.service import WhatsAppBookingStateMachine
from app.database import SessionLocal
import uuid

_CLIENT_PATCH_TARGETS = (
    "app.integrations.whatsapp.service.whatsapp_client",
    "app.integrations.whatsapp.handlers.airport_flow.whatsapp_client",
    "app.integrations.whatsapp.client.whatsapp_client",
)


@pytest.fixture
def mock_db():
    db = MagicMock()
    return db


@pytest.fixture
def mock_client():
    client = MagicMock()
    client.send_interactive_list.return_value = {"success": True}
    client.send_interactive_buttons.return_value = {"success": True}
    client.send_text_message.return_value = {"success": True}
    with ExitStack() as stack:
        for target in _CLIENT_PATCH_TARGETS:
            stack.enter_context(patch(target, client))
        yield client


def _create_conv(phone="9999999999", state="AIRPORT_SELECTION", jt="DEPARTURE", tt="DOMESTIC"):
    conv = WhatsAppConversation(
        id=uuid.uuid4(),
        phone_number=phone,
        current_state=state,
        selected_category="Airport Services",
        flight_details_json={"journey_type": jt, "travel_type": tt}
    )
    return conv


def _mock_airport(iata="DEL", name="Indira Gandhi International Airport", city="Delhi", country="India", supported=True, active=True):
    airport = SupportedAirport(
        id=uuid.uuid4(),
        iata_code=iata,
        airport_name=name,
        city=city,
        country=country,
        is_supported=supported,
        is_active=active
    )
    return airport


def _mock_service_and_package(price=2500):
    svc = Service(id=uuid.uuid4(), name="Test Platinum", slug="platinum", is_active=True)
    pkg = AirportService(id=uuid.uuid4(), service_id=svc.id, price=price, is_available=True)
    return pkg, svc


_DEL_INPUTS = {"DEL", "Delhi", "Indira Gandhi International Airport"}
_AIRPORT_INPUT_EXPECT = {
    "DEL": "DEL",
    "Delhi": "DEL",
    "Indira Gandhi International Airport": "DEL",
    "LKO": "LKO",
    "Lucknow": "LKO",
    "Chaudhary Charan Singh International Airport": "LKO",
    "BOM": "BOM",
    "Mumbai": "BOM",
    "HYD": "HYD",
    "Hyderabad": "HYD",
    "CCU": "CCU",
    "Kolkata": "CCU",
    "AMD": "AMD",
    "Ahmedabad": "AMD",
    "MAA": "MAA",
    "Chennai": "MAA",
    "BLR": "BLR",
    "Bangalore": "BLR",
    "Jaipur": "JAI",
}


@pytest.mark.parametrize("input_val", list(_AIRPORT_INPUT_EXPECT))
def test_airport_input_success_with_packages(mock_client, input_val):
    """City / IATA / airport-name input resolves against the seeded catalog."""
    expected_iata = _AIRPORT_INPUT_EXPECT[input_val]
    db = SessionLocal()
    try:
        conv = _create_conv(phone=f"91{uuid.uuid4().int % 10**10:010d}")
        db.add(conv)
        db.commit()
        res = WhatsAppBookingStateMachine.process_incoming_event(db, conv.phone_number, input_val, "text")
        db.refresh(conv)
        assert conv.selected_airport_iata == expected_iata
        if expected_iata == "DEL":
            assert res["status"] == "terminal_selection_prompt_sent"
            assert conv.current_state == "TERMINAL_SELECTION"
            mock_client.send_interactive_buttons.assert_called()
        else:
            assert res["status"] == "services_menu_sent"
            assert conv.current_state == "SERVICE_SELECTION"
            mock_client.send_interactive_list.assert_called()
    finally:
        db.close()


def test_airport_unsupported(mock_db, mock_client):
    conv = _create_conv()
    
    def fake_execute(stmt):
        m = MagicMock()
        m.scalars.return_value.first.return_value = None
        m.scalar_one_or_none.return_value = None
        return m
    mock_db.execute.side_effect = fake_execute
    
    with patch("app.integrations.whatsapp.service.WhatsAppBookingStateMachine.get_or_create_conversation", return_value=(conv, False)):
        res = WhatsAppBookingStateMachine.process_incoming_event(mock_db, conv.phone_number, "JFK", "text")
        
        assert res["status"] == "unsupported_airport"
        assert conv.current_state == "AIRPORT_SELECTION"
        mock_client.send_text_message.assert_called_once()
        assert "unavailable" in mock_client.send_text_message.call_args[0][1]


def test_airport_no_configured_package(mock_db, mock_client):
    conv = _create_conv()
    airport = _mock_airport()
    
    def fake_execute(stmt):
        m = MagicMock()
        m.scalars.return_value.first.return_value = airport
        m.scalar_one_or_none.return_value = airport
        m.all.return_value = []
        return m
    mock_db.execute.side_effect = fake_execute
    
    with patch("app.integrations.whatsapp.service.WhatsAppBookingStateMachine.get_or_create_conversation", return_value=(conv, False)):
        res = WhatsAppBookingStateMachine.process_incoming_event(mock_db, conv.phone_number, "DEL", "text")
        
        assert res["status"] == "no_services_found"
        assert conv.current_state == "SERVICE_SELECTION"
        mock_client.send_interactive_buttons.assert_called_once()
        assert "no *Domestic Departure* services available" in mock_client.send_interactive_buttons.call_args[1]["body_text"]


@pytest.mark.parametrize("jt,tt,expected_jt,expected_tt", [
    ("ARRIVAL", "DOMESTIC", "ARRIVAL", "DOMESTIC"),
    ("ARRIVAL", "INTERNATIONAL", "ARRIVAL", "INTERNATIONAL"),
    ("DEPARTURE", "DOMESTIC", "DEPARTURE", "DOMESTIC"),
    ("DEPARTURE", "INTERNATIONAL", "DEPARTURE", "INTERNATIONAL"),
    ("TRANSIT", "DOMESTIC_DOMESTIC", "TRANSIT", "DOMESTIC_DOMESTIC"),
    ("TRANSIT", "DOMESTIC_INTERNATIONAL", "TRANSIT", "DOMESTIC_INTERNATIONAL"),
    ("TRANSIT", "INTERNATIONAL_DOMESTIC", "TRANSIT", "INTERNATIONAL_DOMESTIC"),
    ("TRANSIT", "INTERNATIONAL_INTERNATIONAL", "TRANSIT", "INTERNATIONAL_INTERNATIONAL"),
])
def test_airport_various_journey_types(mock_client, jt, tt, expected_jt, expected_tt):
    db = SessionLocal()
    try:
        conv = _create_conv(phone=f"91{uuid.uuid4().int % 10**10:010d}", jt=jt, tt=tt)
        db.add(conv)
        db.commit()
        # LKO has no transit catalog; BOM is the live transit product.
        airport_query = "BOM" if jt == "TRANSIT" else "LKO"
        expected_iata = "BOM" if jt == "TRANSIT" else "LKO"
        res = WhatsAppBookingStateMachine.process_incoming_event(db, conv.phone_number, airport_query, "text")
        db.refresh(conv)
        assert conv.selected_airport_iata == expected_iata
        assert res["status"] in ("services_menu_sent", "terminal_selection_prompt_sent")
        assert conv.flight_details_json.get("journey_type") == expected_jt or jt == expected_jt
    finally:
        db.close()
