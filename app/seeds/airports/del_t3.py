import uuid
from sqlalchemy.orm import Session
from app.models.journey_models import SupportedAirport, Service, AirportService


DEL_T3_DOMESTIC_DEPARTURE_SILVER_FEATURES = [
    "WELCOME GUEST FROM CURB SIDE AREA.",
    "PORTER SERVICE WITH DEDICATED STAFF.",
    "WHEELCHAIR SERVICE AVAILABLE (THROUGH AIRLINES).",
    "ASSIST FROM SEPERATE ENTRY GATE.",
    "ASSIST SEPARATE BAGGAGE CHECK IN AT AIRLINE COUNTER.",
    "ASSIST IN S.H.A (SECURITY HOLD AREA).",
    "BUGGY SERVICE AVAILABLE.",
    "ASSIST GUEST UPTO BOARDING GATE.",
]

DEL_T3_DOMESTIC_DEPARTURE_GOLD_FEATURES = [
    "WELCOME GUEST FROM CURB SIDE AREA.",
    "PORTER SERVICE WITH DEDICATED STAFF.",
    "WHEELCHAIR SERVICE AVAILABLE (THROUGH AIRLINES).",
    "ASSIST FROM SEPERATE ENTRY GATE.",
    "ASSIST SEPARATE BAGGAGE CHECK IN AT AIRLINE COUNTER.",
    "ASSIST IN S.H.A (SECURITY HOLD AREA).",
    "LOUNGE ACCESS FOR 2 HOURS.",
    "BUGGY SERVICE AVAILABLE.",
    "ASSIST GUEST UPTO BOARDING GATE.",
]

DEL_T3_DOMESTIC_DEPARTURE_ELITE_FEATURES = [
    "WELCOME GUEST FROM CURB SIDE AREA.",
    "PORTER SERVICE WITH DEDICATED STAFF.",
    "WHEELCHAIR SERVICE AVAILABLE (THROUGH AIRLINES).",
    "ASSIST FROM SEPERATE ENTRY GATE.",
    "ASSIST SEPARATE BAGGAGE CHECK IN AT AIRLINE COUNTER.",
    "ASSIST IN S.H.A (SECURITY HOLD AREA).",
    "LOUNGE ACCESS FOR 2 HOURS.",
    "BUGGY SERVICE AVAILABLE.",
    "ASSIST GUEST UPTO BOARDING GATE.",
    "UNLIMITED RESCHEDULING (PRIOR 12 HOURS)",
    "CANCELLATION BENEFITS UPTO 12 HOURS OF SERVICE TIME.",
]

DEL_T3_DOMESTIC_ARRIVAL_SILVER_FEATURES = [
    "WELCOME GUEST FROM AEROBRIDGE.",
    "DEDICATED STAFF WITH PLACARD.",
    "PORTER SERVICE WITH DEDICATED STAFF AT ARRIVALS.",
    "BUGGY SERVICE AVAILABLE. (AS PER THE AVAILABLITY).",
    "ASSIST IN BAGGAGE BELT AREA.",
    "ASSIST GUEST TILL THE CAR PARKING AREA.",
]

DEL_T3_DOMESTIC_ARRIVAL_GOLD_FEATURES = [
    "WELCOME GUEST FROM AEROBRIDGE.",
    "DEDICATED STAFF WITH PLACARD.",
    "PORTER SERVICE WITH DEDICATED STAFF AT ARRIVALS.",
    "BUGGY SERVICE AVAILABLE. (AS PER THE AVAILABLITY).",
    "ASSIST IN BAGGAGE BELT AREA.",
    "LOUNGE ACCESS FOR 2 HOURS.",
    "ASSIST GUEST TILL THE CAR PARKING AREA.",
]

DEL_T3_DOMESTIC_ARRIVAL_ELITE_FEATURES = [
    "WELCOME GUEST FROM AEROBRIDGE.",
    "DEDICATED STAFF WITH PLACARD.",
    "PORTER SERVICE WITH DEDICATED STAFF AT ARRIVALS.",
    "BUGGY SERVICE AVAILABLE. (AS PER THE AVAILABLITY).",
    "ASSIST IN BAGGAGE BELT AREA.",
    "LOUNGE ACCESS FOR 2 HOURS.",
    "ASSIST GUEST TILL THE CAR PARKING AREA.",
]


