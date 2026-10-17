import uuid
from sqlalchemy.orm import Session
from app.models.journey_models import SupportedAirport, Service, AirportService


def seed_amd_production_packages(db: Session, amd_airport: SupportedAirport, service_map: dict[str, Service]):
    """
    For Ahmedabad Airport (AMD):
    Seeds production Domestic Departure, Domestic Arrival, International Departure, and International Arrival packages.
    """
    print("\n-- Configuring Production Packages for Ahmedabad (AMD) --")

    plat_svc = service_map.get("platinum")
    elite_svc = service_map.get("elite")

    if not plat_svc or not elite_svc:
        print("  ! Error: Platinum or Elite service not found in service_map")
        return

    # 1. Domestic Departure
    db.query(AirportService).filter_by(
        airport_id=amd_airport.id,
        journey_type="DEPARTURE",
        flight_type="DOMESTIC",
    ).delete(synchronize_session=False)

    # Package 1: Platinum Service (₹2,420)
    db.add(AirportService(
        id=uuid.uuid4(),
        airport_id=amd_airport.id,
        service_id=plat_svc.id,
        journey_type="DEPARTURE",
        flight_type="DOMESTIC",
        short_description="Premium airport departure assist with dedicated baggage support and gate assistance.",
        features=[
            "Welcome at the Curbside Area",
            "Dedicated Porter Service (Up to 3 pieces)",
            "Assist through the Separate Entry Gate",
            "Assist with Baggage Check-in at the Airline Counter",
            "Assist inside the Security Hold Area (SHA)",
            "ASSIST GUEST TILL THE BOARDING GATE",
        ],
        additional_benefits=[],
        min_booking_notice_hours=4,
        is_available=True,
        display_priority=1,
        price=2420.00,
        currency="INR",
    ))

    # Package 2: Elite Service (₹4,400)
    db.add(AirportService(
        id=uuid.uuid4(),
        airport_id=amd_airport.id,
        service_id=elite_svc.id,
        journey_type="DEPARTURE",
        flight_type="DOMESTIC",
        short_description="Complete airport departure experience with lounge access, baggage support, and gate assistance.",
        features=[
            "Welcome at the Curbside Area",
            "Dedicated Porter Service (Up to 3 pieces)",
            "Assist through the Separate Entry Gate",
            "Assist with Baggage Check-in at the Airline Counter",
            "Assist inside the Security Hold Area (SHA)",
            "Complimentary Lounge Access (up to 2 hours)",
            "ASSIST GUEST TILL THE BOARDING GATE",
        ],
        additional_benefits=[
            "Free cancellation up to 12 hours before the scheduled service time.",
            "A minimum of 6 hours' notice is required for rescheduling.",
        ],
        min_booking_notice_hours=4,
        is_available=True,
        display_priority=2,
        price=4400.00,
        currency="INR",
    ))

    # 2. Domestic Arrival
    db.query(AirportService).filter_by(
        airport_id=amd_airport.id,
        journey_type="ARRIVAL",
        flight_type="DOMESTIC",
    ).delete(synchronize_session=False)

    # Package 1: Platinum Service (₹2,420)
    db.add(AirportService(
        id=uuid.uuid4(),
        airport_id=amd_airport.id,
        service_id=plat_svc.id,
        journey_type="ARRIVAL",
        flight_type="DOMESTIC",
        short_description="Premium airport arrival assist with baggage support and meet & greet.",
        features=[
            "Welcome at the Aerobridge",
            "Dedicated Staff with Placard",
            "Dedicated Porter Service (Up to 3 pieces)",
            "Assist at the Baggage Belt Area",
            "Coordination with the Receiving Party",
            "ASSIST GUEST TILL THE CAR PARKING AREA",
        ],
        additional_benefits=[],
        min_booking_notice_hours=4,
        is_available=True,
        display_priority=1,
        price=2420.00,
        currency="INR",
    ))

    # Package 2: Elite Service (₹4,400)
    db.add(AirportService(
        id=uuid.uuid4(),
        airport_id=amd_airport.id,
        service_id=elite_svc.id,
        journey_type="ARRIVAL",
        flight_type="DOMESTIC",
        short_description="VIP airport arrival assist with full luggage handling and priority receiving coordination.",
        features=[
            "Welcome at the Aerobridge",
            "Dedicated Staff with Placard",
            "Dedicated Porter Service (Full luggage handling)",
            "Assist at the Baggage Belt Area",
            "Priority Coordination with the Receiving Party",
            "ASSIST GUEST TILL THE CAR PARKING AREA",
        ],
        additional_benefits=[],
        min_booking_notice_hours=4,
        is_available=True,
        display_priority=2,
        price=4400.00,
        currency="INR",
    ))

    # 3. International Departure
    db.query(AirportService).filter_by(
        airport_id=amd_airport.id,
        journey_type="DEPARTURE",
        flight_type="INTERNATIONAL",
    ).delete(synchronize_session=False)

    # Package 1: Platinum Service (₹3,300)
    db.add(AirportService(
        id=uuid.uuid4(),
        airport_id=amd_airport.id,
        service_id=plat_svc.id,
        journey_type="DEPARTURE",
        flight_type="INTERNATIONAL",
        short_description="Premium international departure assist from airport arrival to the boarding gate.",
        features=[
            "Welcome at the Curbside Area",
            "Dedicated Porter Service",
            "Wheelchair Assist (through the airline, if required)",
            "Assist through the Separate Entry Gate",
            "Assist at the Money Exchange Counter",
            "Assist with Baggage Wrapping Facilities",
            "Assist with Baggage Check-in at the Airline Counter",
            "Assist through Immigration",
            "Assist inside the Security Hold Area (SHA)",
            "ASSIST GUEST TILL THE BOARDING GATE",
        ],
        additional_benefits=[],
        min_booking_notice_hours=6,
        is_available=True,
        display_priority=1,
        price=3300.00,
        currency="INR",
    ))

    # Package 2: Elite Service (₹4,950)
    db.add(AirportService(
        id=uuid.uuid4(),
        airport_id=amd_airport.id,
        service_id=elite_svc.id,
        journey_type="DEPARTURE",
        flight_type="INTERNATIONAL",
        short_description="Complete premium international departure assist with lounge access and enhanced passenger support.",
        features=[
            "Welcome at the Curbside Area",
            "Dedicated Porter Service",
            "Wheelchair Assist (through the airline, if required)",
            "Assist through the Separate Entry Gate",
            "Assist at the Money Exchange Counter",
            "Assist with Baggage Wrapping Facilities",
            "Assist with Baggage Check-in at the Airline Counter",
            "Assist through Immigration",
            "Assist inside the Security Hold Area (SHA)",
            "Complimentary Lounge Access (up to 2 hours)",
            "ASSIST GUEST TILL THE BOARDING GATE",
        ],
        additional_benefits=[
            "Free cancellation up to 12 hours before the scheduled service time.",
            "A minimum of 6 hours' notice is required for rescheduling.",
        ],
        min_booking_notice_hours=6,
        is_available=True,
        display_priority=2,
        price=4950.00,
        currency="INR",
    ))

    # 4. International Arrival
    db.query(AirportService).filter_by(
        airport_id=amd_airport.id,
        journey_type="ARRIVAL",
        flight_type="INTERNATIONAL",
    ).delete(synchronize_session=False)

    # Package 1: Platinum Service (₹2,750)
    db.add(AirportService(
        id=uuid.uuid4(),
        airport_id=amd_airport.id,
        service_id=plat_svc.id,
        journey_type="ARRIVAL",
        flight_type="INTERNATIONAL",
        short_description="Premium international arrival assist from post-immigration to the airport exit.",
        features=[
            "Welcome after Immigration",
            "Assist at the Duty Free Shop",
            "Assist at the Baggage Belt Area",
            "Assist after Customs Clearance",
            "Coordination with the Receiving Party",
            "ASSIST GUEST TILL THE CAR PARKING AREA",
        ],
        additional_benefits=[],
        min_booking_notice_hours=6,
        is_available=True,
        display_priority=1,
        price=2750.00,
        currency="INR",
    ))

    db.flush()
    print("  + Successfully seeded AMD Domestic & International Production Packages")
