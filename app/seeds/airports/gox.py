import uuid
from sqlalchemy.orm import Session
from app.models.journey_models import SupportedAirport, Service, AirportService


# ==============================================================================
# GOA MOPA / MANOHAR INTERNATIONAL AIRPORT (GOX) PRODUCTION FEATURES
# ==============================================================================

GOX_DOMESTIC_DEPARTURE_SILVER_FEATURES = [
    "WELCOME GUEST FROM CURB SIDE AREA",
    "PORTER SERVICE WITH DEDICATED STAFF",
    "WHEELCHAIR SERVICE AVAILABLE (Through Airlines)",
    "ASSIST FROM SEPARATE ENTRY GATE",
    "ASSIST TO BAGGAGE WRAPPING FACILITIES",
    "ASSIST SEPARATE BAGGAGE CHECK-IN AT AIRLINE COUNTER",
    "ASSIST IN S.H.A.(SECURITY HOLD AREA)",
    "BUGGY SERVICE AVAILABLE TILL THE BOARDING GATE (SHARING BASIS)",
    "ASSIST GUEST UPTO BOARDING GATE",
]

GOX_DOMESTIC_DEPARTURE_GOLD_FEATURES = [
    "WELCOME GUEST FROM CURBSIDE AREA",
    "PORTER SERVICE WITH DEDICATED STAFF",
    "WHEELCHAIR SERVICE AVAILABLE (Through Airlines)",
    "ASSIST FROM SEPARATE ENTRY GATE",
    "ASSIST TO BAGGAGE WRAPPING FACILITIES",
    "ASSIST AT SEPARATE CHECKIN PROCESS AT COUNTERS",
    "ASSIST IN S.H.A.(SECURITY HOLD AREA)",
    "LOUNGE SERVICE FACILITY AVAILABLE",
    "BUGGY SERVICE AVAILABLE TILL THE BOARDING GATE",
    "ASSIST GUEST TILL THE BOARDING GATE",
]

GOX_DOMESTIC_DEPARTURE_ELITE_FEATURES = [
    "WELCOME GUEST FROM CURBSIDE AREA",
    "PORTER SERVICE WITH DEDICATED STAFF",
    "WHEELCHAIR SERVICE AVAILABLE (Through Airlines)",
    "ASSIST FROM SEPARATE ENTRY GATE",
    "ASSIST TO BAGGAGE WRAPPING FACILITIES",
    "ASSIST AT SEPARATE CHECKIN PROCESS AT COUNTERS",
    "ASSIST IN S.H.A.(SECURITY HOLD AREA)",
    "LOUNGE SERVICE FACILITY AVAILABLE",
    "BUGGY SERVICE AVAILABLE TILL THE BOARDING GATE",
    "ASSIST GUEST TILL THE BOARDING GATE",
    "CANCELLATION BENEFITS UPTO 12 HOUR’S OF SERVICE TIME",
    "UNLIMITED RESCHEDULING",
]

GOX_DOMESTIC_ARRIVAL_SILVER_FEATURES = [
    "WELCOME GUEST FROM AEROBRIDGE",
    "DEDICATED STAFF WITH PLACARD",
    "PORTER SERVICE WITH DEDICATED STAFF AT ARRIVALS",
    "WHEELCHAIR SERVICE AVAILABLE WITH DEDICATED STAFF(Through Airlines)",
    "ASSIST IN BAGGAGE BELT AREA",
    "ASSIST GUEST TILL THE CAR PARKING AREA",
]

GOX_DOMESTIC_ARRIVAL_GOLD_FEATURES = [
    "WELCOME GUEST FROM AEROBRIDGE",
    "DEDICATED STAFF WITH PLACARD",
    "PORTER SERVICE WITH DEDICATED STAFF AT ARRIVALS",
    "WHEELCHAIR SERVICE AVAILABLE WITH DEDICATED STAFF(Through Airlines)",
    "ASSIST IN BAGGAGE BELT AREA",
    "ASSIST GUEST TILL THE CAR PARKING AREA",
]

