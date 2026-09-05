import uuid
from sqlalchemy.orm import Session
from app.models.journey_models import SupportedAirport, Service, AirportService


def seed_bom_production_packages(db: Session, bom_airport: SupportedAirport, service_map: dict[str, Service]):
    """
    For Mumbai Airport (BOM) Domestic Departure & Domestic Arrival:
    Seeds real production packages:
    - Domestic Departure: Platinum (₹3,850), Elite (₹4,950), Elite Plus (₹7,040)
    - Domestic Arrival: Platinum (₹3,600), Elite (₹4,950), Elite Plus (₹7,040)
    """
    print("\n-- Configuring Production Packages for Mumbai (BOM) Domestic Departure & Domestic Arrival --")

    # 1. Clear all existing services mapped to BOM to avoid duplicates
    db.query(AirportService).filter_by(
        airport_id=bom_airport.id,
    ).delete(synchronize_session=False)

    plat_svc = service_map.get("platinum")
    elite_svc = service_map.get("elite")
    elite_plus_svc = service_map.get("elite_plus")

    # ── DOMESTIC DEPARTURE PACKAGES ──
    # 1. Platinum Service (₹3,850)
    if plat_svc:
        db.add(AirportService(
            id=uuid.uuid4(),
            airport_id=bom_airport.id,
            service_id=plat_svc.id,
            journey_type="DEPARTURE",
            flight_type="DOMESTIC",
            short_description="Premium domestic departure assist from the curbside area to the boarding gate.",
            features=[
                "Welcome at the Curbside Area",
                "Dedicated Porter Service",
                "Wheelchair Assist (through the airline)",
                "Assist through the Separate Entry Gate",
                "Assist with Separate Baggage Check-in at the Airline Counter",
                "Assist inside the Security Hold Area (SHA)",
                "Buggy Service to the Boarding Gate (Sharing Basis, subject to availability)",
                "Escort to the Boarding Gate",
            ],
            additional_benefits=[],
            min_booking_notice_hours=6,
            is_available=True,
            display_priority=1,
            price=3850.00,
            currency="INR",
        ))

    # 2. Elite Service (₹4,950)
    if elite_svc:
        db.add(AirportService(
            id=uuid.uuid4(),
            airport_id=bom_airport.id,
            service_id=elite_svc.id,
            journey_type="DEPARTURE",
            flight_type="DOMESTIC",
            short_description="Enhanced domestic departure assist with lounge access and dedicated airport support.",
            features=[
                "Welcome at the Curbside Area",
                "Dedicated Porter Service",
                "Wheelchair Assist (through the airline)",
                "Assist through the Separate Entry Gate",
                "Assist with Separate Check-in at the Airline Counters",
                "Assist inside the Security Hold Area (SHA)",
                "Lounge Service Facility",
                "Buggy Service to the Boarding Gate (subject to availability)",
                "Escort to the Boarding Gate",
            ],
            additional_benefits=[],
            min_booking_notice_hours=6,
            is_available=True,
            display_priority=2,
            price=4950.00,
            currency="INR",
        ))

    # 3. Elite Plus Service (₹7,040)
    if elite_plus_svc:
        db.add(AirportService(
            id=uuid.uuid4(),
            airport_id=bom_airport.id,
            service_id=elite_plus_svc.id,
            journey_type="DEPARTURE",
            flight_type="DOMESTIC",
            short_description="Complete premium domestic departure assist with lounge access and flexible booking benefits.",
            features=[
                "Welcome at the Curbside Area",
                "Dedicated Porter Service",
                "Wheelchair Assist (through the airline)",
                "Assist through the Separate Entry Gate",
                "Assist with Separate Check-in at the Airline Counters",
                "Assist inside the Security Hold Area (SHA)",
                "Lounge Service Facility",
                "Buggy Service to the Boarding Gate (subject to availability)",
                "Escort to the Boarding Gate",
            ],
            additional_benefits=[
                "Free cancellation up to 12 hours before the scheduled service time",
                "One-time rescheduling with a minimum of 6 hours' prior notice",
            ],
            min_booking_notice_hours=6,
            is_available=True,
            display_priority=3,
            price=7040.00,
            currency="INR",
        ))

    # ── DOMESTIC ARRIVAL PACKAGES ──
    # 1. Platinum Service (₹3,600)
    if plat_svc:
        db.add(AirportService(
            id=uuid.uuid4(),
            airport_id=bom_airport.id,
            service_id=plat_svc.id,
            journey_type="ARRIVAL",
            flight_type="DOMESTIC",
            short_description="Premium domestic arrival assist from the end of the aerobridge to the car parking area.",
            features=[
                "Welcome at the End of the Aerobridge",
                "Dedicated Staff with Placard",
                "Dedicated Porter Service at Arrivals",
                "Buggy Service from the End of the Aerobridge (Sharing Basis, subject to availability)",
                "Wheelchair Assist (through the airline)",
                "Assist at the Baggage Belt Area",
                "Escort to the Car Parking Area",
            ],
            additional_benefits=[],
            min_booking_notice_hours=6,
            is_available=True,
            display_priority=1,
            price=3600.00,
            currency="INR",
        ))

    # 2. Elite Service (₹4,950)
    if elite_svc:
        db.add(AirportService(
            id=uuid.uuid4(),
            airport_id=bom_airport.id,
            service_id=elite_svc.id,
            journey_type="ARRIVAL",
            flight_type="DOMESTIC",
            short_description="Enhanced domestic arrival assist with dedicated airport support from the aerobridge to the car parking area.",
            features=[
                "Welcome at the End of the Aerobridge",
                "Dedicated Staff with Placard",
                "Dedicated Porter Service at Arrivals",
                "Buggy Service from the End of the Aerobridge (subject to availability)",
                "Wheelchair Assist (through the airline)",
                "Assist at the Baggage Belt Area",
                "Escort to the Car Parking Area",
            ],
            additional_benefits=[],
            min_booking_notice_hours=6,
            is_available=True,
            display_priority=2,
            price=4950.00,
            currency="INR",
        ))

    # 3. Elite Plus Service (₹7,040)
    if elite_plus_svc:
        db.add(AirportService(
            id=uuid.uuid4(),
            airport_id=bom_airport.id,
            service_id=elite_plus_svc.id,
            journey_type="ARRIVAL",
            flight_type="DOMESTIC",
            short_description="Complete premium domestic arrival assist with flexible booking benefits.",
            features=[
                "Welcome at the End of the Aerobridge",
                "Dedicated Staff with Placard",
                "Dedicated Porter Service at Arrivals",
                "Buggy Service from the End of the Aerobridge (subject to availability)",
                "Wheelchair Assist (through the airline)",
                "Assist at the Baggage Belt Area",
                "Escort to the Car Parking Area",
            ],
            additional_benefits=[
                "Free cancellation up to 12 hours before the scheduled service time",
                "One-time rescheduling with a minimum of 6 hours' prior notice",
            ],
            min_booking_notice_hours=6,
            is_available=True,
            display_priority=3,
            price=7040.00,
            currency="INR",
        ))

    # ── INTERNATIONAL DEPARTURE PACKAGES ──
    # 1. Platinum Service (₹7,700)
    if plat_svc:
        db.add(AirportService(
            id=uuid.uuid4(),
            airport_id=bom_airport.id,
            service_id=plat_svc.id,
            journey_type="DEPARTURE",
            flight_type="INTERNATIONAL",
            short_description="Premium international departure assist from the curbside area to the boarding gate.",
            features=[
                "Welcome at the Curbside Area",
                "Dedicated Porter Service",
                "Wheelchair Assist (through the airline)",
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
            price=7700.00,
            currency="INR",
        ))

    # 2. Elite Service (₹9,000)
    if elite_svc:
        db.add(AirportService(
            id=uuid.uuid4(),
            airport_id=bom_airport.id,
            service_id=elite_svc.id,
            journey_type="DEPARTURE",
            flight_type="INTERNATIONAL",
            short_description="Enhanced international departure assist with lounge access and dedicated airport support.",
            features=[
                "Welcome at the Curbside Area",
                "Dedicated Porter Service",
                "Wheelchair Assist (through the airline)",
                "Assist through the Separate Entry Gate",
                "Assist at the Money Exchange Counter",
                "Assist with Baggage Wrapping Facilities",
                "Assist with Separate Baggage Check-in at the Airline Counter",
                "Assist through Immigration",
                "Assist inside the Security Hold Area (SHA)",
                "Lounge Service Facility",
                "Buggy Service to the Boarding Gate (subject to availability)",
                "Escort to the Boarding Gate",
            ],
            additional_benefits=[],
            min_booking_notice_hours=6,
            is_available=True,
            display_priority=2,
            price=9000.00,
            currency="INR",
        ))

    # 3. Elite Plus Service (₹13,500)
    if elite_plus_svc:
        db.add(AirportService(
            id=uuid.uuid4(),
            airport_id=bom_airport.id,
            service_id=elite_plus_svc.id,
            journey_type="DEPARTURE",
            flight_type="INTERNATIONAL",
            short_description="Complete premium international departure assist with lounge access and flexible booking benefits.",
            features=[
                "Welcome at the Curbside Area",
                "Dedicated Porter Service",
                "Wheelchair Assist (through the airline)",
                "Assist through the Separate Entry Gate",
                "Assist at the Money Exchange Counter",
                "Assist with Baggage Wrapping Facilities",
                "Assist with Separate Baggage Check-in at the Airline Counter",
                "Assist through Immigration",
                "Assist inside the Security Hold Area (SHA)",
                "Lounge Service Facility",
                "Buggy Service to the Boarding Gate (subject to availability)",
                "Escort to the Boarding Gate",
            ],
            additional_benefits=[
                "Free cancellation up to 12 hours before the scheduled service time",
                "One-time rescheduling with a minimum of 6 hours' prior notice",
            ],
            min_booking_notice_hours=6,
            is_available=True,
            display_priority=3,
            price=13500.00,
            currency="INR",
        ))

    # ── INTERNATIONAL ARRIVAL PACKAGES ──
    # 1. Platinum Service (₹8,690)
    if plat_svc:
        db.add(AirportService(
            id=uuid.uuid4(),
            airport_id=bom_airport.id,
            service_id=plat_svc.id,
            journey_type="ARRIVAL",
            flight_type="INTERNATIONAL",
            short_description="Premium international arrival assist from the aerobridge to the car parking area.",
            features=[
                "Welcome at the Aerobridge",
                "Dedicated Staff with Placard",
                "Dedicated Porter Service at Arrivals",
                "Buggy Service from the End of the Aerobridge (Sharing Basis, subject to availability)",
                "Assist through the Immigration Counter",
                "Assist at the Duty Free Shop",
                "Assist at the Baggage Belt Area",
                "Assist through Customs",
                "Escort to the Car Parking Area",
            ],
            additional_benefits=[],
            min_booking_notice_hours=6,
            is_available=True,
            display_priority=1,
            price=8690.00,
            currency="INR",
        ))

    # 2. Elite Service (₹9,900)
    if elite_svc:
        db.add(AirportService(
            id=uuid.uuid4(),
            airport_id=bom_airport.id,
            service_id=elite_svc.id,
            journey_type="ARRIVAL",
            flight_type="INTERNATIONAL",
            short_description="Enhanced international arrival assist with dedicated airport support from the aerobridge to the car parking area.",
            features=[
                "Welcome at the Aerobridge",
                "Dedicated Staff with Placard",
                "Dedicated Porter Service at Arrivals",
                "Buggy Service from the End of the Aerobridge (subject to availability)",
                "Assist through the Immigration Counter",
                "Assist at the Duty Free Shop",
                "Assist at the Baggage Belt Area",
                "Assist through Customs",
                "Escort to the Car Parking Area",
            ],
            additional_benefits=[],
            min_booking_notice_hours=6,
            is_available=True,
            display_priority=2,
            price=9900.00,
            currency="INR",
        ))

    # 3. Elite Plus Service (₹13,500)
    if elite_plus_svc:
        db.add(AirportService(
            id=uuid.uuid4(),
            airport_id=bom_airport.id,
            service_id=elite_plus_svc.id,
            journey_type="ARRIVAL",
            flight_type="INTERNATIONAL",
            short_description="Complete premium international arrival assist with flexible booking benefits.",
            features=[
                "Welcome at the Aerobridge",
                "Dedicated Staff with Placard",
                "Dedicated Porter Service at Arrivals",
                "Buggy Service from the Aerobridge (subject to availability)",
                "Assist at the Baggage Belt Area",
                "Assist through the Immigration Counter",
                "Assist at the Duty Free Shop",
                "Escort to the Parking Area",
            ],
            additional_benefits=[
                "Free cancellation up to 12 hours before the scheduled service time",
                "One-time rescheduling with a minimum of 6 hours' prior notice",
            ],
            min_booking_notice_hours=6,
            display_priority=3,
            price=13500.00,
            currency="INR",
        ))

    # ── TRANSIT PACKAGES ──
    transit_svc = service_map.get("meet_greet") or plat_svc

    if transit_svc:
        # 1. Domestic → Domestic (₹7,150)
        db.add(AirportService(
            id=uuid.uuid4(),
            airport_id=bom_airport.id,
            service_id=transit_svc.id,
            journey_type="TRANSIT",
            flight_type="DOMESTIC_DOMESTIC",
            short_description="Domestic transit assist from arrival through the connecting flight boarding gate.",
            features=[
                "Welcome at the Aerobridge",
                "Dedicated Staff with Placard",
                "Dedicated Porter Service at Arrivals",
                "Wheelchair Assist (through the airline)",
                "Buggy Service from the End of the Aerobridge",
                "Assist inside the Security Hold Area (Transit Area)",
                "Lounge Access for up to 2 hours (Departure only)",
                "Buggy Service to the Boarding Gate (subject to availability)",
                "Escort to the Boarding Gate",
            ],
            additional_benefits=[],
            min_booking_notice_hours=6,
            is_available=False,
            display_priority=1,
            price=7150.00,
            currency="INR",
        ))

        # 2. Domestic → International (₹9,000 - DRAFT / INACTIVE)
        db.add(AirportService(
            id=uuid.uuid4(),
            airport_id=bom_airport.id,
            service_id=transit_svc.id,
            journey_type="TRANSIT",
            flight_type="DOMESTIC_INTERNATIONAL",
            short_description="Domestic to international transit assist.",
            features=[],
            additional_benefits=[],
            min_booking_notice_hours=6,
            is_available=False,  # DRAFT / INACTIVE
            display_priority=2,
            price=9000.00,
            currency="INR",
        ))

        # 3. International → Domestic (₹9,000)
        db.add(AirportService(
            id=uuid.uuid4(),
            airport_id=bom_airport.id,
            service_id=transit_svc.id,
            journey_type="TRANSIT",
            flight_type="INTERNATIONAL_DOMESTIC",
            short_description="International-to-domestic transit assist from arrival through the connecting domestic boarding gate.",
            features=[
                "Welcome at the Aerobridge",
                "Dedicated Staff with Placard",
                "Dedicated Porter Service at Arrivals",
                "Buggy Service from the End of the Aerobridge",
                "Wheelchair Assist (through the airline)",
                "Guidance to the Immigration Counter",
                "Assist at the Baggage Belt Area",
                "Assist with Separate Check-in at the Airline Counters",
                "Assist inside the Security Hold Area (SHA)",
                "Lounge Access for up to 2 hours (Departure only)",
                "Buggy Service to the Boarding Gate (subject to availability)",
                "Escort to the Boarding Gate",
            ],
            additional_benefits=[],
            min_booking_notice_hours=6,
            is_available=False,
            display_priority=3,
            price=9000.00,
            currency="INR",
        ))

        # 4. International → International (₹10,000)
        db.add(AirportService(
            id=uuid.uuid4(),
            airport_id=bom_airport.id,
            service_id=transit_svc.id,
            journey_type="TRANSIT",
            flight_type="INTERNATIONAL_INTERNATIONAL",
            short_description="International transit assist from the arriving flight through the airport transit process to the next connecting flight.",
            features=[
                "Warm welcome at the Aerobridge or Bus Gate by a porter",
                "Dedicated porter assist from the Aerobridge on arrival to the boarding gate of the next connecting flight",
                "Guidance through the airport and airline transit process",
                "Facilitation through security according to the passenger's class of travel",
                "Adani Lounge access with snacks, food, and non-alcoholic beverages",
                "Golf cart transfer to the lounge or boarding gate, subject to the boarding gate location",
            ],
            additional_benefits=[],
            min_booking_notice_hours=6,
            is_available=False,
            display_priority=4,
            price=10000.00,
            currency="INR",
        ))

    db.flush()
    print("  + Created BOM Production Packages: Domestic & International Departure & Arrival + Transit")