def seed_del_production_packages(db: Session, del_airport: SupportedAirport, service_map: dict[str, Service]):
    """
    For Delhi Airport (DEL) Domestic Departure & Arrival (T1 & T2, T3), International Departure & Arrival (T3), and Transit:
    Seeds terminal-specific production packages:
    - Domestic Departure Terminal 1 & 2: Silver (₹3,000) and Elite (₹5,000)
    - Domestic Arrival Terminal 1 & 2: Silver (₹3,000) and Elite (₹5,000)
    - Domestic Departure Terminal 3: Silver (₹3,000), Gold (₹3,500), and Elite (₹5,000)
    - Domestic Arrival Terminal 3: Silver (₹3,000), Gold (₹3,500), and Elite (₹5,000)
    - International Departure Terminal 3: Silver (₹5,500), Gold (₹6,500), and Elite (₹7,000)
    - International Arrival Terminal 3: Silver (₹5,500), Gold (₹6,000), and Elite (₹7,000)
    - Transit: 4 Route Types (₹5,500, ₹7,500, ₹7,500, ₹9,500)
    """
    print("\n-- Configuring Production Packages for Delhi (DEL) (T1 & T2, T3 Dom Dep/Arr, T3 Intl Dep/Arr, Transit) --")

    # Remove old services mapped to DEL Domestic Departure, Domestic Arrival, Intl Departure T3, Intl Arrival T3, and Transit
    db.query(AirportService).filter_by(
        airport_id=del_airport.id,
        journey_type="DEPARTURE",
        flight_type="DOMESTIC",
    ).delete(synchronize_session=False)

    db.query(AirportService).filter_by(
        airport_id=del_airport.id,
        journey_type="ARRIVAL",
        flight_type="DOMESTIC",
    ).delete(synchronize_session=False)

    db.query(AirportService).filter_by(
        airport_id=del_airport.id,
        journey_type="DEPARTURE",
        flight_type="INTERNATIONAL",
        terminal="Terminal 3",
    ).delete(synchronize_session=False)

    db.query(AirportService).filter_by(
        airport_id=del_airport.id,
        journey_type="ARRIVAL",
        flight_type="INTERNATIONAL",
        terminal="Terminal 3",
    ).delete(synchronize_session=False)

    db.query(AirportService).filter_by(
        airport_id=del_airport.id,
        journey_type="TRANSIT",
    ).delete(synchronize_session=False)

    silver_svc = service_map.get("silver")
    gold_svc = service_map.get("gold")
    elite_svc = service_map.get("elite")

    # ── Domestic Departure: Terminal 1 & 2 Packages ──
    if silver_svc:
        db.add(AirportService(
            id=uuid.uuid4(),
            airport_id=del_airport.id,
            service_id=silver_svc.id,
            journey_type="DEPARTURE",
            flight_type="DOMESTIC",
            terminal="Terminal 1 & 2",
            short_description="Premium domestic departure assist from curbside to gate (Terminal 1 & 2).",
            features=[
                "Welcome at the Curbside Area",
                "Dedicated Porter Service",
                "Wheelchair Assist (through the airline, if required)",
                "Assist through the Separate Entry Gate",
                "Assist with Separate Baggage Check-in at the Airline Counter",
                "Assist inside the Security Hold Area (SHA)",
                "Escort to the Boarding Gate",
            ],
            additional_benefits=[],
            min_booking_notice_hours=6,
            is_available=True,
            display_priority=1,
            price=3000.00,
            currency="INR",
        ))

    if elite_svc:
        db.add(AirportService(
            id=uuid.uuid4(),
            airport_id=del_airport.id,
            service_id=elite_svc.id,
            journey_type="DEPARTURE",
            flight_type="DOMESTIC",
            terminal="Terminal 1 & 2",
            short_description="Complete premium domestic departure experience with flexible booking benefits (Terminal 1 & 2).",
            features=[
                "Welcome at the Curbside Area",
                "Dedicated Porter Service",
                "Wheelchair Assist (through the airline, if required)",
                "Assist through the Separate Entry Gate",
                "Assist with Separate Baggage Check-in at the Airline Counter",
                "Assist inside the Security Hold Area (SHA)",
                "Escort to the Boarding Gate",
            ],
            additional_benefits=[
                "Unlimited Rescheduling (with at least 12 hours' prior notice)",
                "Free Cancellation up to 12 hours before the scheduled service time",
            ],
            min_booking_notice_hours=6,
            is_available=True,
            display_priority=2,
            price=5000.00,
            currency="INR",
        ))

    # ── Domestic Arrival: Terminal 1 & 2 Packages ──
    if silver_svc:
        db.add(AirportService(
            id=uuid.uuid4(),
            airport_id=del_airport.id,
            service_id=silver_svc.id,
            journey_type="ARRIVAL",
            flight_type="DOMESTIC",
            terminal="Terminal 1 & 2",
            short_description="Premium domestic arrival assist from the aerobridge to the car parking area.",
            features=[
                "Welcome at the Aerobridge",
                "Dedicated Staff with Placard",
                "Dedicated Porter Service at Arrivals",
                "Assist at the Baggage Belt Area",
                "Escort to the Car Parking Area",
            ],
            additional_benefits=[],
            min_booking_notice_hours=6,
            is_available=True,
            display_priority=1,
            price=3000.00,
            currency="INR",
        ))

    if elite_svc:
        db.add(AirportService(
            id=uuid.uuid4(),
            airport_id=del_airport.id,
            service_id=elite_svc.id,
            journey_type="ARRIVAL",
            flight_type="DOMESTIC",
            terminal="Terminal 1 & 2",
            short_description="Complete premium domestic arrival assist with airport support and flexible booking benefits.",
            features=[
                "Welcome at the Aerobridge",
                "Dedicated Staff with Placard",
                "Dedicated Porter Service at Arrivals",
                "Assist at the Baggage Belt Area",
                "Escort to the Car Parking Area",
            ],
            additional_benefits=[
                "Unlimited Rescheduling (with at least 12 hours' prior notice)",
                "Free Cancellation up to 12 hours before the scheduled service time",
            ],
            min_booking_notice_hours=6,
            is_available=True,
            display_priority=2,
            price=5000.00,
            currency="INR",
        ))

    # ── Domestic Departure: Terminal 3 Packages ──
    if silver_svc:
        db.add(AirportService(
            id=uuid.uuid4(),
            airport_id=del_airport.id,
            service_id=silver_svc.id,
            journey_type="DEPARTURE",
            flight_type="DOMESTIC",
            terminal="Terminal 3",
            short_description="Premium domestic departure assist from curbside area to boarding gate (Terminal 3).",
            features=list(DEL_T3_DOMESTIC_DEPARTURE_SILVER_FEATURES),
            additional_benefits=[],
            min_booking_notice_hours=6,
            is_available=True,
            display_priority=1,
            price=3000.00,
            currency="INR",
        ))

    if gold_svc:
        db.add(AirportService(
            id=uuid.uuid4(),
            airport_id=del_airport.id,
            service_id=gold_svc.id,
            journey_type="DEPARTURE",
            flight_type="DOMESTIC",
            terminal="Terminal 3",
            short_description="Enhanced domestic departure assist with lounge access and airport support (Terminal 3).",
            features=list(DEL_T3_DOMESTIC_DEPARTURE_GOLD_FEATURES),
            additional_benefits=[],
            min_booking_notice_hours=6,
            is_available=True,
            display_priority=2,
            price=3500.00,
            currency="INR",
        ))

    if elite_svc:
        db.add(AirportService(
            id=uuid.uuid4(),
            airport_id=del_airport.id,
            service_id=elite_svc.id,
            journey_type="DEPARTURE",
            flight_type="DOMESTIC",
            terminal="Terminal 3",
            short_description="Complete premium domestic departure experience with lounge access and flexible booking benefits (Terminal 3).",
            features=list(DEL_T3_DOMESTIC_DEPARTURE_ELITE_FEATURES),
            additional_benefits=[],
            min_booking_notice_hours=6,
            is_available=True,
            display_priority=3,
            price=5000.00,
            currency="INR",
        ))

    # ── Domestic Arrival: Terminal 3 Packages ──
    if silver_svc:
        db.add(AirportService(
            id=uuid.uuid4(),
            airport_id=del_airport.id,
            service_id=silver_svc.id,
            journey_type="ARRIVAL",
            flight_type="DOMESTIC",
            terminal="Terminal 3",
            short_description="Premium domestic arrival assist from the aerobridge to the car parking area (Terminal 3).",
            features=list(DEL_T3_DOMESTIC_ARRIVAL_SILVER_FEATURES),
            additional_benefits=[],
            min_booking_notice_hours=6,
            is_available=True,
            display_priority=1,
            price=3000.00,
            currency="INR",
        ))

    if gold_svc:
        db.add(AirportService(
            id=uuid.uuid4(),
            airport_id=del_airport.id,
            service_id=gold_svc.id,
            journey_type="ARRIVAL",
            flight_type="DOMESTIC",
            terminal="Terminal 3",
            short_description="Enhanced domestic arrival assist with lounge access from the aerobridge to the car parking area (Terminal 3).",
            features=list(DEL_T3_DOMESTIC_ARRIVAL_GOLD_FEATURES),
            additional_benefits=[],
            min_booking_notice_hours=6,
            is_available=True,
            display_priority=2,
            price=3500.00,
            currency="INR",
        ))

    if elite_svc:
        db.add(AirportService(
            id=uuid.uuid4(),
            airport_id=del_airport.id,
            service_id=elite_svc.id,
            journey_type="ARRIVAL",
            flight_type="DOMESTIC",
            terminal="Terminal 3",
            short_description="Complete premium domestic arrival experience from the aerobridge to the car parking area (Terminal 3).",
            features=list(DEL_T3_DOMESTIC_ARRIVAL_ELITE_FEATURES),
            additional_benefits=[],
            min_booking_notice_hours=6,
            is_available=True,
            display_priority=3,
            price=5000.00,
            currency="INR",
        ))

    # ── International Departure: Terminal 3 Packages ──
    if silver_svc:
        db.add(AirportService(
            id=uuid.uuid4(),
            airport_id=del_airport.id,
            service_id=silver_svc.id,
            journey_type="DEPARTURE",
            flight_type="INTERNATIONAL",
            terminal="Terminal 3",
            short_description="Premium international departure assist from the curbside area to the boarding gate.",
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
                "Buggy Service to the Boarding Gate (subject to availability)",
                "Escort to the Boarding Gate",
            ],
            additional_benefits=[],
            min_booking_notice_hours=6,
            is_available=True,
            display_priority=1,
            price=5500.00,
            currency="INR",
        ))

    if gold_svc:
        db.add(AirportService(
            id=uuid.uuid4(),
            airport_id=del_airport.id,
            service_id=gold_svc.id,
            journey_type="DEPARTURE",
            flight_type="INTERNATIONAL",
            terminal="Terminal 3",
            short_description="Enhanced international departure assist with lounge access and premium airport support.",
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
                "Buggy Service to the Boarding Gate (subject to availability)",
                "Escort to the Boarding Gate",
            ],
            additional_benefits=[],
            min_booking_notice_hours=6,
            is_available=True,
            display_priority=2,
            price=6500.00,
            currency="INR",
        ))

    if elite_svc:
        db.add(AirportService(
            id=uuid.uuid4(),
            airport_id=del_airport.id,
            service_id=elite_svc.id,
            journey_type="DEPARTURE",
            flight_type="INTERNATIONAL",
            terminal="Terminal 3",
            short_description="Complete premium international departure experience with lounge access, flexible booking benefits, and airport assist.",
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
                "Buggy Service to the Boarding Gate (subject to availability)",
                "Escort to the Boarding Gate",
            ],
            additional_benefits=[
                "Unlimited Rescheduling (with at least 12 hours' prior notice)",
                "Free Cancellation up to 12 hours before the scheduled service time",
            ],
            min_booking_notice_hours=6,
            is_available=True,
            display_priority=3,
            price=7000.00,
            currency="INR",
        ))

    # ── International Arrival: Terminal 3 Packages ──
    if silver_svc:
        db.add(AirportService(
            id=uuid.uuid4(),
            airport_id=del_airport.id,
            service_id=silver_svc.id,
            journey_type="ARRIVAL",
            flight_type="INTERNATIONAL",
            terminal="Terminal 3",
            short_description="Premium international arrival assist from the aerobridge to the car parking area.",
            features=[
                "Welcome at the Aerobridge",
                "Dedicated Staff with Placard",
                "Dedicated Porter Service at Arrivals",
                "Buggy Service from the End of the Aerobridge (Sharing Basis, subject to availability)",
                "Assist through Immigration",
                "Assist at the Duty Free Shop",
                "Assist at the Baggage Belt Area",
                "Assist through Customs Clearance",
                "Escort to the Car Parking Area",
            ],
            additional_benefits=[],
            min_booking_notice_hours=6,
            is_available=True,
            display_priority=1,
            price=5500.00,
            currency="INR",
        ))

    if gold_svc:
        db.add(AirportService(
            id=uuid.uuid4(),
            airport_id=del_airport.id,
            service_id=gold_svc.id,
            journey_type="ARRIVAL",
            flight_type="INTERNATIONAL",
            terminal="Terminal 3",
            short_description="Enhanced international arrival assist with lounge access and premium airport support.",
            features=[
                "Welcome at the Aerobridge",
                "Dedicated Staff with Placard",
                "Dedicated Porter Service at Arrivals",
                "Buggy Service from the End of the Aerobridge (Sharing Basis, subject to availability)",
                "Assist through Immigration",
                "Assist at the Duty Free Shop",
                "Assist at the Baggage Belt Area",
                "Complimentary Lounge Access (up to 2 hours)",
                "Assist through Customs Clearance",
                "Escort to the Car Parking Area",
            ],
            additional_benefits=[],
            min_booking_notice_hours=6,
            is_available=True,
            display_priority=2,
            price=6000.00,
            currency="INR",
        ))

    if elite_svc:
        db.add(AirportService(
            id=uuid.uuid4(),
            airport_id=del_airport.id,
            service_id=elite_svc.id,
            journey_type="ARRIVAL",
            flight_type="INTERNATIONAL",
            terminal="Terminal 3",
            short_description="Complete premium international arrival experience with lounge access, flexible booking benefits, and airport assist.",
            features=[
                "Welcome at the Aerobridge",
                "Dedicated Staff with Placard",
                "Dedicated Porter Service at Arrivals",
                "Buggy Service from the End of the Aerobridge (Sharing Basis, subject to availability)",
                "Assist through Immigration",
                "Assist at the Duty Free Shop",
                "Assist at the Baggage Belt Area",
                "Complimentary Lounge Access (up to 2 hours)",
                "Assist through Customs Clearance",
                "Escort to the Car Parking Area",
            ],
            additional_benefits=[
                "Unlimited Rescheduling (with at least 12 hours' prior notice)",
                "Free Cancellation up to 12 hours before the scheduled service time",
            ],
            min_booking_notice_hours=6,
            is_available=True,
            display_priority=3,
            price=7000.00,
            currency="INR",
        ))

    # ── Transit Packages ──
    transit_service = service_map.get("meet_greet") or silver_svc

    if transit_service:
        # 1. Domestic → Domestic
        db.add(AirportService(
            id=uuid.uuid4(),
            airport_id=del_airport.id,
            service_id=transit_service.id,
            journey_type="TRANSIT",
            flight_type="DOMESTIC_DOMESTIC",
            short_description="Premium domestic transit assist between connecting domestic flights.",
            features=[
                "WELCOME GUEST FROM AEROBRIDGE/BUS GATE.",
                "BAGGAGE ASSISTANT FOR BAGGAGE.",
                "BUGGY SERVICE AVIALABLE (ONLY AT T3).",
                "ASSIST IN BAGGAGE BELT AREA (IF REQUIRED).",
                "ASSIST IN TERMINAL CHANGE (T2-T3) (IF REQUIRED).",
                "ASSIST IN AIRLINE COUNTERS.",
                "ASSIST IN S.H.A (SECURITY HOLD AREA).",
                "ASSIST TILL BOARDING AREA.",
            ],
            additional_benefits=[],
            min_booking_notice_hours=6,
            is_available=True,
            display_priority=1,
            price=5500.00,
            currency="INR",
        ))

        # 2. Domestic → International
        db.add(AirportService(
            id=uuid.uuid4(),
            airport_id=del_airport.id,
            service_id=transit_service.id,
            journey_type="TRANSIT",
            flight_type="DOMESTIC_INTERNATIONAL",
            short_description="Premium transit assist for passengers connecting from a domestic flight to an international flight.",
            features=[
                "WELCOME GUEST FROM AEROBRIDGE .",
                "BAGGAGE ASSISTANT FOR BAGGAGE.",
                "BUGGY SERVICE AVIALABLE (ONLY AT T3).",
                "ASSIST IN BAGGAGE BELT AREA (IF REQUIRED).",
                "ASSIST IN TERMINAL CHANGE (T2-T3) (IF REQUIRED).",
                "ASSIST IN AIRLINE COUNTERS.",
                "GUIDANCE FOR IMMIGRATION COUNTERS",
                "ASSIST IN S.H.A (SECURITY HOLD AREA).",
                "ASSIST TILL BOARDING AREA.",
            ],
            additional_benefits=[],
            min_booking_notice_hours=6,
            is_available=True,
            display_priority=2,
            price=7500.00,
            currency="INR",
        ))

        # 3. International → Domestic
        db.add(AirportService(
            id=uuid.uuid4(),
            airport_id=del_airport.id,
            service_id=transit_service.id,
            journey_type="TRANSIT",
            flight_type="INTERNATIONAL_DOMESTIC",
            short_description="Premium transit assist for passengers connecting from an international flight to a domestic flight.",
            features=[
                "WELCOME GUEST FROM AEROBRIDGE .",
                "BAGGAGE ASSISTANT FOR BAGGAGE.",
                "BUGGY SERVICE AVIALABLE (ONLY AT T3).",
                "ASSIST IN BAGGAGE BELT AREA.",
                "ASSIST IN TERMINAL CHANGE (T3-T2) IF REQUIRED",
                "ASSIST IN AIRLINE COUNTERS.",
                "ASSIST IN S.H.A (SECURITY HOLD AREA).",
                "ASSIST TILL BOARDING AREA.",
            ],
            additional_benefits=[],
            min_booking_notice_hours=6,
            is_available=True,
            display_priority=3,
            price=7500.00,
            currency="INR",
        ))

        # 4. International → International
        db.add(AirportService(
            id=uuid.uuid4(),
            airport_id=del_airport.id,
            service_id=transit_service.id,
            journey_type="TRANSIT",
            flight_type="INTERNATIONAL_INTERNATIONAL",
            short_description="Premium international-to-international transit assist.",
            features=[
                "WELCOME GUEST FROM AEROBRIDGE .",
                "BAGGAGE ASSISTANT FOR BAGGAGE.",
                "BUGGY SERVICE AVIALABLE..",
                "ASSIST IN AIRLINE COUNTERS.(IN TRANSIT AREA)",
                "ASSIST IN S.H.A (SECURITY HOLD AREA).",
                "ASSIST TILL BOARDING AREA.",
            ],
            additional_benefits=[],
            min_booking_notice_hours=6,
            is_available=True,
            display_priority=4,
            price=9500.00,
            currency="INR",
        ))

    db.flush()
    print("  + Created DEL Production Packages for T1 & T2 (Departure & Arrival), T3 (Domestic Dep, Intl Dep & Intl Arr), and Transit")
