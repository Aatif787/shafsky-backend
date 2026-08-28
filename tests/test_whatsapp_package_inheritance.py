"""
Comprehensive unit & integration tests for WhatsApp Package Inheritance Display.

Validates:
1. Dynamic inheritance logic for Silver + Gold + Elite
2. Dynamic inheritance logic for Silver + Elite (missing Gold)
3. Dynamic inheritance logic for Gold + Elite (missing Silver)
4. Single tier display (no inheritance line)
5. Zero inclusion repetition across tiers
6. Level 2 package details presentation
7. Database seeded airport packages across multiple hubs and journey types
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from app.integrations.whatsapp import copy as wa_copy
from app.database import SessionLocal
from app.integrations.whatsapp.service import WhatsAppBookingStateMachine
from app.models.journey_models import SupportedAirport


def test_three_tier_inheritance_display():
    """Test 3-tier inheritance: Silver, Gold, Elite."""
    silver_features = [
        "WELCOME GUEST FROM AEROBRIDGE.",
        "DEDICATED STAFF WITH PLACARD.",
        "PORTER SERVICE WITH DEDICATED STAFF AT ARRIVALS.",
        "BUGGY SERVICE AVAILABLE. (AS PER THE AVAILABLITY).",
        "ASSIST TILL CAR PARKING.",
        "DEDICATED ARRIVAL ESCORT.",
    ]
    gold_features = silver_features + [
        "LOUNGE ACCESS FOR 2 HOURS (TERMINAL 3).",
    ]
    elite_features = gold_features + [
        "UNLIMITED RESCHEDULING.",
        "FREE CANCELLATION UPTO 6 HOURS.",
    ]

    services = [
        {"id": "1", "title": "Silver Service", "price": 3000, "features": silver_features},
        {"id": "2", "title": "Gold Service", "price": 3500, "features": gold_features},
        {"id": "3", "title": "Elite Service", "price": 5000, "features": elite_features},
    ]

    body = wa_copy.compact_inheritance_package_lines(services)

    # 1. Silver section
    assert "1. *Silver Service*" in body
    assert "₹3,000" in body
    assert "6 services included" in body
    # Silver should NOT have inheritance line
    assert "Includes all" not in body.split("2. *Gold Service*")[0]

    # 2. Gold section
    assert "2. *Gold Service*" in body
    assert "₹3,500" in body
    assert "Includes all Silver services" in body
    assert "+ LOUNGE ACCESS FOR 2 HOURS (TERMINAL 3)." in body
    # Gold should NOT repeat Silver's 6 individual bullet points
    gold_part = body.split("2. *Gold Service*")[1].split("3. *Elite Service*")[0]
    assert "WELCOME GUEST FROM AEROBRIDGE" not in gold_part
    assert "PORTER SERVICE WITH DEDICATED STAFF" not in gold_part

    # 3. Elite section
    assert "3. *Elite Service*" in body
    assert "₹5,000" in body
    assert "Includes all Gold services" in body
    assert "+ UNLIMITED RESCHEDULING." in body
    assert "+ FREE CANCELLATION UPTO 6 HOURS." in body
    # Elite should NOT repeat Silver or Gold's earlier bullet points
    elite_part = body.split("3. *Elite Service*")[1]
    assert "WELCOME GUEST FROM AEROBRIDGE" not in elite_part
    assert "LOUNGE ACCESS FOR 2 HOURS" not in elite_part


def test_two_tier_silver_and_elite_inheritance():
    """Airport with only Silver and Elite — Gold does NOT exist."""
    silver_features = [
        "Meet & Greet at Aerobridge",
        "Porter service for 2 bags",
        "Buggy transfer",
    ]
    elite_features = silver_features + [
        "VIP Lounge access",
        "Chauffeured luxury tarmac transfer",
    ]

    services = [
        {"id": "1", "title": "Silver Service", "price": 2800, "features": silver_features},
        {"id": "2", "title": "Elite Service", "price": 6000, "features": elite_features},
    ]

    body = wa_copy.compact_inheritance_package_lines(services)

    assert "1. *Silver Service*" in body
    assert "3 services included" in body
    assert "2. *Elite Service*" in body
    assert "Includes all Silver services" in body
    assert "+ VIP Lounge access" in body
    assert "+ Chauffeured luxury tarmac transfer" in body
    # Gold must NEVER be mentioned
    assert "Gold" not in body


def test_two_tier_gold_and_elite_inheritance():
    """Airport with only Gold and Elite — Silver does NOT exist."""
    gold_features = [
        "Meet & Greet at Aerobridge",
        "Porter service for 2 bags",
        "Lounge access",
    ]
    elite_features = gold_features + [
        "Chauffeured luxury tarmac transfer",
    ]

    services = [
        {"id": "1", "title": "Gold Service", "price": 4000, "features": gold_features},
        {"id": "2", "title": "Elite Service", "price": 6500, "features": elite_features},
    ]

    body = wa_copy.compact_inheritance_package_lines(services)

    assert "1. *Gold Service*" in body
    assert "3 services included" in body
    assert "2. *Elite Service*" in body
    assert "Includes all Gold services" in body
    assert "+ Chauffeured luxury tarmac transfer" in body
    # Silver must NEVER be mentioned
    assert "Silver" not in body


def test_single_tier_display():
    """Airport with only one package (e.g. Silver)."""
    silver_features = [
        "Meet & Greet at Aerobridge",
        "Porter service",
        "Escort to vehicle",
    ]

    services = [
        {"id": "1", "title": "Silver Service", "price": 2500, "features": silver_features},
    ]

    body = wa_copy.compact_inheritance_package_lines(services)

    assert "1. *Silver Service*" in body
    assert "₹2,500" in body
    assert "3 services included" in body
    assert "Includes all" not in body


def test_same_inclusions_across_tiers():
    """If Gold has identical inclusions as Silver but different price, no false additions."""
    common_features = ["Feature A", "Feature B", "Feature C"]
    services = [
        {"id": "1", "title": "Silver Service", "price": 3000, "features": common_features},
        {"id": "2", "title": "Gold Service", "price": 3500, "features": common_features},
    ]

    body = wa_copy.compact_inheritance_package_lines(services)

    assert "1. *Silver Service*" in body
    assert "3 services included" in body
    assert "2. *Gold Service*" in body
    assert "Includes all Silver services" in body
    # Should not have any "+ " addition line
    assert "+ " not in body


def test_level_2_package_details():
    """Test Level 2 package details on selection."""
    silver_features = ["Escort", "Porter", "Buggy"]
    gold_features = silver_features + ["Lounge Access"]
    elite_features = gold_features + ["Rescheduling"]

    all_svcs = [
        {"id": "10", "title": "Silver Service", "price": 3000, "features": silver_features},
        {"id": "20", "title": "Gold Service", "price": 3500, "features": gold_features},
        {"id": "30", "title": "Elite Service", "price": 5000, "features": elite_features},
    ]

    # Silver selection: shows all 3 exact inclusions
    silver_details = wa_copy.selected_package_details_text(all_svcs[0], all_services=all_svcs)
    assert "• Escort" in silver_details
    assert "• Porter" in silver_details
    assert "• Buggy" in silver_details

    # Gold selection: shows inheritance + additions
    gold_details = wa_copy.selected_package_details_text(all_svcs[1], all_services=all_svcs)
    assert "Gold includes all Silver services plus:" in gold_details
    assert "+ Lounge Access" in gold_details
    assert "• Escort" not in gold_details

    # Elite selection: shows inheritance + additions
    elite_details = wa_copy.selected_package_details_text(all_svcs[2], all_services=all_svcs)
    assert "Elite includes all Gold services plus:" in elite_details
    assert "+ Rescheduling" in elite_details
    assert "• Escort" not in elite_details


def test_airport_packages_body_formatting():
    """Test full message generated by airport_packages_body."""
    services = [
        {"id": "1", "title": "Silver Service", "price": 3000, "features": ["A", "B", "C", "D", "E", "F"]},
        {"id": "2", "title": "Gold Service", "price": 3500, "features": ["A", "B", "C", "D", "E", "F", "Lounge Access"]},
        {"id": "3", "title": "Elite Service", "price": 5000, "features": ["A", "B", "C", "D", "E", "F", "Lounge Access", "Free Rescheduling"]},
    ]

    msg = wa_copy.airport_packages_body(
        airport_name="Indira Gandhi International Airport",
        iata="DEL",
        journey_label="Arrival",
        travel_label="Domestic",
        services=services,
        terminal="Terminal 3",
    )

    assert "✨ *Available services at Indira Gandhi International Airport (DEL)*" in msg
    assert "• *Journey Type*: Arrival" in msg
    assert "• *Travel Type*: Domestic" in msg
    assert "• *Terminal*: Terminal 3" in msg
    assert "Reply with a number or tap *View Packages*:" in msg
    assert "1. *Silver Service*" in msg
    assert "6 services included" in msg
    assert "2. *Gold Service*" in msg
    assert "Includes all Silver services" in msg
    assert "+ Lounge Access" in msg
    assert "3. *Elite Service*" in msg
    assert "Includes all Gold services" in msg
    assert "+ Free Rescheduling" in msg


def test_database_seeded_airports_inheritance():
    """Verify package inheritance generation across seeded database airports."""
    db = SessionLocal()
    try:
        airports = db.query(SupportedAirport).all()
        assert len(airports) > 0

        for apt in airports:
            for jt in ["ARRIVAL", "DEPARTURE"]:
                for tt in [["DOMESTIC", "ALL"], ["INTERNATIONAL", "ALL"]]:
                    # Test both without terminal and with Terminal 3 (for multi-terminal airports)
                    for term in [None, "Terminal 3", "Terminal 1", "T3"]:
                        packages = WhatsAppBookingStateMachine._get_authoritative_airport_packages(
                            db, apt.id, jt, tt, terminal=term
                        )
                        if not packages:
                            continue

                        # Deduplicate by service id/title for menu display
                        seen = set()
                        available_services = []
                        for aps, svc in packages:
                            if svc.name in seen:
                                continue
                            seen.add(svc.name)
                            available_services.append({
                                "id": str(svc.id),
                                "title": svc.name,
                                "price": float(aps.price),
                                "features": aps.features if isinstance(aps.features, list) else [],
                            })

                        if not available_services:
                            continue

                        body = wa_copy.airport_packages_body(
                            airport_name=apt.airport_name,
                            iata=apt.iata_code,
                            journey_label=jt.title(),
                            travel_label="Domestic" if "DOMESTIC" in tt else "International",
                            services=available_services,
                            terminal=term,
                        )

                        # Must be compact (<= 1024 chars for Meta WhatsApp body)
                        assert len(body) <= 1024, f"Body exceeded Meta limit for {apt.iata_code} {jt}: {len(body)} chars"

                        if len(available_services) >= 2:
                            # Higher tier must contain inheritance phrase
                            assert "Includes all" in body
    finally:
        db.close()


if __name__ == "__main__":
    test_three_tier_inheritance_display()
    test_two_tier_silver_and_elite_inheritance()
    test_two_tier_gold_and_elite_inheritance()
    test_single_tier_display()
    test_same_inclusions_across_tiers()
    test_level_2_package_details()
    test_airport_packages_body_formatting()
    test_database_seeded_airports_inheritance()
    print("[+] ALL WHATSAPP PACKAGE INHERITANCE TESTS PASSED 100%!")
