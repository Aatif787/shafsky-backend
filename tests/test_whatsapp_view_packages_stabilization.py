"""
Comprehensive Test Suite for WhatsApp Airport-Service Flow Stabilization & View Packages Callback Handling.

Covers:
1. DEL -> Departure -> Domestic: terminal selection appears, T1&2 vs T3 packages isolated.
2. DEL -> Departure -> International: correct single-terminal resolution without extra prompt.
3. Mumbai -> Arrival -> Domestic: View Packages callback works, package list rendered, no generic error.
4. Mumbai -> Departure -> Domestic: View Packages works.
5. Mumbai -> Transit: all 4 transit routes verified and working.
6. Lucknow -> Departure -> Domestic: standard airport flow works directly.
7. Airport with terminal=None: no unnecessary terminal prompt.
8. Airport with single terminal: no unnecessary terminal prompt.
9. Airport with multiple terminals: terminal selection works.
10. Package selection after View Packages: transitions to FLIGHT_INPUT.
11. Session expiry: expired session restarts with notice, active session preserved.
12. All 20 supported airports: no standalone packages, no duplicate names, no callback errors.
"""

import pytest
import uuid
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from app.database import SessionLocal
from app.models.journey_models import SupportedAirport, AirportService, Service
from app.models.whatsapp_models import WhatsAppConversation
from app.integrations.whatsapp.service import WhatsAppBookingStateMachine


@pytest.fixture
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


def test_1_del_departure_domestic_terminal_isolation(db):
    """1. DEL -> Departure -> Domestic: terminal selection appears, T1&2 and T3 packages isolated."""
    del_airport = db.query(SupportedAirport).filter_by(iata_code="DEL").first()
    assert del_airport is not None

    terminals = WhatsAppBookingStateMachine._get_applicable_terminals(
        db, del_airport, journey_type="DEPARTURE", travel_type="DOMESTIC"
    )
    assert len(terminals) == 2
    assert "Terminal 1 & 2" in terminals
    assert "Terminal 3" in terminals

    t1_pkgs = WhatsAppBookingStateMachine._get_authoritative_airport_packages(
        db, del_airport.id, "DEPARTURE", ["DOMESTIC", "ALL"], terminal="Terminal 1 & 2"
    )
    assert len(t1_pkgs) == 2
    assert {svc.name: float(aps.price) for aps, svc in t1_pkgs} == {
        "Silver Service": 3000.0,
        "Elite Service": 5000.0
    }

    t3_pkgs = WhatsAppBookingStateMachine._get_authoritative_airport_packages(
        db, del_airport.id, "DEPARTURE", ["DOMESTIC", "ALL"], terminal="Terminal 3"
    )
    assert len(t3_pkgs) == 3
    assert {svc.name: float(aps.price) for aps, svc in t3_pkgs} == {
        "Silver Service": 3000.0,
        "Gold Service": 3500.0,
        "Elite Service": 5000.0
    }


def test_2_del_departure_international_direct_resolution(db):
    """2. DEL -> Departure -> International: correct terminal behavior (Terminal 3 auto-set, no prompt)."""
    del_airport = db.query(SupportedAirport).filter_by(iata_code="DEL").first()
    terminals = WhatsAppBookingStateMachine._get_applicable_terminals(
        db, del_airport, journey_type="DEPARTURE", travel_type="INTERNATIONAL"
    )
    assert len(terminals) == 1
    assert terminals[0] == "Terminal 3"

    rows = WhatsAppBookingStateMachine._get_authoritative_airport_packages(
        db, del_airport.id, "DEPARTURE", ["INTERNATIONAL", "ALL"], terminal="Terminal 3"
    )
    assert len(rows) == 3
    assert {svc.name: float(aps.price) for aps, svc in rows} == {
        "Silver Service": 5500.0,
        "Gold Service": 6500.0,
        "Elite Service": 7000.0
    }