GOX_DOMESTIC_ARRIVAL_ELITE_FEATURES = [
    "WELCOME GUEST FROM AEROBRIDGE",
    "DEDICATED STAFF WITH PLACARD",
    "PORTER SERVICE WITH DEDICATED STAFF AT ARRIVALS",
    "WHEELCHAIR SERVICE AVAILABLE WITH DEDICATED STAFF(Through Airlines)",
    "ASSIST IN BAGGAGE BELT AREA",
    "ASSIST GUEST TILL THE CAR PARKING AREA",
    "CANCELLATION BENEFITS UPTO 12 HOUR’S OF SERVICE TIME",
    "UNLIMITED RESCHEDULING",
]

GOX_INTERNATIONAL_DEPARTURE_SILVER_FEATURES = [
    "WELCOME GUEST FROM CURB SIDE AREA",
    "PORTER SERVICE WITH DEDICATED STAFF",
    "WHEELCHAIR SERVICE AVAILABLE (Through Airlines)",
    "ASSIST FROM SEPARATE ENTRY GATE",
    "ASSIST IN MONEY EXCHANGE COUNTER",
    "ASSIST TO BAGGAGE WRAPPING FACILITIES",
    "ASSIST SEPARATE BAGGAGE CHECK-IN AT AIRLINE COUNTER",
    "ASSIST FOR IMMIGRATION COUNTERS",
    "ASSIST IN S.H.A.(SECURITY HOLD AREA)",
    "ASSIST GUEST UPTO BOARDING GATE",
]

GOX_INTERNATIONAL_DEPARTURE_GOLD_FEATURES = [
    "WELCOME GUEST FROM CURB SIDE AREA",
    "PORTER SERVICE WITH DEDICATED STAFF",
    "WHEELCHAIR SERVICE AVAILABLE (Through Airlines)",
    "ASSIST FROM SEPARATE ENTRY GATE",
    "ASSIST IN MONEY EXCHANGE COUNTER",
    "ASSIST TO BAGGAGE WRAPPING FACILITIES",
    "ASSIST SEPARATE BAGGAGE CHECK-IN AT AIRLINE COUNTER",
    "ASSIST FOR IMMIGRATION COUNTERS",
    "ASSIST IN S.H.A.(SECURITY HOLD AREA)",
    "LOUNGE SERVICE FACILITY AVAILABLE (02 HOURS)",
    "ASSIST GUEST UPTO BOARDING GATE",
]

GOX_INTERNATIONAL_DEPARTURE_ELITE_FEATURES = [
    "WELCOME GUEST FROM CURB SIDE AREA",
    "PORTER SERVICE WITH DEDICATED STAFF",
    "WHEELCHAIR SERVICE AVAILABLE (Through Airlines)",
    "ASSIST FROM SEPARATE ENTRY GATE",
    "ASSIST IN MONEY EXCHANGE COUNTER",
    "ASSIST TO BAGGAGE WRAPPING FACILITIES",
    "ASSIST SEPARATE BAGGAGE CHECK-IN AT AIRLINE COUNTER",
    "ASSIST FOR IMMIGRATION COUNTERS",
    "ASSIST IN S.H.A.(SECURITY HOLD AREA)",
    "LOUNGE SERVICE FACILITY AVAILABLE (02 HOURS)",
    "ASSIST GUEST UPTO BOARDING GATE",
    "CANCELLATION BENEFITS UPTO 12 HOUR’S OF SERVICE TIME",
    "UNLIMITED RESCHEDULING",
]

GOX_INTERNATIONAL_ARRIVAL_SILVER_FEATURES = [
    "ASSIST FOR POST CUSTOMS.",
    "ASSIST FOR BAGGAGE BELT.",
    "COORDINATION WITH RECEIVING PERSON.",
]

GOX_INTERNATIONAL_ARRIVAL_ELITE_FEATURES = [
    "ASSIST FOR POST CUSTOMS.",
    "ASSIST FOR BAGGAGE BELT.",
    "COORDINATION WITH RECEIVING PERSON.",
    "UNLIMITED RESCHEDULING",
    "CANCELLATION BENEFITS UPTO 12 HOUR’S OF SERVICE TIME.",
]

