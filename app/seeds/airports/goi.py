import uuid
from sqlalchemy.orm import Session
from app.models.journey_models import SupportedAirport, Service, AirportService


def seed_goi_production_packages(db: Session, goi_airport: SupportedAirport, service_map: dict[str, Service]):
    """
    For Goa Dabolim Airport (GOI):
    Seeds production packages:
    - Domestic Departure: Silver Service (₹2,500), Gold Service (₹4,000)
    - Domestic Arrival: Silver Service (₹2,500)
    - International Departure: Silver Service (₹2,000)
    - International Arrival: Silver Service (₹2,000)
    """
    print("\n-- Configuring Production Packages for Goa Dabolim Airport (GOI) --")

    # 1. Clear all existing services mapped to GOI to avoid duplicates
    db.query(AirportService).filter_by(
        airport_id=goi_airport.id,
    ).delete(synchronize_session=False)

    silver_svc = service_map.get("silver")
    gold_svc = service_map.get("gold")

    # ── 1. DOMESTIC DEPARTURE PACKAGES ──
    # Silver Service (₹2,500)
    if silver_svc:
        db.add(AirportService(
            id=uuid.uuid4(),
            airport_id=goi_airport.id,
            service_id=silver_svc.id,
            journey_type="DEPARTURE",
            flight_type="DOMESTIC",
            short_description="Domestic departure assist from the departure curbside to the boarding gate.",
            features=[
                "Welcome at the Departure Curbside / Car Drop Area",
                "Dedicated Porter Service",
                "Wheelchair Assist (through the airline)",
                "Assist at the Domestic Departure Area",
                "Assist with Separate Baggage Check-in at the Airline Counter",
                "Assist inside the Security Hold Area (SHA)",
                "Escort to the Boarding Gate",
            ],
            additional_benefits=[],
            min_booking_notice_hours=6,
            is_available=True,
            display_priority=1,
            price=2500.00,
            currency="INR",
        ))

    # Gold Service (₹4,000)
    if gold_svc:
        db.add(AirportService(
            id=uuid.uuid4(),
            airport_id=goi_airport.id,
            service_id=gold_svc.id,
            journey_type="DEPARTURE",
            flight_type="DOMESTIC",
            short_description="Enhanced domestic departure assist with lounge service and dedicated airport support.",
            features=[
                "Welcome at the Departure Curbside / Car Drop Area",
                "Dedicated Porter Service",
                "Wheelchair Assist (through the airline)",
                "Assist at the Domestic Departure Area",
                "Assist with Separate Baggage Check-in at the Airline Counter",
                "Assist inside the Security Hold Area (SHA)",
                "Lounge Service Facility",
                "Escort to the Boarding Gate",
            ],
            additional_benefits=[],
            min_booking_notice_hours=6,
            is_available=True,
            display_priority=2,
            price=4000.00,
            currency="INR",
        ))

    # ── 2. DOMESTIC ARRIVAL PACKAGES ──
    # Silver Service (₹2,500)
    if silver_svc:
        db.add(AirportService(
            id=uuid.uuid4(),
            airport_id=goi_airport.id,
            service_id=silver_svc.id,
            journey_type="ARRIVAL",
            flight_type="DOMESTIC",
            short_description="Domestic arrival assist from the end of the aerobridge to the car parking area.",
            features=[
                "Welcome at the End of the Aerobridge",
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
            price=2500.00,
            currency="INR",
        ))

    # ── 3. INTERNATIONAL DEPARTURE PACKAGES ──
    # Silver Service (₹2,000)
    if silver_svc:
        db.add(AirportService(
            id=uuid.uuid4(),
            airport_id=goi_airport.id,
            service_id=silver_svc.id,
            journey_type="DEPARTURE",
            flight_type="INTERNATIONAL",
            short_description="International departure assist from the curbside area to the boarding gate.",
            features=[
                "Welcome at the Curbside Area / Car Drop Area",
                "Dedicated Porter Service",
                "Wheelchair Assist (through the airline)",
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
            price=2000.00,
            currency="INR",
        ))

    # ── 4. INTERNATIONAL ARRIVAL PACKAGES ──
    # Silver Service (₹2,000)
    if silver_svc:
        db.add(AirportService(
            id=uuid.uuid4(),
            airport_id=goi_airport.id,
            service_id=silver_svc.id,
            journey_type="ARRIVAL",
            flight_type="INTERNATIONAL",
            short_description="International arrival assist from post-customs to the car parking area.",
            features=[
                "Welcome after Customs Clearance",
                "Dedicated Porter Service (up to 3 bags per passenger)",
                "Assist at the Baggage Belt Area",
                "Coordination with the Receiving Party",
                "Escort to the Car Parking Area",
            ],
            additional_benefits=[],
            min_booking_notice_hours=6,
            is_available=True,
            display_priority=1,
            price=2000.00,
            currency="INR",
        ))

    db.flush()
    print("  + Created GOI Production Packages: Domestic Dep (Silver INR 2,500, Gold INR 4,000), Domestic Arr (Silver INR 2,500), Intl Dep (Silver INR 2,000), Intl Arr (Silver INR 2,000)")
