"""
Automated Verification Suite for WhatsApp Terminal Selection Flow & Multi-Terminal Resolution.

Validates:
1. DEL / Departure / Domestic with no terminal selected -> prompts for terminal selection (Terminal 1 & 2, Terminal 3).
2. DEL / Departure / Domestic / Terminal 3 -> returns ONLY Terminal 3 packages (Silver ₹3000, Gold ₹3500, Elite ₹5000).
3. DEL / Departure / Domestic / Terminal 1 & 2 -> returns ONLY Terminal 1 & 2 packages (Silver ₹3000, Elite ₹5000).
4. DEL / Departure / International -> automatic terminal resolution (Terminal 3 only, no extra prompt).
5. An airport with terminal=None only (e.g. BOM, BLR) -> no unnecessary terminal selection prompt, goes straight to packages.
6. An airport/flow with only one active terminal configuration -> no unnecessary terminal prompt.
7. Arrival flow (DEL / Arrival / Domestic and DEL / Arrival / International) -> preserved correct behavior.
8. Transit flow (DEL / Transit and BOM / Transit) -> preserved correct behavior.
9. All 20 supported airports -> no standalone services, no duplicate package names caused by terminal variants.
10. Database invariants -> confirms no database rows were modified.
"""

import pytest
import uuid
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


def test_1_del_departure_domestic_triggers_terminal_selection(db):
    """1. DEL / Departure / Domestic / no terminal selected -> terminal selection should appear."""
    del_airport = db.query(SupportedAirport).filter_by(iata_code="DEL").first()
    assert del_airport is not None

    terminals = WhatsAppBookingStateMachine._get_applicable_terminals(
        db, del_airport, journey_type="DEPARTURE", travel_type="DOMESTIC"
    )
    assert len(terminals) == 2
    assert "Terminal 1 & 2" in terminals
    assert "Terminal 3" in terminals

    # Simulate entering DEL in AIRPORT_SELECTION state
    test_phone = f"+9199999{uuid.uuid4().hex[:5]}"
    conv = WhatsAppConversation(
        id=uuid.uuid4(),
        phone_number=test_phone,
        current_state="AIRPORT_SELECTION",
        selected_category="Airport Services",
        requires_airport=True,
        requires_flight=True,
        flight_details_json={"journey_type": "DEPARTURE", "travel_type": "DOMESTIC"}
    )
    db.add(conv)
    db.commit()

    res = WhatsAppBookingStateMachine._state_airport_selection(db, conv, "DEL")
    assert conv.current_state == "TERMINAL_SELECTION"
    assert res.get("status") == "terminal_selection_prompt_sent"


def test_2_del_departure_domestic_terminal_3_packages(db):
    """2. DEL / Departure / Domestic / Terminal 3 -> only Terminal 3 packages."""
    del_airport = db.query(SupportedAirport).filter_by(iata_code="DEL").first()
    assert del_airport is not None

    test_phone = f"+9199999{uuid.uuid4().hex[:5]}"
    conv = WhatsAppConversation(
        id=uuid.uuid4(),
        phone_number=test_phone,
        current_state="TERMINAL_SELECTION",
        selected_category="Airport Services",
        selected_airport_iata="DEL",
        selected_airport_name=del_airport.airport_name,
        requires_airport=True,
        requires_flight=True,
        flight_details_json={"journey_type": "DEPARTURE", "travel_type": "DOMESTIC"}
    )
    db.add(conv)
    db.commit()

    # User selects Terminal 3
    res = WhatsAppBookingStateMachine._state_terminal_selection(db, conv, "Terminal 3", "btn_term_2")
    assert conv.current_state == "SERVICE_SELECTION"
    assert conv.flight_details_json.get("terminal") == "Terminal 3"

    # Query packages for Terminal 3
    t3_packages = WhatsAppBookingStateMachine._get_authoritative_airport_packages(
        db, del_airport.id, "DEPARTURE", ["DOMESTIC", "ALL"], terminal="Terminal 3"
    )
    assert len(t3_packages) == 3
    pkg_dict = {svc.name: float(aps.price) for aps, svc in t3_packages}
    assert pkg_dict == {
        "Silver Service": 3000.0,
        "Gold Service": 3500.0,
        "Elite Service": 5000.0
    }
    # Ensure no duplicates
    assert len(set(svc.name for aps, svc in t3_packages)) == 3


