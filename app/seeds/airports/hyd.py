import uuid
from sqlalchemy.orm import Session
from app.models.journey_models import SupportedAirport, Service, AirportService


HYD_TRANSIT_DOMESTIC_DOMESTIC_FEATURES = [
    "WELCOME GUEST FROM AEROBRIDGE",
    "DEDICATED STAFF WITH PLACARD",
    "PORTER SERVICE WITH DEDICATED STAFF AT ARRIVALS",
    "WHEELCHAIR SERVICE AVAILABLE (Through Airlines)",
    "ASSIST IN S.H.A.(TRANSIT AREA)",
    "LOUNGE ACCESS FOR 2 HOURS (AT DEPARTURE ONLY)",
    "ASSIST PAX UPTO BOARDING GATE",
]

HYD_TRANSIT_DOMESTIC_INTERNATIONAL_FEATURES = [
    "WELCOME GUEST FROM AEROBRIDGE",
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
                "Welcome at the Curbside Area",
                "Dedicated Porter Service",
                "Wheelchair Assist (through the airline, if required)",
                "Assist through the Separate Entry Gate",
                "Assist with Baggage Wrapping Facilities",
                "Assist with Separate Baggage Check-in at the Airline Counter",
                "Assist inside the Security Hold Area (SHA)",
                "Buggy Service to the Boarding Gate (Sharing Basis, subject to availability)",
                "Escort to the Boarding Gate",
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
                "Welcome at the Curbside Area",
                "Dedicated Porter Service",
                "Wheelchair Assist (through the airline, if required)",
                "Assist through the Separate Entry Gate",
                "Assist with Baggage Wrapping Facilities",
                "Assist with Separate Check-in at the Airline Counters",
                "Assist inside the Security Hold Area (SHA)",
                "Complimentary Lounge Access",
                "Buggy Service to the Boarding Gate",
                "Escort to the Boarding Gate",
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
                "Welcome at the Curbside Area",
                "Dedicated Porter Service",
                "Wheelchair Assist (through the airline, if required)",
                "Assist through the Separate Entry Gate",
                "Assist with Baggage Wrapping Facilities",
                "Assist with Separate Check-in at the Airline Counters",
                "Assist inside the Security Hold Area (SHA)",
                "Complimentary Lounge Access",
                "Buggy Service to the Boarding Gate",
                "Escort to the Boarding Gate",
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
                "Welcome at the End of the Aerobridge",
                "Dedicated Staff with Placard",
                "Dedicated Porter Service at Arrivals",
                "Buggy Service from the End of the Aerobridge (Sharing Basis, subject to availability)",
                "Wheelchair Assist (through the airline, if required)",
                "Assist at the Baggage Belt Area",
                "Escort to the Car Parking Area",
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
                "Welcome at the End of the Aerobridge",
                "Dedicated Staff with Placard",
                "Dedicated Porter Service at Arrivals",
                "Dedicated Buggy Service from the End of the Aerobridge",
                "Wheelchair Assist (through the airline, if required)",
                "Assist at the Baggage Belt Area",
                "Escort to the Car Parking Area",
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
                "Welcome at the End of the Aerobridge",
                "Dedicated Staff with Placard",
                "Dedicated Porter Service at Arrivals",
                "Dedicated Buggy Service from the End of the Aerobridge",
                "Wheelchair Assist (through the airline, if required)",
                "Assist at the Baggage Belt Area",
                "Escort to the Car Parking Area",
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
                "Welcome at the Curbside Area",
                "Dedicated Porter Service",
                "Wheelchair Assist (through the airline, if required)",
                "Assist through the Separate Entry Gate",
                "Assist at the Money Exchange Counter",
                "Assist with Baggage Wrapping Facilities",
                "Assist with Separate Baggage Check-in at the Airline Counter",
                "Assist through Immigration",
                "Assist inside the Security Hold Area (SHA)",
                "Escort to the Boarding Gate",
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
                "Welcome at the Curbside Area",
                "Dedicated Porter Service",
                "Wheelchair Assist (through the airline, if required)",
                "Assist through the Separate Entry Gate",
                "Assist at the Money Exchange Counter",
                "Assist with Baggage Wrapping Facilities",
                "Assist with Separate Baggage Check-in at the Airline Counter",
                "Assist through Immigration",
                "Assist inside the Security Hold Area (SHA)",
                "Complimentary Lounge Access (up to 2 hours)",
                "Escort to the Boarding Gate",
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
                "Welcome at the Curbside Area",
                "Dedicated Porter Service",
                "Wheelchair Assist (through the airline, if required)",
                "Assist through the Separate Entry Gate",
                "Assist at the Money Exchange Counter",
                "Assist with Baggage Wrapping Facilities",
                "Assist with Separate Baggage Check-in at the Airline Counter",
                "Assist through Immigration",
                "Assist inside the Security Hold Area (SHA)",
                "Complimentary Lounge Access (up to 2 hours)",
                "Escort to the Boarding Gate",
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
                "Assist after Customs Clearance",
                "Assist at the Baggage Belt Area",
                "Coordination with the Receiving Party",
                "Escort to the Car Parking Area",
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
                "Assist after Customs Clearance",
                "Assist at the Baggage Belt Area",
                "Coordination with the Receiving Party",
                "Escort to the Car Parking Area",
                "VIP Car Parking Facilitation",
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
                "Assist after Customs Clearance",
                "Assist at the Baggage Belt Area",
                "Coordination with the Receiving Party",
                "Escort to the Car Parking Area",
                "VIP Car Parking Facilitation",
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
