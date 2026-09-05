import uuid
from sqlalchemy.orm import Session
from app.models.journey_models import SupportedAirport, Service, AirportService


def seed_ccu_production_packages(db: Session, ccu_airport: SupportedAirport, service_map: dict[str, Service]):
    """
    For Kolkata Airport (CCU):
    Seeds production packages:
    - Domestic Departure: Silver (INR 2,200) and Gold (INR 3,500)
    - Domestic Arrival: Silver (INR 1,500)
    - International Departure: Silver (INR 3,000)
    - International Arrival: Silver (INR 2,000)
    """
    print("\n-- Configuring Production Packages for Kolkata (CCU) --")

    # Remove all existing services for CCU
    db.query(AirportService).filter_by(
        airport_id=ccu_airport.id,
        journey_type="DEPARTURE",
        flight_type="DOMESTIC",
    ).delete(synchronize_session=False)

    db.query(AirportService).filter_by(
        airport_id=ccu_airport.id,
        journey_type="ARRIVAL",
        flight_type="DOMESTIC",
    ).delete(synchronize_session=False)

    db.query(AirportService).filter_by(
        airport_id=ccu_airport.id,
        journey_type="DEPARTURE",
        flight_type="INTERNATIONAL",
    ).delete(synchronize_session=False)

    db.query(AirportService).filter_by(
        airport_id=ccu_airport.id,
        journey_type="ARRIVAL",
        flight_type="INTERNATIONAL",
    ).delete(synchronize_session=False)

    silver_svc = service_map.get("silver")
    gold_svc = service_map.get("gold")

    # -- Domestic Departure Packages --
    if silver_svc:
        db.add(AirportService(
            id=uuid.uuid4(),
            airport_id=ccu_airport.id,
            service_id=silver_svc.id,
            journey_type="DEPARTURE",
            flight_type="DOMESTIC",
            short_description="Premium domestic departure assist from the departure curbside to the boarding gate.",
            features=[
                "Welcome at the Departure Curbside / Car Drop Area",
                "Dedicated Porter Service",
                "Wheelchair Assist (through the airline, if required)",
                "Assist at the Domestic Departure Area",
                "Assist with Separate Baggage Check-in at the Airline Counter",
                "Assist inside the Security Hold Area (SHA)",
                "Escort to the Boarding Gate",
            ],
            additional_benefits=[],
            min_booking_notice_hours=6,
            is_available=True,
            display_priority=1,
            price=2200.00,
            currency="INR",
        ))

    if gold_svc:
        db.add(AirportService(
            id=uuid.uuid4(),
            airport_id=ccu_airport.id,
            service_id=gold_svc.id,
            journey_type="DEPARTURE",
            flight_type="DOMESTIC",
            short_description="Enhanced domestic departure assist with lounge access and airport support.",
            features=[
                "Welcome at the Departure Curbside / Car Drop Area",
                "Dedicated Porter Service",
                "Wheelchair Assist (through the airline, if required)",
                "Assist at the Domestic Departure Area",
                "Assist with Separate Baggage Check-in at the Airline Counter",
                "Assist inside the Security Hold Area (SHA)",
                "Complimentary Lounge Access (up to 2 hours)",
                "Escort to the Boarding Gate",
            ],
            additional_benefits=[],
            min_booking_notice_hours=6,
            is_available=True,
            display_priority=2,
            price=3500.00,
            currency="INR",
        ))

    # -- Domestic Arrival Packages --
    if silver_svc:
        db.add(AirportService(
            id=uuid.uuid4(),
            airport_id=ccu_airport.id,
            service_id=silver_svc.id,
            journey_type="ARRIVAL",
            flight_type="DOMESTIC",
            short_description="Premium domestic arrival assist from the baggage belt area to the car parking area.",
            features=[
                "Welcome near the Baggage Belt Area",
                "Dedicated Staff with Placard",
                "Dedicated Porter Service at Arrivals",
                "Wheelchair Assist (through the airline, if required)",
                "Assist at the Baggage Belt Area",
                "Escort to the Car Parking Area",
            ],
            additional_benefits=[],
            min_booking_notice_hours=6,
            is_available=True,
            display_priority=1,
            price=1500.00,
            currency="INR",
        ))

    # -- International Departure Packages --
    if silver_svc:
        db.add(AirportService(
            id=uuid.uuid4(),
            airport_id=ccu_airport.id,
            service_id=silver_svc.id,
            journey_type="DEPARTURE",
            flight_type="INTERNATIONAL",
            short_description="Premium international departure assist from the curbside area to the boarding gate.",
            features=[
                "Welcome at the Curbside / Car Drop Area",
                "Dedicated Porter Service",
                "Wheelchair Assist (through the airline, if required)",
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
            price=3000.00,
            currency="INR",
        ))

    # -- International Arrival Packages --
    if silver_svc:
        db.add(AirportService(
            id=uuid.uuid4(),
            airport_id=ccu_airport.id,
            service_id=silver_svc.id,
            journey_type="ARRIVAL",
            flight_type="INTERNATIONAL",
            short_description="Premium international arrival assist from post-customs to the car parking area.",
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
    print("  + Created CCU Production Packages: Domestic Dep/Arr + International Dep/Arr")