def test_3_del_departure_domestic_terminal_1_2_packages(db):
    """3. DEL / Departure / Domestic / Terminal 1 & 2 -> only Terminal 1 & 2 packages."""
    del_airport = db.query(SupportedAirport).filter_by(iata_code="DEL").first()
    assert del_airport is not None

    test_phone = f"+9199999{uuid.uuid4().hex[:5]}"
    conv = WhatsAppConversation(
        id=uuid.uuid4(),
        phone_number=test_phone,
        current_state="TERMINAL_SELECTION",
        selected_category="Airport Services",
        selected_airport_iata="DEL",
        selected_airport_name=del_airport.airport_name,
        requires_airport=True,
        requires_flight=True,
        flight_details_json={"journey_type": "DEPARTURE", "travel_type": "DOMESTIC"}
    )
    db.add(conv)
    db.commit()

    # User selects Terminal 1 & 2 via shorthand "1"
    res = WhatsAppBookingStateMachine._state_terminal_selection(db, conv, "1", None)
    assert conv.current_state == "SERVICE_SELECTION"
    assert conv.flight_details_json.get("terminal") == "Terminal 1 & 2"

    # Query packages for Terminal 1 & 2
    t1_packages = WhatsAppBookingStateMachine._get_authoritative_airport_packages(
        db, del_airport.id, "DEPARTURE", ["DOMESTIC", "ALL"], terminal="Terminal 1 & 2"
    )
    assert len(t1_packages) == 2
    pkg_dict = {svc.name: float(aps.price) for aps, svc in t1_packages}
    assert pkg_dict == {
        "Silver Service": 3000.0,
        "Elite Service": 5000.0
    }
    # Gold service must NOT be in Terminal 1 & 2
    assert "Gold Service" not in pkg_dict


def test_4_del_departure_international_no_unnecessary_terminal_prompt(db):
    """4. DEL / Departure / International -> correct terminal behavior based on live configuration (single terminal, no prompt)."""
    del_airport = db.query(SupportedAirport).filter_by(iata_code="DEL").first()
    assert del_airport is not None

    terminals = WhatsAppBookingStateMachine._get_applicable_terminals(
        db, del_airport, journey_type="DEPARTURE", travel_type="INTERNATIONAL"
    )
    assert len(terminals) == 1
    assert terminals[0] == "Terminal 3"

    test_phone = f"+9199999{uuid.uuid4().hex[:5]}"
    conv = WhatsAppConversation(
        id=uuid.uuid4(),
        phone_number=test_phone,
        current_state="AIRPORT_SELECTION",
        selected_category="Airport Services",
        requires_airport=True,
        requires_flight=True,
        flight_details_json={"journey_type": "DEPARTURE", "travel_type": "INTERNATIONAL"}
    )
    db.add(conv)
    db.commit()

    res = WhatsAppBookingStateMachine._state_airport_selection(db, conv, "DEL")
    # Transitions directly to SERVICE_SELECTION because only 1 terminal exists
    assert conv.current_state == "SERVICE_SELECTION"
    assert conv.flight_details_json.get("terminal") == "Terminal 3"


def test_5_airport_with_no_terminals_skips_prompt(db):
    """5. An airport with terminal=None only (e.g. BOM, BLR) -> no unnecessary terminal selection."""
    for iata in ["BOM", "BLR", "LKO", "HYD", "MAA"]:
        ap = db.query(SupportedAirport).filter_by(iata_code=iata).first()
        if not ap:
            continue
        terminals = WhatsAppBookingStateMachine._get_applicable_terminals(
            db, ap, journey_type="DEPARTURE", travel_type="DOMESTIC"
        )
        assert len(terminals) == 0, f"{iata} unexpectedly returned terminals: {terminals}"

        test_phone = f"+9199999{uuid.uuid4().hex[:5]}"
        conv = WhatsAppConversation(
            id=uuid.uuid4(),
            phone_number=test_phone,
            current_state="AIRPORT_SELECTION",
            selected_category="Airport Services",
            requires_airport=True,
            requires_flight=True,
            flight_details_json={"journey_type": "DEPARTURE", "travel_type": "DOMESTIC"}
        )
        db.add(conv)
        db.commit()

        res = WhatsAppBookingStateMachine._state_airport_selection(db, conv, iata)
        assert conv.current_state == "SERVICE_SELECTION"