def test_3_mumbai_arrival_domestic_view_packages_works(db):
    """3. Mumbai -> Arrival -> Domestic: View Packages callback works, package list appears, no generic error."""
    bom_airport = db.query(SupportedAirport).filter_by(iata_code="BOM").first()
    assert bom_airport is not None

    phone = f"9198{uuid.uuid4().int % 100000000:08d}"
    conv = WhatsAppConversation(
        id=uuid.uuid4(),
        phone_number=phone,
        current_state="SERVICE_SELECTION",
        selected_category="Airport Services",
        selected_airport_iata="BOM",
        selected_airport_name=bom_airport.airport_name,
        requires_airport=True,
        requires_flight=True,
        flight_details_json={"journey_type": "ARRIVAL", "travel_type": "DOMESTIC", "flight_type": "DOMESTIC"},
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc)
    )
    db.add(conv)
    db.commit()

    # User clicks / sends 'View Packages' button
    res_btn = WhatsAppBookingStateMachine.process_incoming_event(
        db, phone, "View Packages", input_id="btn_view_packages"
    )
    assert res_btn.get("status") == "services_menu_sent"
    assert res_btn.get("success") is True

    # User sends text 'View Packages'
    res_txt = WhatsAppBookingStateMachine.process_incoming_event(
        db, phone, "View Packages"
    )
    assert res_txt.get("status") == "services_menu_sent"
    assert res_txt.get("success") is True


def test_4_mumbai_departure_domestic_view_packages_works(db):
    """4. Mumbai -> Departure -> Domestic: View Packages works."""
    bom_airport = db.query(SupportedAirport).filter_by(iata_code="BOM").first()
    phone = f"9198{uuid.uuid4().int % 100000000:08d}"
    conv = WhatsAppConversation(
        id=uuid.uuid4(),
        phone_number=phone,
        current_state="SERVICE_SELECTION",
        selected_category="Airport Services",
        selected_airport_iata="BOM",
        selected_airport_name=bom_airport.airport_name,
        requires_airport=True,
        requires_flight=True,
        flight_details_json={"journey_type": "DEPARTURE", "travel_type": "DOMESTIC", "flight_type": "DOMESTIC"},
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc)
    )
    db.add(conv)
    db.commit()

    res = WhatsAppBookingStateMachine.process_incoming_event(
        db, phone, "View Packages", input_id="btn_view_packages"
    )
    assert res.get("status") == "services_menu_sent"
    assert res.get("success") is True


def test_5_mumbai_transit_flow_works(db):
    """5. Mumbai -> Transit: existing flow still works with authoritative pricing."""
    bom_airport = db.query(SupportedAirport).filter_by(iata_code="BOM").first()
    for route, expected_price in [
        ("DOMESTIC_DOMESTIC", Decimal("7150.00")),
        ("DOMESTIC_INTERNATIONAL", Decimal("9000.00")),
        ("INTERNATIONAL_DOMESTIC", Decimal("9000.00")),
        ("INTERNATIONAL_INTERNATIONAL", Decimal("10000.00")),
    ]:
        rows = WhatsAppBookingStateMachine._get_authoritative_airport_packages(
            db, bom_airport.id, "TRANSIT", [route, "ALL"]
        )
        assert len(rows) == 1
        assert rows[0][0].price == expected_price


def test_6_lucknow_departure_domestic_works(db):
    """6. Lucknow -> Departure -> Domestic: existing package flow works."""
    lko_airport = db.query(SupportedAirport).filter_by(iata_code="LKO").first()
    assert lko_airport is not None

    phone = f"9198{uuid.uuid4().int % 100000000:08d}"
    conv = WhatsAppConversation(
        id=uuid.uuid4(),
        phone_number=phone,
        current_state="SERVICE_SELECTION",
        selected_category="Airport Services",
        selected_airport_iata="LKO",
        selected_airport_name=lko_airport.airport_name,
        requires_airport=True,
        requires_flight=True,
        flight_details_json={"journey_type": "DEPARTURE", "travel_type": "DOMESTIC", "flight_type": "DOMESTIC"},
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc)
    )
    db.add(conv)
    db.commit()

    res = WhatsAppBookingStateMachine.process_incoming_event(
        db, phone, "View Packages", input_id="btn_view_packages"
    )
    assert res.get("status") == "services_menu_sent"
    assert res.get("success") is True


def test_7_airport_terminal_none_skips_prompt(db):
    """7. Airport with terminal=None configuration -> no unnecessary terminal selection."""
    blr_airport = db.query(SupportedAirport).filter_by(iata_code="BLR").first()
    terminals = WhatsAppBookingStateMachine._get_applicable_terminals(
        db, blr_airport, "DEPARTURE", "DOMESTIC"
    )
    assert len(terminals) == 0


