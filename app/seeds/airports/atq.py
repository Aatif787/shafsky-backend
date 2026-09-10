import uuid
from sqlalchemy.orm import Session
from app.models.journey_models import SupportedAirport, Service, AirportService


def seed_atq_production_packages(db: Session, atq_airport: SupportedAirport, service_map: dict[str, Service]):
    """
    For Amritsar Airport (ATQ):
    Seeds production packages:
    - Domestic Departure: ₹2,500
    - Domestic Arrival: ₹2,500
    - International Departure: ₹2,500
    - International Arrival: ₹2,500
    """
    print("\n-- Configuring Production Packages for Amritsar Airport (ATQ) --")

    # 1. Clear all existing services mapped to ATQ to avoid duplicates
    db.query(AirportService).filter_by(
        airport_id=atq_airport.id,
    ).delete(synchronize_session=False)

    meet_svc = service_map.get("meet_greet") or service_map.get("platinum")

    if meet_svc:
        # 1. Domestic Departure (₹2,500)
        db.add(AirportService(
            id=uuid.uuid4(),
            airport_id=atq_airport.id,
            service_id=meet_svc.id,
            journey_type="DEPARTURE",
            flight_type="DOMESTIC",
            short_description="Domestic departure assist from the curbside area to the boarding gate.",
            features=[
                "WELCOME GUEST FROM CURBSIDE AREA",
                "PORTER SERVICE WITH DEDICATED STAFF",
                "WHEELCHAIR SERVICE AVAILABLE (Through Airlines)",
                "ASSIST FROM SEPARATE ENTRY GATE",
                "ASSIST TO BAGGAGE WRAPPING FACILITIES",
                "ASSIST AT SEPARATE CHECKIN PROCESS AT COUNTERS",
                "ASSIST IN S.H.A.(SECURITY HOLD AREA)",
                "LOUNGE SERVICE FACILITY AVAILABLE (CHARGES APPLICABLE)",
                "ASSIST GUEST TILL THE BOARDING GATE",
            ],
            additional_benefits=[],
            min_booking_notice_hours=6,
            is_available=True,
            display_priority=1,
            price=2500.00,
            currency="INR",
        ))

        # 2. Domestic Arrival (₹2,500)
        db.add(AirportService(
            id=uuid.uuid4(),
            airport_id=atq_airport.id,
            service_id=meet_svc.id,
            journey_type="ARRIVAL",
            flight_type="DOMESTIC",
            short_description="Domestic arrival assist from the aerobridge to the car parking area.",
            features=[
                "WELCOME GUEST FROM AEROBRIDGE",
                "DEDICATED STAFF WITH PLACARD",
                "PORTER SERVICE WITH DEDICATED STAFF AT ARRIVALS",
                "WHEELCHAIR SERVICE AVAILABLE (Through Airlines)",
                "ASSIST IN BAGGAGE BELT AREA",
                "ASSIST GUEST TILL THE CAR PARKING AREA",
            ],
            additional_benefits=[],
            min_booking_notice_hours=6,
            is_available=True,
            display_priority=1,
            price=2500.00,
            currency="INR",
        ))

        # 3. International Departure (₹2,500)
        db.add(AirportService(
            id=uuid.uuid4(),
            airport_id=atq_airport.id,
            service_id=meet_svc.id,
            journey_type="DEPARTURE",
            flight_type="INTERNATIONAL",
            short_description="International departure assist from the curbside area to the boarding gate.",
            features=[
                "WELCOME GUEST FROM CURBSIDE AREA",
                "PORTER SERVICE WITH DEDICATED STAFF",
                "WHEELCHAIR SERVICE AVAILABLE (Through Airlines)",
                "ASSIST FROM SEPARATE ENTRY GATE",
                "ASSIST TO BAGGAGE WRAPPING FACILITIES",
                "ASSIST AT SEPARATE CHECK IN PROCESS AT COUNTERS",
                "ASSIST IN S.H.A.(SECURITY HOLD AREA)",
                "LOUNGE SERVICE FACILITY AVAILABLE (CHARGES APPLICABLE)",
                "ASSIST GUEST TILL THE BOARDING GATE",
            ],
            additional_benefits=[],
            min_booking_notice_hours=6,
            is_available=True,
            display_priority=1,
            price=2500.00,
            currency="INR",
        ))

        # 4. International Arrival (₹2,500)
        db.add(AirportService(
            id=uuid.uuid4(),
            airport_id=atq_airport.id,
            service_id=meet_svc.id,
            journey_type="ARRIVAL",
            flight_type="INTERNATIONAL",
            short_description="International arrival assist from post-immigration to the parking area.",
            features=[
                "WELCOME GUEST FROM POST IMMIGRATION.",
                "PORTER SERVICE WITH DEDICATED STAFF AT POST IMMIGRATION AREA.",
                "ASSIST IN BAGGAGE BELT AREA.",
                "ASSIST IN CUSTOMS.",
                "COORDINATION WITH RECEIVING PARTY.",
                "ASSIST GUEST TILL THE PARKING AREA.",
            ],
            additional_benefits=[],
            min_booking_notice_hours=6,
            is_available=True,
            display_priority=1,
            price=2500.00,
            currency="INR",
        ))

    db.flush()
    print("  + Created ATQ Production Packages: Domestic & International Departure & Arrival (INR 2,500)")