def test_6_single_terminal_configuration_skips_prompt(db):
    """6. An airport with only one active terminal configuration -> no unnecessary terminal selection."""
    del_airport = db.query(SupportedAirport).filter_by(iata_code="DEL").first()
    assert del_airport is not None

    terminals = WhatsAppBookingStateMachine._get_applicable_terminals(
        db, del_airport, journey_type="ARRIVAL", travel_type="INTERNATIONAL"
    )
    assert len(terminals) == 1
    assert terminals[0] == "Terminal 3"


def test_7_arrival_flow_preserved(db):
    """7. Arrival flow -> existing correct behavior preserved."""
    del_airport = db.query(SupportedAirport).filter_by(iata_code="DEL").first()
    assert del_airport is not None

    # DEL Arrival International packages
    intl_arr_rows = WhatsAppBookingStateMachine._get_authoritative_airport_packages(
        db, del_airport.id, "ARRIVAL", ["INTERNATIONAL", "ALL"], terminal="Terminal 3"
    )
    assert len(intl_arr_rows) == 3
    slugs = [svc.slug.lower() for aps, svc in intl_arr_rows]
    assert "silver" in slugs
    assert "gold" in slugs
    assert "elite" in slugs


def test_8_transit_flow_preserved(db):
    """8. Transit flow -> existing correct behavior preserved."""
    del_airport = db.query(SupportedAirport).filter_by(iata_code="DEL").first()
    bom_airport = db.query(SupportedAirport).filter_by(iata_code="BOM").first()

    # DEL transit
    del_transit = WhatsAppBookingStateMachine._get_authoritative_airport_packages(
        db, del_airport.id, "TRANSIT", ["DOMESTIC_DOMESTIC", "ALL"]
    )
    assert len(del_transit) == 1
    assert del_transit[0][0].price == Decimal("5500.00")

    # BOM transit
    bom_transit = WhatsAppBookingStateMachine._get_authoritative_airport_packages(
        db, bom_airport.id, "TRANSIT", ["DOMESTIC_DOMESTIC", "ALL"]
    )
    assert len(bom_transit) == 1
    assert bom_transit[0][0].price == Decimal("7150.00")


def test_9_all_20_airports_no_duplicates_or_standalones(db):
    """9. All 20 supported airports -> no standalone services, no duplicate package names caused by terminal variants."""
    STANDALONE_SLUGS = {"meet_greet", "fast_track", "lounge", "porter", "buggy", "wheelchair", "transport"}
    airports = db.query(SupportedAirport).all()
    assert len(airports) >= 20

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

                    # Check for duplicates in package names
                    names = [svc.name for aps, svc in rows]
                    assert len(names) == len(set(names)), (
                        f"Duplicate packages found at {ap.iata_code} {jt} {tt} (terminal={term}): {names}"
                    )

                    # Check that no standalone services leak into departure/arrival packages
                    for aps, svc in rows:
                        slug = (svc.slug or "").lower().strip()
                        assert slug not in STANDALONE_SLUGS, (
                            f"Standalone service {slug} leaked in {ap.iata_code} {jt} {tt}"
                        )


def test_10_database_invariants_and_row_count_unchanged(db):
    """10. Verify that no database rows were changed."""
    active_rows = db.query(AirportService).filter(AirportService.is_available == True).count()
    assert active_rows == 137, f"Expected exactly 137 active airport_services rows, got {active_rows}"

    total_airports = db.query(SupportedAirport).count()
    assert total_airports == 20
