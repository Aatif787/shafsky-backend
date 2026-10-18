import uuid
from sqlalchemy.orm import Session
from app.models.journey_models import SupportedAirport, Service, AirportService


HYD_TRANSIT_DOMESTIC_DOMESTIC_FEATURES = [
    "WELCOME IN THE END OF THE AEROBRIDGE",
    "DEDICATED STAFF WITH PLACARD",
    "PORTER SERVICE WITH DEDICATED STAFF AT ARRIVALS",
    "WHEELCHAIR SERVICE AVAILABLE (THROUGH AIRLINES)",
    "ASSIST IN S.H.A.(TRANSIT AREA)",
    "LOUNGE ACCESS FOR 2 HOURS (AT DEPARTURE ONLY)",
    "ASSIST PAX UPTO BOARDING GATE",
]

HYD_TRANSIT_DOMESTIC_INTERNATIONAL_FEATURES = [
    "WELCOME IN THE END OF THE AEROBRIDGE",
    "DEDICATED STAFF WITH PLACARD",
    "PORTER SERVICE WITH DEDICATED STAFF AT ARRIVALS",
    "WHEELCHAIR SERVICE AVAILABLE",
    "ASSIST IN BAGGAGE BELT AREA",
    "ASSIST AT SEPARATE CHECKIN PROCESS AT AIRLINES COUNTERS",
    "GUIDANCE TO THE IMMIGRATION COUNTER",
    "ASSIST IN S.H.A.(SECURITY HOLD AREA)",
    "LOUNGE ACCESS FOR 2 HOURS (AT DEPARTURE ONLY)",
    "ASSIST PAX UPTO BOARDING GATE",
]


