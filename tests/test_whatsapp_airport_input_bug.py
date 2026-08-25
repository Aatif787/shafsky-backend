import pytest
from unittest.mock import patch, MagicMock
from app.models.whatsapp_models import WhatsAppConversation, WhatsAppMessage
from app.models.journey_models import SupportedAirport, AirportService, Service
from app.integrations.whatsapp.service import WhatsAppBookingStateMachine
import uuid

@pytest.fixture
def mock_db():
    db = MagicMock()
    return db

@pytest.fixture
def mock_client():
    with patch("app.integrations.whatsapp.service.whatsapp_client") as client:
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

# 1, 2, 3: Test inputs DEL, Delhi, Indira Gandhi, Lucknow, LKO, etc.
@pytest.mark.parametrize("input_val", [
    "DEL", "Delhi", "Indira Gandhi International Airport",
    "LKO", "Lucknow", "Chaudhary Charan Singh International Airport",
    "BOM", "Mumbai", "HYD", "Hyderabad",
    "CCU", "Kolkata", "AMD", "Ahmedabad",
    "MAA", "Chennai", "BLR", "Bangalore", "Jaipur"
])
def test_airport_input_success_with_packages(mock_db, mock_client, input_val):
    conv = _create_conv()
    airport = _mock_airport()
    
    def fake_execute(stmt):
        m = MagicMock()
        m.scalars.return_value.first.return_value = airport
        m.scalar_one_or_none.return_value = airport
        m.all.return_value = [_mock_service_and_package()]
        return m
    
    mock_db.execute.side_effect = fake_execute
    
    with patch("app.integrations.whatsapp.service.WhatsAppBookingStateMachine.get_or_create_conversation", return_value=(conv, False)):
        res = WhatsAppBookingStateMachine.process_incoming_event(mock_db, conv.phone_number, input_val, "text")
        
        assert res["status"] == "services_menu_sent"
        assert conv.current_state == "SERVICE_SELECTION"
        assert conv.selected_airport_iata == "DEL"
        mock_client.send_interactive_list.assert_called_once()
        mock_db.commit.assert_called()

# 4. Unsupported airport
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
        assert conv.current_state == "AIRPORT_SELECTION" # State preserved
        mock_client.send_text_message.assert_called_once()
        assert "unavailable" in mock_client.send_text_message.call_args[0][1]

# 6. Airport with no configured package (returns zero)
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
        assert conv.current_state == "SERVICE_SELECTION" # Transitions, then says no packages
        mock_client.send_interactive_buttons.assert_called_once()
        assert "no *Domestic Departure* services available" in mock_client.send_interactive_buttons.call_args[1]["body_text"]

# 7-14. Various Travel/Transit Types
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
def test_airport_various_journey_types(mock_db, mock_client, jt, tt, expected_jt, expected_tt):
    conv = _create_conv(jt=jt, tt=tt)
    airport = _mock_airport()
    
    def fake_execute(stmt):
        m = MagicMock()
        m.scalars.return_value.first.return_value = airport
        m.scalar_one_or_none.return_value = airport
        m.all.return_value = [_mock_service_and_package()]
        return m
    mock_db.execute.side_effect = fake_execute
    
    with patch("app.integrations.whatsapp.service.WhatsAppBookingStateMachine.get_or_create_conversation", return_value=(conv, False)):
        res = WhatsAppBookingStateMachine.process_incoming_event(mock_db, conv.phone_number, "DEL", "text")
        
        assert res["status"] == "services_menu_sent"
        assert conv.current_state == "SERVICE_SELECTION"