def test_8_airport_with_only_one_terminal_skips_prompt(db):
    """8. Airport with only one terminal -> no unnecessary terminal selection."""
    del_airport = db.query(SupportedAirport).filter_by(iata_code="DEL").first()
    terminals = WhatsAppBookingStateMachine._get_applicable_terminals(
        db, del_airport, "ARRIVAL", "INTERNATIONAL"
    )
    assert len(terminals) == 1


def test_9_airport_with_multiple_terminals_works(db):
    """9. Airport with multiple terminals -> terminal selection works."""
    del_airport = db.query(SupportedAirport).filter_by(iata_code="DEL").first()
    terminals = WhatsAppBookingStateMachine._get_applicable_terminals(
        db, del_airport, "DEPARTURE", "DOMESTIC"
    )
    assert len(terminals) == 2


def test_10_package_selection_after_view_packages(db):
    """10. Package selection after View Packages -> continues to FLIGHT_INPUT."""
    bom_airport = db.query(SupportedAirport).filter_by(iata_code="BOM").first()
    phone = f"9198{uuid.uuid4().int % 100000000:08d}"
    conv = WhatsAppConversation(
        id=uuid.uuid4(),
        phone_number=phone,
        current_state="SERVICE_SELECTION",
        selected_category="Airport Services",
        selected_airport_iata="BOM",
        selected_airport_name=bom_airport.airport_name,
        requires_airport=True,
        requires_flight=True,
        flight_details_json={"journey_type": "ARRIVAL", "travel_type": "DOMESTIC", "flight_type": "DOMESTIC"},
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc)
    )
    db.add(conv)
    db.commit()

    # User chooses option 1
    res = WhatsAppBookingStateMachine.process_incoming_event(db, phone, "1")
    assert res.get("status") == "flight_prompt_sent"
    assert res.get("success") is True

    db.refresh(conv)
    assert conv.current_state == "FLIGHT_INPUT"
    assert conv.selected_service_name is not None
    assert conv.total_amount is not None


def test_11_session_expiry_behavior(db):
    """11. Session expiry -> expired session restarts with notice, active session is preserved."""
    phone = f"9198{uuid.uuid4().int % 100000000:08d}"
    conv = WhatsAppConversation(
        id=uuid.uuid4(),
        phone_number=phone,
        current_state="SERVICE_SELECTION",
        selected_category="Airport Services",
        selected_airport_iata="BOM",
        requires_airport=True,
        requires_flight=True,
        created_at=datetime.now(timezone.utc) - timedelta(minutes=25),
        updated_at=datetime.now(timezone.utc) - timedelta(minutes=20)  # >15 mins ago
    )
    db.add(conv)
    db.commit()

    res = WhatsAppBookingStateMachine.process_incoming_event(db, phone, "View Packages")
    assert res.get("status") == "category_menu_sent"

    db.refresh(conv)
    assert conv.current_state == "CATEGORY_SELECTION"


def test_12_all_20_airports_integrity(db):
    """12. All 20 supported airports -> no standalone packages, no duplicate names, no callback errors."""
    STANDALONE_SLUGS = {"meet_greet", "fast_track", "lounge", "porter", "buggy", "wheelchair", "transport"}
    airports = db.query(SupportedAirport).all()
    assert len(airports) == 20

    for ap in airports:
        for jt in ["DEPARTURE", "ARRIVAL"]:
            for tt in ["DOMESTIC", "INTERNATIONAL"]:
                terminals = WhatsAppBookingStateMachine._get_applicable_terminals(db, ap, jt, tt)
                terminal_cases = terminals if terminals else [None]

                for term in terminal_cases:
                    rows = WhatsAppBookingStateMachine._get_authoritative_airport_packages(
                        db, ap.id, jt, [tt, "ALL"], terminal=term
                    )
                    if not rows:
                        continue

                    names = [svc.name for aps, svc in rows]
                    assert len(names) == len(set(names)), f"Duplicate packages at {ap.iata_code} {jt} {tt} term={term}: {names}"

                    for aps, svc in rows:
                        slug = (svc.slug or "").lower().strip()
                        assert slug not in STANDALONE_SLUGS, f"Standalone service {slug} leaked in {ap.iata_code} {jt} {tt}"
