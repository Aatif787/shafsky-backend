import uuid
from sqlalchemy.orm import Session
from app.models.journey_models import SupportedAirport, Service, AirportService


def seed_lko_production_packages(db: Session, lko_airport: SupportedAirport, service_map: dict[str, Service]):
    """
    For Lucknow Airport (LKO):
    Seeds production packages:
    - Domestic Departure: Platinum (INR 2,420) and Elite (INR 4,400)
    - Domestic Arrival: Platinum (INR 2,420) and Elite (INR 4,400)
    - International Departure: Platinum (INR 3,300) and Elite (INR 4,950)
    - International Arrival: Platinum (INR 2,750)
    """
    print("\n-- Configuring Production Packages for Lucknow (LKO) --")

    # Remove all old services for LKO
    db.query(AirportService).filter_by(
        airport_id=lko_airport.id,
    ).delete(synchronize_session=False)

    platinum_svc = service_map.get("platinum")
    elite_svc = service_map.get("elite")

    # ── Domestic Departure Packages ──
    if platinum_svc:
        db.add(AirportService(
            id=uuid.uuid4(),
            airport_id=lko_airport.id,
            service_id=platinum_svc.id,
            journey_type="DEPARTURE",
            flight_type="DOMESTIC",
            short_description="Premium domestic departure assist from the curbside area to the boarding gate.",
            features=[
                "WELCOME AT THE CURBSIDE AREA",
                "DEDICATED PORTER SERVICE",
                "WHEELCHAIR ASSIST (THROUGH THE AIRLINE, IF REQUIRED)",
                "ASSIST THROUGH THE SEPARATE ENTRY GATE",
                "ASSIST WITH BAGGAGE WRAPPING FACILITIES",
                "ASSIST WITH SEPARATE BAGGAGE CHECK-IN AT THE AIRLINE COUNTER",
                "ASSIST INSIDE THE SECURITY HOLD AREA (SHA)",
                "ESCORT TO THE BOARDING GATE",
            ],
            additional_benefits=[],
            min_booking_notice_hours=6,
            is_available=True,
            display_priority=1,
            price=2420.00,
            currency="INR",
        ))

    if elite_svc:
        db.add(AirportService(
            id=uuid.uuid4(),
            airport_id=lko_airport.id,
            service_id=elite_svc.id,
            journey_type="DEPARTURE",
            flight_type="DOMESTIC",
            short_description="Complete premium domestic departure assist with lounge access and airport support.",
            features=[
                "WELCOME AT THE CURBSIDE AREA",
                "DEDICATED PORTER SERVICE",
                "WHEELCHAIR ASSIST (THROUGH THE AIRLINE, IF REQUIRED)",
                "ASSIST THROUGH THE SEPARATE ENTRY GATE",
                "ASSIST WITH BAGGAGE WRAPPING FACILITIES",
                "ASSIST WITH SEPARATE CHECK-IN AT THE AIRLINE COUNTERS",
                "ASSIST INSIDE THE SECURITY HOLD AREA (SHA)",
                "COMPLIMENTARY LOUNGE ACCESS",
                "ESCORT TO THE BOARDING GATE",
            ],
            additional_benefits=[
                "Free cancellation up to 12 hours before the scheduled service time",
                "Minimum 6 hours' prior notice required for rescheduling",
            ],
            min_booking_notice_hours=6,
            is_available=True,
            display_priority=2,
            price=4400.00,
            currency="INR",
        ))

    # ── Domestic Arrival Packages ──
    if platinum_svc:
        db.add(AirportService(
            id=uuid.uuid4(),
            airport_id=lko_airport.id,
            service_id=platinum_svc.id,
            journey_type="ARRIVAL",
            flight_type="DOMESTIC",
            short_description="Premium domestic arrival assist from the aerobridge to the car parking area.",
            features=[
                "WELCOME AT THE AEROBRIDGE",
                "DEDICATED STAFF WITH PLACARD",
                "DEDICATED PORTER SERVICE AT ARRIVALS",
                "WHEELCHAIR ASSIST (THROUGH THE AIRLINE, IF REQUIRED)",
                "ASSIST AT THE BAGGAGE BELT AREA",
                "ASSIST GUEST TILL THE CAR PARKING AREA",
            ],
            additional_benefits=[],
            min_booking_notice_hours=6,
            is_available=True,
            display_priority=1,
            price=2420.00,
            currency="INR",
        ))

    if elite_svc:
        db.add(AirportService(
            id=uuid.uuid4(),
            airport_id=lko_airport.id,
            service_id=elite_svc.id,
            journey_type="ARRIVAL",
            flight_type="DOMESTIC",
            short_description="Complete premium domestic arrival assist with airport support.",
            features=[
                "WELCOME AT THE AEROBRIDGE",
                "DEDICATED STAFF WITH PLACARD",
                "DEDICATED PORTER SERVICE AT ARRIVALS",
                "WHEELCHAIR ASSIST (THROUGH THE AIRLINE, IF REQUIRED)",
                "ASSIST AT THE BAGGAGE BELT AREA",
                "ASSIST GUEST TILL THE CAR PARKING AREA",
            ],
            additional_benefits=[
                "Free cancellation up to 12 hours before the scheduled service time",
                "Minimum 6 hours' prior notice required for rescheduling",
            ],
            min_booking_notice_hours=6,
            is_available=True,
            display_priority=2,
            price=4400.00,
            currency="INR",
        ))

    # ── International Departure Packages ──
    if platinum_svc:
        db.add(AirportService(
            id=uuid.uuid4(),
            airport_id=lko_airport.id,
            service_id=platinum_svc.id,
            journey_type="DEPARTURE",
            flight_type="INTERNATIONAL",
            short_description="Premium international departure assist from the curbside area to the boarding gate.",
            features=[
                "WELCOME AT THE CURBSIDE AREA",
                "DEDICATED PORTER SERVICE",
                "WHEELCHAIR ASSIST (THROUGH THE AIRLINE, IF REQUIRED)",
                "ASSIST THROUGH THE SEPARATE ENTRY GATE",
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
            price=3300.00,
            currency="INR",
        ))

    if elite_svc:
        db.add(AirportService(
            id=uuid.uuid4(),
            airport_id=lko_airport.id,
            service_id=elite_svc.id,
            journey_type="DEPARTURE",
            flight_type="INTERNATIONAL",
            short_description="Complete premium international departure assist with lounge access and airport support.",
            features=[
                "WELCOME AT THE CURBSIDE AREA",
                "DEDICATED PORTER SERVICE",
                "WHEELCHAIR ASSIST (THROUGH THE AIRLINE, IF REQUIRED)",
                "ASSIST THROUGH THE SEPARATE ENTRY GATE",
                "ASSIST AT THE MONEY EXCHANGE COUNTER",
                "ASSIST WITH BAGGAGE WRAPPING FACILITIES",
                "ASSIST WITH SEPARATE BAGGAGE CHECK-IN AT THE AIRLINE COUNTER",
                "ASSIST THROUGH IMMIGRATION",
                "ASSIST INSIDE THE SECURITY HOLD AREA (SHA)",
                "COMPLIMENTARY LOUNGE ACCESS (UP TO 2 HOURS)",
                "ESCORT TO THE BOARDING GATE",
            ],
            additional_benefits=[
                "Free cancellation up to 12 hours before the scheduled service time",
                "Minimum 6 hours' prior notice required for rescheduling",
            ],
            min_booking_notice_hours=6,
            is_available=True,
            display_priority=2,
            price=4950.00,
            currency="INR",
        ))

    # ── International Arrival Packages ──
    if platinum_svc:
        db.add(AirportService(
            id=uuid.uuid4(),
            airport_id=lko_airport.id,
            service_id=platinum_svc.id,
            journey_type="ARRIVAL",
            flight_type="INTERNATIONAL",
            short_description="Premium international arrival assist from post-immigration to the car parking area.",
            features=[
                "WELCOME AFTER IMMIGRATION",
                "ASSIST AT THE DUTY FREE SHOP",
                "ASSIST AT THE BAGGAGE BELT AREA",
                "ASSIST AFTER CUSTOMS CLEARANCE",
                "COORDINATION WITH THE RECEIVING PARTY",
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
    print("  + Created LKO Production Packages: Domestic Dep/Arr + International Dep/Arr")
