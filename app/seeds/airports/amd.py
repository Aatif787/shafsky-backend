import uuid
from sqlalchemy.orm import Session
from app.models.journey_models import SupportedAirport, Service, AirportService

def seed_amd_production_packages(db: Session, amd_airport: SupportedAirport, service_map: dict[str, Service]):
    """
    For Ahmedabad Airport (AMD) Domestic Departure & Arrival:
    Seeds production Platinum Service (₹2,420) & Elite Service (₹4,400) packages.
    """
    print("\n-- Configuring Production Packages for Ahmedabad (AMD) Domestic Arrival & Departure --")
    
    # 1. Remove old demo services mapped to AMD Domestic
    db.query(AirportService).filter_by(
        airport_id=amd_airport.id,
        flight_type="DOMESTIC",
    ).delete(synchronize_session=False)

    for j_type in ["ARRIVAL", "DEPARTURE"]:
        # Package 1: Platinum Service (₹2,420)
        plat_svc = service_map.get("platinum")
        if plat_svc:
            plat_mapping = AirportService(
                id=uuid.uuid4(),
                airport_id=amd_airport.id,
                service_id=plat_svc.id,
                journey_type=j_type,
                flight_type="DOMESTIC",
                short_description="Premium airport arrival assist with baggage support and meet & greet.",
                features=[
                    "Welcome at the Aerobridge",
                    "Dedicated Staff with Placard",
                    "Baggage Assist (Up to 3 Pieces)",
                    "Assist at the Baggage Belt Area",
                    "Coordination with the Receiving Party",
                    "Escort to the Car Parking Area",
                ],
                min_booking_notice_hours=4,
                is_available=True,
                display_priority=1,
                price=2420.00,
                currency="INR",
            )
            db.add(plat_mapping)

        # Package 2: Elite Service (₹4,400)
        elite_svc = service_map.get("elite")
        if elite_svc:
            elite_mapping = AirportService(
                id=uuid.uuid4(),
                airport_id=amd_airport.id,
                service_id=elite_svc.id,
                journey_type=j_type,
                flight_type="DOMESTIC",
                short_description="Premium airport arrival assist with dedicated baggage support and complete arrival coordination.",
                features=[
                    "Welcome at the Aerobridge",
                    "Dedicated Staff with Placard",
                    "Baggage Assist",
                    "Assist at the Baggage Belt Area",
                    "Coordination with the Receiving Party",
                    "Escort to the Car Parking Area",
                ],
                min_booking_notice_hours=4,
                is_available=True,
                display_priority=2,
                price=4400.00,
                currency="INR",
            )
            db.add(elite_mapping)

    db.flush()
    print("  + Created AMD Domestic Packages: Platinum Service (INR 2,420) and Elite Service (INR 4,400)")

    # 2. Remove old demo services mapped to AMD International Departure
    print("\n-- Configuring Production Packages for Ahmedabad (AMD) International Departure --")
    db.query(AirportService).filter_by(
        airport_id=amd_airport.id,
        journey_type="DEPARTURE",
        flight_type="INTERNATIONAL",
    ).delete(synchronize_session=False)

    plat_svc = service_map.get("platinum")
    if plat_svc:
        plat_mapping = AirportService(
            id=uuid.uuid4(),
            airport_id=amd_airport.id,
            service_id=plat_svc.id,
            journey_type="DEPARTURE",
            flight_type="INTERNATIONAL",
            short_description="Premium international departure assist from airport arrival to the boarding gate.",
            features=[
                "Welcome at the curbside area",
                "Dedicated porter service",
                "Wheelchair assist (through the airline, if required)",
                "Assist from the entry gate",
                "Assist at the money exchange counter",
                "Assist with baggage wrapping facilities",
                "Assist during baggage check-in at the airline counter",
                "Assist through immigration",
                "Assist inside the Security Hold Area (SHA)",
                "Escort to the boarding gate",
            ],
            additional_benefits=[],
            min_booking_notice_hours=6,
            is_available=True,
            display_priority=1,
            price=3300.00,
            currency="INR",
        )
        db.add(plat_mapping)

    elite_svc = service_map.get("elite")
    if elite_svc:
        elite_mapping = AirportService(
            id=uuid.uuid4(),
            airport_id=amd_airport.id,
            service_id=elite_svc.id,
            journey_type="DEPARTURE",
            flight_type="INTERNATIONAL",
            short_description="Complete premium international departure assist with lounge access and enhanced passenger support.",
            features=[
                "Welcome at the curbside area",
                "Dedicated porter service",
                "Wheelchair assist (through the airline, if required)",
                "Assist from the entry gate",
                "Assist at the money exchange counter",
                "Assist with baggage wrapping facilities",
                "Assist during baggage check-in at the airline counter",
                "Assist through immigration",
                "Assist inside the Security Hold Area (SHA)",
                "Complimentary lounge access (up to 2 hours)",
                "Escort to the boarding gate",
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
        )
        db.add(elite_mapping)

    db.flush()
    print("  + Created AMD International Departure Packages: Platinum Service (INR 3,300) and Elite Service (INR 4,950)")

    # 3. Remove old demo services mapped to AMD International Arrival
    print("\n-- Configuring Production Packages for Ahmedabad (AMD) International Arrival --")
    db.query(AirportService).filter_by(
        airport_id=amd_airport.id,
        journey_type="ARRIVAL",
        flight_type="INTERNATIONAL",
    ).delete(synchronize_session=False)

    plat_svc = service_map.get("platinum")
    if plat_svc:
        plat_mapping = AirportService(
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
                "Escort to the Car Parking Area",
            ],
            additional_benefits=[],
            min_booking_notice_hours=6,
            is_available=True,
            display_priority=1,
            price=2750.00,
            currency="INR",
        )
        db.add(plat_mapping)

    db.flush()
    print("  + Created AMD International Arrival Package: Platinum Service (INR 2,750)")
