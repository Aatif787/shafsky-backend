import uuid
from sqlalchemy.orm import Session
from app.models.journey_models import SupportedAirport, Service, AirportService


def seed_jai_production_packages(db: Session, jai_airport: SupportedAirport, service_map: dict[str, Service]):
    """
    For Jaipur International Airport (JAI):
    Seeds production packages:
    - Domestic Departure: Platinum Service (₹2,420), Elite Service (₹4,400)
    - Domestic Arrival: Platinum Service (₹2,420), Elite Service (₹4,400)
    """
    print("\n-- Configuring Production Packages for Jaipur International Airport (JAI) --")

    # 1. Clear all existing services mapped to JAI to avoid duplicates
    db.query(AirportService).filter_by(
        airport_id=jai_airport.id,
    ).delete(synchronize_session=False)

    plat_svc = service_map.get("platinum")
    elite_svc = service_map.get("elite")

    # ── 1. DOMESTIC DEPARTURE PACKAGES ──
    # Platinum Service (₹2,420)
    if plat_svc:
        db.add(AirportService(
            id=uuid.uuid4(),
            airport_id=jai_airport.id,
            service_id=plat_svc.id,
            journey_type="DEPARTURE",
            flight_type="DOMESTIC",
            short_description="Premium domestic departure assist from the curbside area to the boarding gate.",
            features=[
                "Welcome at the Curbside Area",
                "Dedicated Porter Service",
                "Wheelchair Assist (through the airline)",
                "Assist through the Separate Entry Gate",
                "Assist with Baggage Wrapping Facilities",
                "Assist with Separate Baggage Check-in at the Airline Counter",
                "Assist inside the Security Hold Area (SHA)",
                "Escort to the Boarding Gate",
            ],
            additional_benefits=[],
            min_booking_notice_hours=6,
            is_available=True,
            display_priority=1,
            price=2420.00,
            currency="INR",
        ))

    # Elite Service (₹4,400)
    if elite_svc:
        db.add(AirportService(
            id=uuid.uuid4(),
            airport_id=jai_airport.id,
            service_id=elite_svc.id,
            journey_type="DEPARTURE",
            flight_type="DOMESTIC",
            short_description="Complete premium domestic departure assist with lounge service and flexible booking benefits.",
            features=[
                "Welcome at the Curbside Area",
                "Dedicated Porter Service",
                "Wheelchair Assist (through the airline)",
                "Assist through the Separate Entry Gate",
                "Assist with Baggage Wrapping Facilities",
                "Assist at the Separate Check-in Process at the Counters",
                "Assist inside the Security Hold Area (SHA)",
                "Lounge Service Facility",
                "Escort to the Boarding Gate",
            ],
            additional_benefits=[
                "Cancellation benefits up to 12 hours before the scheduled service time",
                "Minimum 6 hours' prior notice required for rescheduling",
            ],
            min_booking_notice_hours=6,
            is_available=True,
            display_priority=2,
            price=4400.00,
            currency="INR",
        ))

    # ── 2. DOMESTIC ARRIVAL PACKAGES ──
    # Platinum Service (₹2,420)
    if plat_svc:
        db.add(AirportService(
            id=uuid.uuid4(),
            airport_id=jai_airport.id,
            service_id=plat_svc.id,
            journey_type="ARRIVAL",
            flight_type="DOMESTIC",
            short_description="Premium domestic arrival assist from the aerobridge to the car parking area.",
            features=[
                "Welcome at the Aerobridge",
                "Dedicated Staff with Placard",
                "Dedicated Porter Service at Arrivals",
                "Wheelchair Assist (through the airline)",
                "Assist at the Baggage Belt Area",
                "Escort to the Car Parking Area",
            ],
            additional_benefits=[],
            min_booking_notice_hours=6,
            is_available=True,
            display_priority=1,
            price=2420.00,
            currency="INR",
        ))

    # Elite Service (₹4,400)
    if elite_svc:
        db.add(AirportService(
            id=uuid.uuid4(),
            airport_id=jai_airport.id,
            service_id=elite_svc.id,
            journey_type="ARRIVAL",
            flight_type="DOMESTIC",
            short_description="Premium domestic arrival assist with dedicated airport support from the aerobridge to the car parking area.",
            features=[
                "Welcome at the Aerobridge",
                "Dedicated Staff with Placard",
                "Dedicated Porter Service at Arrivals",
                "Wheelchair Assist (through the airline)",
                "Assist at the Baggage Belt Area",
                "Escort to the Car Parking Area",
            ],
            additional_benefits=[],
            min_booking_notice_hours=6,
            is_available=True,
            display_priority=2,
            price=4400.00,
            currency="INR",
        ))

    # ── 3. INTERNATIONAL DEPARTURE PACKAGES ──
    # 1. Platinum Service (₹3,300)
    if plat_svc:
        db.add(AirportService(
            id=uuid.uuid4(),
            airport_id=jai_airport.id,
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
                "Escort to the Boarding Gate",
            ],
            additional_benefits=[],
            min_booking_notice_hours=6,
            is_available=True,
            display_priority=1,
            price=3300.00,
            currency="INR",
        ))

    # 2. Elite Service (₹4,950)
    if elite_svc:
        db.add(AirportService(
            id=uuid.uuid4(),
            airport_id=jai_airport.id,
            service_id=elite_svc.id,
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
                "Lounge Service Facility (up to 2 hours)",
                "Escort to the Boarding Gate",
            ],
            additional_benefits=[
                "Cancellation benefits up to 12 hours before the scheduled service time",
                "Minimum 6 hours' prior notice required for rescheduling",
            ],
            min_booking_notice_hours=6,
            is_available=True,
            display_priority=2,
            price=4950.00,
            currency="INR",
        ))

    # ── 4. INTERNATIONAL ARRIVAL PACKAGES ──
    # 1. Platinum Service (₹2,750)
    if plat_svc:
        db.add(AirportService(
            id=uuid.uuid4(),
            airport_id=jai_airport.id,
            service_id=plat_svc.id,
            journey_type="ARRIVAL",
            flight_type="INTERNATIONAL",
            short_description="Premium international arrival assist from post-immigration through customs to the car parking area.",
            features=[
                "Welcome after Immigration",
                "Assist at the Duty Free Shop",
                "Assist at the Baggage Belt Area",
                "Assist after Customs",
                "Coordination with the Receiving Person",
                "Escort to the Car Parking Area",
            ],
            additional_benefits=[],
            min_booking_notice_hours=6,
            is_available=True,
            display_priority=1,
            price=2750.00,
            currency="INR",
        ))

    db.flush()
    print("  + Created JAI Production Packages: Domestic & International Departure & Arrival")
