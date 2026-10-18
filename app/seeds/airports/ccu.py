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
                "WELCOME AT THE DEPARTURE CURBSIDE / CAR DROP AREA",
                "DEDICATED PORTER SERVICE",
                "WHEELCHAIR ASSIST (THROUGH THE AIRLINE, IF REQUIRED)",
                "ASSIST AT THE DOMESTIC DEPARTURE AREA",
                "ASSIST WITH SEPARATE BAGGAGE CHECK-IN AT THE AIRLINE COUNTER",
                "ASSIST INSIDE THE SECURITY HOLD AREA (SHA)",
                "ESCORT TO THE BOARDING GATE",
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
                "WELCOME AT THE DEPARTURE CURBSIDE / CAR DROP AREA",
                "DEDICATED PORTER SERVICE",
                "WHEELCHAIR ASSIST (THROUGH THE AIRLINE, IF REQUIRED)",
                "ASSIST AT THE DOMESTIC DEPARTURE AREA",
                "ASSIST WITH SEPARATE BAGGAGE CHECK-IN AT THE AIRLINE COUNTER",
                "ASSIST INSIDE THE SECURITY HOLD AREA (SHA)",
                "COMPLIMENTARY LOUNGE ACCESS (UP TO 2 HOURS)",
                "ESCORT TO THE BOARDING GATE",
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
                "WELCOME GUEST FROM NEAR THE BELT AREA.",
                "DEDICATED STAFF WITH PLACARD.",
                "PORTER SERVICE WITH DEDICATED STAFF AT ARRIVALS.",
                "WHEELCHAIR SERVICE AVAILABLE (THROUGH AIRLINES).",
                "ASSIST IN BAGGAGE BELT AREA.",
                "ASSIST GUEST TILL THE CAR PARKING AREA",
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
                "WELCOME AT THE CURBSIDE / CAR DROP AREA",
                "DEDICATED PORTER SERVICE",
                "WHEELCHAIR ASSIST (THROUGH THE AIRLINE, IF REQUIRED)",
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
                "WELCOME AFTER CUSTOMS CLEARANCE",
                "DEDICATED PORTER SERVICE (UP TO 3 BAGS PER PASSENGER)",
                "ASSIST AT THE BAGGAGE BELT AREA",
                "COORDINATION WITH THE RECEIVING PARTY",
                "ASSIST GUEST TILL THE CAR PARKING AREA",
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