def seed_hyd_production_packages(db: Session, hyd_airport: SupportedAirport, service_map: dict[str, Service]):
    """
    For Hyderabad Airport (HYD) Domestic Departure & Domestic Arrival, International Departure & Arrival, and Transit:
    Seeds production Silver Service (₹3,000), Gold Service (₹3,500), and Elite Service (₹5,000) packages,
    and Transit Services: Domestic-Domestic (₹5,500) and Domestic-International (₹7,500).
    """
    print("\n-- Configuring Production Packages for Hyderabad (HYD) All Categories & Transit --")
    
    # 1. Remove old demo services mapped to HYD Domestic Departure
    db.query(AirportService).filter_by(
        airport_id=hyd_airport.id,
        journey_type="DEPARTURE",
        flight_type="DOMESTIC",
    ).delete(synchronize_session=False)

    # 1a. Remove old demo services mapped to HYD Domestic Arrival
    db.query(AirportService).filter_by(
        airport_id=hyd_airport.id,
        journey_type="ARRIVAL",
        flight_type="DOMESTIC",
    ).delete(synchronize_session=False)

    silver_svc = service_map.get("silver")
    gold_svc = service_map.get("gold")
    elite_svc = service_map.get("elite")

    # ── DEPARTURE PACKAGES ──
    # 1. Silver Service (₹3,000)
    if silver_svc:
        silver_dep = AirportService(
            id=uuid.uuid4(),
            airport_id=hyd_airport.id,
            service_id=silver_svc.id,
            journey_type="DEPARTURE",
            flight_type="DOMESTIC",
            short_description="Premium domestic departure assist from curbside to the boarding gate with dedicated airport support.",
            features=[
                "WELCOME AT THE CURBSIDE AREA",
                "DEDICATED PORTER SERVICE",
                "WHEELCHAIR ASSIST (THROUGH THE AIRLINE, IF REQUIRED)",
                "ASSIST THROUGH THE SEPARATE ENTRY GATE",
                "ASSIST WITH BAGGAGE WRAPPING FACILITIES",
                "ASSIST WITH SEPARATE BAGGAGE CHECK-IN AT THE AIRLINE COUNTER",
                "ASSIST INSIDE THE SECURITY HOLD AREA (SHA)",
                "BUGGY SERVICE TO THE BOARDING GATE (SHARING BASIS, SUBJECT TO AVAILABILITY)",
                "ESCORT TO THE BOARDING GATE",
            ],
            additional_benefits=[],
            min_booking_notice_hours=6,
            is_available=True,
            display_priority=1,
            price=3000.00,
            currency="INR",
        )
        db.add(silver_dep)

    # 2. Gold Service (₹3,500)
    if gold_svc:
        gold_dep = AirportService(
            id=uuid.uuid4(),
            airport_id=hyd_airport.id,
            service_id=gold_svc.id,
            journey_type="DEPARTURE",
            flight_type="DOMESTIC",
            short_description="Premium domestic departure assist with lounge access and airport support from curbside to the boarding gate.",
            features=[
                "WELCOME AT THE CURBSIDE AREA",
                "DEDICATED PORTER SERVICE",
                "WHEELCHAIR ASSIST (THROUGH THE AIRLINE, IF REQUIRED)",
                "ASSIST THROUGH THE SEPARATE ENTRY GATE",
                "ASSIST WITH BAGGAGE WRAPPING FACILITIES",
                "ASSIST WITH SEPARATE CHECK-IN AT THE AIRLINE COUNTERS",
                "ASSIST INSIDE THE SECURITY HOLD AREA (SHA)",
                "COMPLIMENTARY LOUNGE ACCESS",
                "BUGGY SERVICE TO THE BOARDING GATE",
                "ESCORT TO THE BOARDING GATE",
            ],
            additional_benefits=[],
            min_booking_notice_hours=6,
            is_available=True,
            display_priority=2,
            price=3500.00,
            currency="INR",
        )
        db.add(gold_dep)

    # 3. Elite Service (₹5,000)
    if elite_svc:
        elite_dep = AirportService(
            id=uuid.uuid4(),
            airport_id=hyd_airport.id,
            service_id=elite_svc.id,
            journey_type="DEPARTURE",
            flight_type="DOMESTIC",
            short_description="Complete premium domestic departure experience with lounge access, dedicated airport assist, and flexible booking benefits.",
            features=[
                "WELCOME AT THE CURBSIDE AREA",
                "DEDICATED PORTER SERVICE",
                "WHEELCHAIR ASSIST (THROUGH THE AIRLINE, IF REQUIRED)",
                "ASSIST THROUGH THE SEPARATE ENTRY GATE",
                "ASSIST WITH BAGGAGE WRAPPING FACILITIES",
                "ASSIST WITH SEPARATE CHECK-IN AT THE AIRLINE COUNTERS",
                "ASSIST INSIDE THE SECURITY HOLD AREA (SHA)",
                "COMPLIMENTARY LOUNGE ACCESS",
                "BUGGY SERVICE TO THE BOARDING GATE",
                "ESCORT TO THE BOARDING GATE",
            ],
            additional_benefits=[
                "Free cancellation up to 12 hours before the scheduled service time",
                "Unlimited rescheduling",
            ],
            min_booking_notice_hours=6,
            is_available=True,
            display_priority=3,
            price=5000.00,
            currency="INR",
        )
        db.add(elite_dep)

    # ── ARRIVAL PACKAGES ──
    # 1. Silver Service (₹3,000)
    if silver_svc:
        silver_arr = AirportService(
            id=uuid.uuid4(),
            airport_id=hyd_airport.id,
            service_id=silver_svc.id,
            journey_type="ARRIVAL",
            flight_type="DOMESTIC",
            short_description="Premium domestic arrival assist from the aerobridge to the car parking area.",
            features=[
                "WELCOME AT THE END OF THE AEROBRIDGE",
                "DEDICATED STAFF WITH PLACARD",
                "DEDICATED PORTER SERVICE AT ARRIVALS",
                "BUGGY SERVICE FROM THE END OF THE AEROBRIDGE (SHARING BASIS, SUBJECT TO AVAILABILITY)",
                "WHEELCHAIR ASSIST (THROUGH THE AIRLINE, IF REQUIRED)",
                "ASSIST AT THE BAGGAGE BELT AREA",
                "ASSIST GUEST TILL THE CAR PARKING AREA",
            ],
            additional_benefits=[],
            min_booking_notice_hours=6,
            is_available=True,
            display_priority=1,
            price=3000.00,
            currency="INR",
        )
        db.add(silver_arr)

    # 2. Gold Service (₹3,500)
    if gold_svc:
        gold_arr = AirportService(
            id=uuid.uuid4(),
            airport_id=hyd_airport.id,
            service_id=gold_svc.id,
            journey_type="ARRIVAL",
            flight_type="DOMESTIC",
            short_description="Enhanced domestic arrival assist with dedicated airport support from the aerobridge to the car parking area.",
            features=[
                "WELCOME AT THE END OF THE AEROBRIDGE",
                "DEDICATED STAFF WITH PLACARD",
                "DEDICATED PORTER SERVICE AT ARRIVALS",
                "DEDICATED BUGGY SERVICE FROM THE END OF THE AEROBRIDGE",
                "WHEELCHAIR ASSIST (THROUGH THE AIRLINE, IF REQUIRED)",
                "ASSIST AT THE BAGGAGE BELT AREA",
                "ASSIST GUEST TILL THE CAR PARKING AREA",
            ],
            additional_benefits=[],
            min_booking_notice_hours=6,
            is_available=True,
            display_priority=2,
            price=3500.00,
            currency="INR",
        )
        db.add(gold_arr)

    # 3. Elite Service (₹5,000)
    if elite_svc:
        elite_arr = AirportService(
            id=uuid.uuid4(),
            airport_id=hyd_airport.id,
            service_id=elite_svc.id,
            journey_type="ARRIVAL",
            flight_type="DOMESTIC",
            short_description="Complete premium domestic arrival experience with flexible booking benefits and airport assist.",
            features=[
                "WELCOME AT THE END OF THE AEROBRIDGE",
                "DEDICATED STAFF WITH PLACARD",
                "DEDICATED PORTER SERVICE AT ARRIVALS",
                "DEDICATED BUGGY SERVICE FROM THE END OF THE AEROBRIDGE",
                "WHEELCHAIR ASSIST (THROUGH THE AIRLINE, IF REQUIRED)",
                "ASSIST AT THE BAGGAGE BELT AREA",
                "ASSIST GUEST TILL THE CAR PARKING AREA",
            ],
            additional_benefits=[
                "Free cancellation up to 12 hours before the scheduled service time",
                "Unlimited rescheduling",
            ],
            min_booking_notice_hours=6,
            is_available=True,
            display_priority=3,
            price=5000.00,
            currency="INR",
        )
        db.add(elite_arr)
    # 1b. Remove old demo services mapped to HYD International Departure
    db.query(AirportService).filter_by(
        airport_id=hyd_airport.id,
        journey_type="DEPARTURE",
        flight_type="INTERNATIONAL",
    ).delete(synchronize_session=False)

    # ── INTERNATIONAL DEPARTURE PACKAGES ──
    # 1. Silver Service (₹5,000)
    if silver_svc:
        silver_intl_dep = AirportService(
            id=uuid.uuid4(),
            airport_id=hyd_airport.id,
            service_id=silver_svc.id,
            journey_type="DEPARTURE",
            flight_type="INTERNATIONAL",
            short_description="Premium international departure assist from the curbside to the boarding gate.",
            features=[
                "WELCOME AT THE CURBSIDE AREA",
                "DEDICATED PORTER SERVICE",
                "WHEELCHAIR ASSIST (THROUGH THE AIRLINE, IF REQUIRED)",
                "ASSIST THROUGH THE SEPARATE ENTRY GATE",
                "ASSIST AT THE MONEY EXCHANGE COUNTER",
                "ASSIST WITH BAGGAGE WRAPPING FACILITIES",
                "ASSIST WITH SEPARATE BAGGAGE CHECK-IN AT THE AIRLINE COUNTER",
                "ASSIST THROUGH IMMIGRATION",
                "ASSIST INSIDE THE SECURITY HOLD AREA (SHA)",
                "ESCORT TO THE BOARDING GATE",
            ],
            additional_benefits=[],
            min_booking_notice_hours=6,
            is_available=True,
            display_priority=1,
            price=5000.00,
            currency="INR",
        )
        db.add(silver_intl_dep)

    # 2. Gold Service (₹5,500)
    if gold_svc:
        gold_intl_dep = AirportService(
            id=uuid.uuid4(),
            airport_id=hyd_airport.id,
            service_id=gold_svc.id,
            journey_type="DEPARTURE",
            flight_type="INTERNATIONAL",
            short_description="Enhanced international departure assist with lounge access and airport support.",
            features=[
                "WELCOME AT THE CURBSIDE AREA",
                "DEDICATED PORTER SERVICE",
                "WHEELCHAIR ASSIST (THROUGH THE AIRLINE, IF REQUIRED)",
                "ASSIST THROUGH THE SEPARATE ENTRY GATE",
                "ASSIST AT THE MONEY EXCHANGE COUNTER",
                "ASSIST WITH BAGGAGE WRAPPING FACILITIES",
                "ASSIST WITH SEPARATE BAGGAGE CHECK-IN AT THE AIRLINE COUNTER",
                "ASSIST THROUGH IMMIGRATION",
                "ASSIST INSIDE THE SECURITY HOLD AREA (SHA)",
                "COMPLIMENTARY LOUNGE ACCESS (UP TO 2 HOURS)",
                "ESCORT TO THE BOARDING GATE",
            ],
            additional_benefits=[],
            min_booking_notice_hours=6,
            is_available=True,
            display_priority=2,
            price=5500.00,
            currency="INR",
        )
        db.add(gold_intl_dep)

    # 3. Elite Service (₹7,000)
    if elite_svc:
        elite_intl_dep = AirportService(
            id=uuid.uuid4(),
            airport_id=hyd_airport.id,
            service_id=elite_svc.id,
            journey_type="DEPARTURE",
            flight_type="INTERNATIONAL",
            short_description="Complete premium international departure experience with lounge access, airport assist, and flexible booking benefits.",
            features=[
                "WELCOME AT THE CURBSIDE AREA",
                "DEDICATED PORTER SERVICE",
                "WHEELCHAIR ASSIST (THROUGH THE AIRLINE, IF REQUIRED)",
                "ASSIST THROUGH THE SEPARATE ENTRY GATE",
                "ASSIST AT THE MONEY EXCHANGE COUNTER",
                "ASSIST WITH BAGGAGE WRAPPING FACILITIES",
                "ASSIST WITH SEPARATE BAGGAGE CHECK-IN AT THE AIRLINE COUNTER",
                "ASSIST THROUGH IMMIGRATION",
                "ASSIST INSIDE THE SECURITY HOLD AREA (SHA)",
                "COMPLIMENTARY LOUNGE ACCESS (UP TO 2 HOURS)",
                "ESCORT TO THE BOARDING GATE",
            ],
            additional_benefits=[
                "Free cancellation up to 12 hours before the scheduled service time",
                "Unlimited rescheduling",
            ],
            min_booking_notice_hours=6,
            is_available=True,
            display_priority=3,
            price=7000.00,
            currency="INR",
        )
        db.add(elite_intl_dep)
    # 1c. Remove old demo services mapped to HYD International Arrival
    db.query(AirportService).filter_by(
        airport_id=hyd_airport.id,
        journey_type="ARRIVAL",
        flight_type="INTERNATIONAL",
    ).delete(synchronize_session=False)

    # ── INTERNATIONAL ARRIVAL PACKAGES ──
    # 1. Silver Service (₹2,500)
    if silver_svc:
        silver_intl_arr = AirportService(
            id=uuid.uuid4(),
            airport_id=hyd_airport.id,
            service_id=silver_svc.id,
            journey_type="ARRIVAL",
            flight_type="INTERNATIONAL",
            short_description="Premium international arrival assist from post-customs to the car parking area.",
            features=[
                "ASSIST AFTER CUSTOMS CLEARANCE",
                "ASSIST AT THE BAGGAGE BELT AREA",
                "COORDINATION WITH THE RECEIVING PARTY",
                "ASSIST GUEST TILL THE CAR PARKING AREA",
            ],
            additional_benefits=[],
            min_booking_notice_hours=6,
            is_available=True,
            display_priority=1,
            price=2500.00,
            currency="INR",
        )
        db.add(silver_intl_arr)

    # 2. Gold Service (₹3,500)
    if gold_svc:
        gold_intl_arr = AirportService(
            id=uuid.uuid4(),
            airport_id=hyd_airport.id,
            service_id=gold_svc.id,
            journey_type="ARRIVAL",
            flight_type="INTERNATIONAL",
            short_description="Enhanced international arrival assist with VIP parking facilitation and airport support.",
            features=[
                "ASSIST AFTER CUSTOMS CLEARANCE",
                "ASSIST AT THE BAGGAGE BELT AREA",
                "COORDINATION WITH THE RECEIVING PARTY",
                "ASSIST GUEST TILL THE CAR PARKING AREA",
                "VIP CAR PARKING FACILITATION",
            ],
            additional_benefits=[],
            min_booking_notice_hours=6,
            is_available=True,
            display_priority=2,
            price=3500.00,
            currency="INR",
        )
        db.add(gold_intl_arr)

    # 3. Elite Service (₹4,500)
    if elite_svc:
        elite_intl_arr = AirportService(
            id=uuid.uuid4(),
            airport_id=hyd_airport.id,
            service_id=elite_svc.id,
            journey_type="ARRIVAL",
            flight_type="INTERNATIONAL",
            short_description="Complete premium international arrival experience with VIP parking facilitation and flexible booking benefits.",
            features=[
                "ASSIST AFTER CUSTOMS CLEARANCE",
                "ASSIST AT THE BAGGAGE BELT AREA",
                "COORDINATION WITH THE RECEIVING PARTY",
                "ASSIST GUEST TILL THE CAR PARKING AREA",
                "VIP CAR PARKING FACILITATION",
            ],
            additional_benefits=[
                "Free cancellation up to 14 hours before the scheduled service time",
                "Unlimited rescheduling",
            ],
            min_booking_notice_hours=6,
            is_available=True,
            display_priority=3,
            price=4500.00,
            currency="INR",
        )
        db.add(elite_intl_arr)

    # 1d. Remove old demo services mapped to HYD Transit
    db.query(AirportService).filter_by(
        airport_id=hyd_airport.id,
        journey_type="TRANSIT",
    ).delete(synchronize_session=False)

    # ── HYD TRANSIT PACKAGES ──
    transit_svc = service_map.get("meet_greet") or silver_svc
    if transit_svc:
        # 1. Domestic → Domestic (₹5,500)
        db.add(AirportService(
            id=uuid.uuid4(),
            airport_id=hyd_airport.id,
            service_id=transit_svc.id,
            journey_type="TRANSIT",
            flight_type="DOMESTIC_DOMESTIC",
            short_description="Premium transit assist for passengers connecting from a domestic flight to another domestic flight at Hyderabad Airport.",
            features=list(HYD_TRANSIT_DOMESTIC_DOMESTIC_FEATURES),
            additional_benefits=[],
            min_booking_notice_hours=6,
            is_available=True,
            display_priority=1,
            price=5500.00,
            currency="INR",
        ))

        # 2. Domestic → International (₹7,500)
        db.add(AirportService(
            id=uuid.uuid4(),
            airport_id=hyd_airport.id,
            service_id=transit_svc.id,
            journey_type="TRANSIT",
            flight_type="DOMESTIC_INTERNATIONAL",
            short_description="Premium transit assist for passengers connecting from a domestic flight to an international flight at Hyderabad Airport.",
            features=list(HYD_TRANSIT_DOMESTIC_INTERNATIONAL_FEATURES),
            additional_benefits=[],
            min_booking_notice_hours=6,
            is_available=True,
            display_priority=2,
            price=7500.00,
            currency="INR",
        ))

    db.flush()
    print("  + Created HYD Production Packages (Dom Dep/Arr, Intl Dep/Arr, and Transit Dom-Dom INR 5500, Dom-Intl INR 7500)")