GOX_TRANSIT_DOMESTIC_DOMESTIC_FEATURES = [
    "WELCOME GUEST FROM AEROBRIDGE",
    "DEDICATED STAFF WITH PLACARD",
    "PORTER SERVICE WITH DEDICATED STAFF AT ARRIVALS",
    "WHEELCHAIR SERVICE AVAILABLE (THROUGH AIRLINES)",
    "ASSIST IN S.H.A.(TRANSIT AREA)",
    "LOUNGE ACCESS FOR 2 HOURS (AT DEPARTURE ONLY)",
    "ASSIST PAX UPTO BOARDING GATE",
]

GOX_TRANSIT_DOMESTIC_INTERNATIONAL_FEATURES = [
    "WELCOME GUEST FROM AEROBRIDGE",
    "DEDICATED STAFF WITH PLACARD",
    "PORTER SERVICE WITH DEDICATED STAFF AT ARRIVALS",
    "WHEELCHAIR SERVICE AVAILABLE (THROUGH AIRLINES)",
    "ASSIST IN BAGGAGE BELT AREA",
    "ASSIST SEPARATE BAGGAGE CHECK-IN AT AIRLINE COUNTER",
    "GUIDANCE TO THE IMMIGRATION COUNTER",
    "ASSIST IN S.H.A.(SECURITY HOLD AREA)",
    "LOUNGE ACCESS FOR 2 HOURS (AT DEPARTURE ONLY)",
    "ASSIST GUEST UPTO BOARDING GATE",
]

GOX_TRANSIT_INTERNATIONAL_INTERNATIONAL_FEATURES = [
    "WELCOME GUEST FROM AEROBRIDGE",
    "DEDICATED STAFF WITH PLACARD",
    "PORTER SERVICE WITH DEDICATED STAFF AT ARRIVALS",
    "WHEELCHAIR SERVICE AVAILABLE (Through Airlines)",
    "ASSIST IN S.H.A.(TRANSIT AREA)",
    "LOUNGE ACCESS FOR 2 HOURS (AT DEPARTURE ONLY)",
    "ASSIST PAX UPTO BOARDING GATE",
]


def seed_gox_production_packages(db: Session, gox_airport: SupportedAirport, service_map: dict[str, Service]):
    """
    For Goa Mopa Airport (GOX):
    Configures authoritative production packages:
    - Domestic Departure: Silver (₹2,500), Gold (₹3,000), Elite (₹4,500)
    - Domestic Arrival: Silver (₹2,500), Gold (₹3,000), Elite (₹4,500)
    - International Departure: Silver (₹4,500), Gold (₹5,000), Elite (₹7,000)
    - International Arrival: Silver (₹2,500), Elite (₹4,500) [Gold NOT CONFIGURED]
    - Transit:
      * Domestic-Domestic (₹4,500)
      * Domestic-International (₹6,500)
      * International-International (₹7,000)
      [International-Domestic NOT CONFIGURED]
    Total active packages: 14.
    """
    print("\n-- Configuring Production Packages for Goa Mopa Airport (GOX) --")

    # 1. Clear all existing services mapped to GOX to eliminate duplicates and stale records
    db.query(AirportService).filter_by(
        airport_id=gox_airport.id,
    ).delete(synchronize_session=False)

    silver_svc = service_map.get("silver")
    gold_svc = service_map.get("gold")
    elite_svc = service_map.get("elite")
    transit_svc = service_map.get("meet_greet") or silver_svc

    if not silver_svc or not gold_svc or not elite_svc:
        raise RuntimeError("GOX requires catalog services: silver, gold, elite")

    # ── 1. DOMESTIC DEPARTURE ──
    # Silver (INR 2500)
    db.add(AirportService(
        id=uuid.uuid4(),
        airport_id=gox_airport.id,
        service_id=silver_svc.id,
        journey_type="DEPARTURE",
        flight_type="DOMESTIC",
        short_description="Domestic departure assist from curbside to the boarding gate.",
        features=list(GOX_DOMESTIC_DEPARTURE_SILVER_FEATURES),
        additional_benefits=[],
        min_booking_notice_hours=6,
        is_available=True,
        display_priority=1,
        price=2500.00,
        currency="INR",
    ))
    # Gold (INR 3000)
    db.add(AirportService(
        id=uuid.uuid4(),
        airport_id=gox_airport.id,
        service_id=gold_svc.id,
        journey_type="DEPARTURE",
        flight_type="DOMESTIC",
        short_description="Domestic departure assist with lounge service and dedicated airport support.",
        features=list(GOX_DOMESTIC_DEPARTURE_GOLD_FEATURES),
        additional_benefits=[],
        min_booking_notice_hours=6,
        is_available=True,
        display_priority=2,
        price=3000.00,
        currency="INR",
    ))
    # Elite (INR 4500)
    db.add(AirportService(
        id=uuid.uuid4(),
        airport_id=gox_airport.id,
        service_id=elite_svc.id,
        journey_type="DEPARTURE",
        flight_type="DOMESTIC",
        short_description="Elite domestic departure assist with lounge service, cancellation benefits, and unlimited rescheduling.",
        features=list(GOX_DOMESTIC_DEPARTURE_ELITE_FEATURES),
        additional_benefits=["CANCELLATION BENEFITS UPTO 12 HOUR’S OF SERVICE TIME", "UNLIMITED RESCHEDULING"],
        min_booking_notice_hours=6,
        is_available=True,
        display_priority=3,
        price=4500.00,
        currency="INR",
    ))

    # ── 2. DOMESTIC ARRIVAL ──
    # Silver (INR 2500)
    db.add(AirportService(
        id=uuid.uuid4(),
        airport_id=gox_airport.id,
        service_id=silver_svc.id,
        journey_type="ARRIVAL",
        flight_type="DOMESTIC",
        short_description="Domestic arrival assist from aerobridge to car parking area.",
        features=list(GOX_DOMESTIC_ARRIVAL_SILVER_FEATURES),
        additional_benefits=[],
        min_booking_notice_hours=6,
        is_available=True,
        display_priority=1,
        price=2500.00,
        currency="INR",
    ))
    # Gold (INR 3000)
    db.add(AirportService(
        id=uuid.uuid4(),
        airport_id=gox_airport.id,
        service_id=gold_svc.id,
        journey_type="ARRIVAL",
        flight_type="DOMESTIC",
        short_description="Domestic arrival assist with dedicated staff from aerobridge to car parking area.",
        features=list(GOX_DOMESTIC_ARRIVAL_GOLD_FEATURES),
        additional_benefits=[],
        min_booking_notice_hours=6,
        is_available=True,
        display_priority=2,
        price=3000.00,
        currency="INR",
    ))
    # Elite (INR 4500)
    db.add(AirportService(
        id=uuid.uuid4(),
        airport_id=gox_airport.id,
        service_id=elite_svc.id,
        journey_type="ARRIVAL",
        flight_type="DOMESTIC",
        short_description="Elite domestic arrival assist with cancellation benefits and unlimited rescheduling.",
        features=list(GOX_DOMESTIC_ARRIVAL_ELITE_FEATURES),
        additional_benefits=["CANCELLATION BENEFITS UPTO 12 HOUR’S OF SERVICE TIME", "UNLIMITED RESCHEDULING"],
        min_booking_notice_hours=6,
        is_available=True,
        display_priority=3,
        price=4500.00,
        currency="INR",
    ))

    # ── 3. INTERNATIONAL DEPARTURE ──
    # Silver (INR 4500)
    db.add(AirportService(
        id=uuid.uuid4(),
        airport_id=gox_airport.id,
        service_id=silver_svc.id,
        journey_type="DEPARTURE",
        flight_type="INTERNATIONAL",
        short_description="International departure assist from curbside to boarding gate.",
        features=list(GOX_INTERNATIONAL_DEPARTURE_SILVER_FEATURES),
        additional_benefits=[],
        min_booking_notice_hours=6,
        is_available=True,
        display_priority=1,
        price=4500.00,
        currency="INR",
    ))
    # Gold (INR 5000)
    db.add(AirportService(
        id=uuid.uuid4(),
        airport_id=gox_airport.id,
        service_id=gold_svc.id,
        journey_type="DEPARTURE",
        flight_type="INTERNATIONAL",
        short_description="International departure assist with 2-hour lounge access and full terminal escort.",
        features=list(GOX_INTERNATIONAL_DEPARTURE_GOLD_FEATURES),
        additional_benefits=[],
        min_booking_notice_hours=6,
        is_available=True,
        display_priority=2,
        price=5000.00,
        currency="INR",
    ))
    # Elite (INR 7000)
    db.add(AirportService(
        id=uuid.uuid4(),
        airport_id=gox_airport.id,
        service_id=elite_svc.id,
        journey_type="DEPARTURE",
        flight_type="INTERNATIONAL",
        short_description="Elite international departure assist with lounge access, cancellation benefits, and unlimited rescheduling.",
        features=list(GOX_INTERNATIONAL_DEPARTURE_ELITE_FEATURES),
        additional_benefits=["CANCELLATION BENEFITS UPTO 12 HOUR’S OF SERVICE TIME", "UNLIMITED RESCHEDULING"],
        min_booking_notice_hours=6,
        is_available=True,
        display_priority=3,
        price=7000.00,
        currency="INR",
    ))

    # ── 4. INTERNATIONAL ARRIVAL ──
    # Silver (INR 2500)
    db.add(AirportService(
        id=uuid.uuid4(),
        airport_id=gox_airport.id,
        service_id=silver_svc.id,
        journey_type="ARRIVAL",
        flight_type="INTERNATIONAL",
        short_description="International arrival assist from post customs to baggage belt and receiving person coordination.",
        features=list(GOX_INTERNATIONAL_ARRIVAL_SILVER_FEATURES),
        additional_benefits=[],
        min_booking_notice_hours=6,
        is_available=True,
        display_priority=1,
        price=2500.00,
        currency="INR",
    ))
    # Elite (INR 4500) - Note: Gold is NOT CONFIGURED
    db.add(AirportService(
        id=uuid.uuid4(),
        airport_id=gox_airport.id,
        service_id=elite_svc.id,
        journey_type="ARRIVAL",
        flight_type="INTERNATIONAL",
        short_description="Elite international arrival assist with cancellation benefits and unlimited rescheduling.",
        features=list(GOX_INTERNATIONAL_ARRIVAL_ELITE_FEATURES),
        additional_benefits=["UNLIMITED RESCHEDULING", "CANCELLATION BENEFITS UPTO 12 HOUR’S OF SERVICE TIME."],
        min_booking_notice_hours=6,
        is_available=True,
        display_priority=2,
        price=4500.00,
        currency="INR",
    ))

    # ── 5. TRANSIT ──
    if transit_svc:
        # Domestic -> Domestic (INR 4500)
        db.add(AirportService(
            id=uuid.uuid4(),
            airport_id=gox_airport.id,
            service_id=transit_svc.id,
            journey_type="TRANSIT",
            flight_type="DOMESTIC_DOMESTIC",
            short_description="Domestic to domestic transit assist at Goa Mopa Airport.",
            features=list(GOX_TRANSIT_DOMESTIC_DOMESTIC_FEATURES),
            additional_benefits=[],
            min_booking_notice_hours=6,
            is_available=True,
            display_priority=1,
            price=4500.00,
            currency="INR",
        ))
        # Domestic -> International (INR 6500)
        db.add(AirportService(
            id=uuid.uuid4(),
            airport_id=gox_airport.id,
            service_id=transit_svc.id,
            journey_type="TRANSIT",
            flight_type="DOMESTIC_INTERNATIONAL",
            short_description="Domestic to international transit assist at Goa Mopa Airport.",
            features=list(GOX_TRANSIT_DOMESTIC_INTERNATIONAL_FEATURES),
            additional_benefits=[],
            min_booking_notice_hours=6,
            is_available=True,
            display_priority=2,
            price=6500.00,
            currency="INR",
        ))
        # International -> International (INR 7000)
        db.add(AirportService(
            id=uuid.uuid4(),
            airport_id=gox_airport.id,
            service_id=transit_svc.id,
            journey_type="TRANSIT",
            flight_type="INTERNATIONAL_INTERNATIONAL",
            short_description="International to international transit assist at Goa Mopa Airport.",
            features=list(GOX_TRANSIT_INTERNATIONAL_INTERNATIONAL_FEATURES),
            additional_benefits=[],
            min_booking_notice_hours=6,
            is_available=True,
            display_priority=3,
            price=7000.00,
            currency="INR",
        ))

    db.flush()
    print("  + Configured GOX Production Packages: 14 authoritative active mappings (Dom Dep 3, Dom Arr 3, Intl Dep 3, Intl Arr 2, Transit 3)")
