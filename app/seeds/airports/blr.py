import uuid
from sqlalchemy.orm import Session
from app.models.journey_models import SupportedAirport, Service, AirportService


BLR_DOMESTIC_DEPARTURE_SHARED_FEATURES = [
    "WELCOME GUEST FROM CURB SIDE AREA.",
    "PORTER SERVICE WITH DEDICATED STAFF.",
    "WHEELCHAIR SERVICE AVAILABLE (Through Airlines).",
    "ASSIST FROM SEPARATE ENTRY GATE.",
    "ASSIST SEPARATE BAGGAGE CHECK-IN AT AIRLINE COUNTER.",
    "ASSIST IN S.H.A.(SECURITY HOLD AREA).",
    "BUGGY SERVICE AVAILABLE TILL THE BOARDING GATE (SHARING BASIS) & (SUBJECT TO AVAILABILITY).",
    "ASSIST GUEST UPTO BOARDING GATE.",
]
BLR_DOMESTIC_DEPARTURE_SILVER_FEATURES = BLR_DOMESTIC_DEPARTURE_SHARED_FEATURES

BLR_DOMESTIC_ARRIVAL_SHARED_FEATURES = [
    "WELCOME GUEST FROM AEROBRIDGE.",
    "DEDICATED STAFF WITH PLACARD.",
    "PORTER SERVICE WITH DEDICATED STAFF AT ARRIVALS.",
    "BUGGY SERVICE AVAILABLE FROM END OF THE AEROBRIDGE (SHARING BASIS) & (SUBJECT TO AVAILABILITY).",
    "WHEELCHAIR SERVICE AVAILABLE (Through Airlines).",
    "ASSIST IN BAGGAGE BELT AREA.",
    "ASSIST GUEST TILL THE CAR PARKING AREA.",
]

BLR_INTERNATIONAL_DEPARTURE_SHARED_FEATURES = [
    "WELCOME GUEST FROM CURB SIDE AREA.",
    "PORTER SERVICE WITH DEDICATED STAFF.",
    "WHEELCHAIR SERVICE AVAILABLE (Through Airlines).",
    "ASSIST FROM SEPARATE ENTRY GATE.",
    "ASSIST IN MONEY EXCHANGE COUNTER.",
    "ASSIST TO BAGGAGE WRAPPING FACILITIES.",
    "ASSIST AT SEPARATE BAGGAGE CHECKIN PROCESS AT AIRLINE COUNTERS.",
    "ASSIST FOR IMMIGRATION COUNTERS.",
    "ASSIST IN S.H.A.(SECURITY HOLD AREA).",
    "BUGGY SERVICE AVAILABLE (SUBJECT TO AVAILABILITY)",
    "ASSIST PAX UPTO BOARDING GATE.",
]

BLR_INTERNATIONAL_ARRIVAL_SHARED_FEATURES = [
    "WELCOME GUEST FROM AEROBRIDGE.",
    "DEDICATED STAFF WITH PLACARD.",
    "PORTER SERVICE WITH DEDICATED STAFF AT ARRIVALS.",
    "BUGGY SERVICE AVAILABLE (SHARING BASIS) & (SUBJECT TO AVAILABILITY).",
    "ASSIST TO THE IMMIGRATION COUNTER.",
    "ASSIST IN DUTY FREE SHOP.",
    "ASSIST IN BAGGAGE BELT AREA.",
    "ASSIST IN CUSTOM AREA.",
    "ASSIST GUEST TILL THE CAR PARKING AREA.",
]


def seed_blr_production_packages(db: Session, blr_airport: SupportedAirport, service_map: dict[str, Service]):
    """
    Configure authoritative production packages for Bengaluru (BLR).
    Domestic Departure: Silver (INR 4500), Gold (INR 6500), Elite (INR 8000) - Shared 8 Inclusions.
    Domestic Arrival: Silver (INR 4500), Gold (INR 6500), Elite (INR 8000) - Shared 7 Inclusions.
    International Departure: Silver (INR 6000), Gold (INR 9000), Elite (INR 13000) - Shared 11 Inclusions.
    International Arrival: Silver (INR 6000), Gold (INR 9000), Elite (INR 13000) - Shared 9 Inclusions.
    Transit: Not configured (is_available = False).
    """
    silver_svc = service_map.get("silver")
    gold_svc = service_map.get("gold")
    elite_svc = service_map.get("elite")

    if not (silver_svc and gold_svc and elite_svc):
        print("  ! Skipping BLR: Required services (silver, gold, elite) missing from service_map")
        return

    # Deactivate all other BLR mappings
    existing_mappings = db.query(AirportService).filter_by(airport_id=blr_airport.id).all()
    for m in existing_mappings:
        m.is_available = False

    def upsert(svc, journey, flight, price, features, priority):
        existing = (
            db.query(AirportService)
            .filter_by(
                airport_id=blr_airport.id,
                service_id=svc.id,
                journey_type=journey,
                flight_type=flight,
                terminal=None,
            )
            .first()
        )
        if existing:
            target = existing
            action = "updated"
        else:
            target = AirportService(
                id=uuid.uuid4(),
                airport_id=blr_airport.id,
                service_id=svc.id,
                journey_type=journey,
                flight_type=flight,
                terminal=None,
            )
            db.add(target)
            action = "created"

        target.short_description = None
        target.features = list(features)
        target.additional_benefits = []
        target.min_booking_notice_hours = 6
        target.is_available = True
        target.display_priority = priority
        target.price = price
        target.currency = "INR"
        target.terminal = None
        return action

    # Domestic Departure
    upsert(silver_svc, "DEPARTURE", "DOMESTIC", 4500.00, BLR_DOMESTIC_DEPARTURE_SHARED_FEATURES, 1)
    upsert(gold_svc, "DEPARTURE", "DOMESTIC", 6500.00, BLR_DOMESTIC_DEPARTURE_SHARED_FEATURES, 2)
    upsert(elite_svc, "DEPARTURE", "DOMESTIC", 8000.00, BLR_DOMESTIC_DEPARTURE_SHARED_FEATURES, 3)

    # Domestic Arrival
    upsert(silver_svc, "ARRIVAL", "DOMESTIC", 4500.00, BLR_DOMESTIC_ARRIVAL_SHARED_FEATURES, 1)
    upsert(gold_svc, "ARRIVAL", "DOMESTIC", 6500.00, BLR_DOMESTIC_ARRIVAL_SHARED_FEATURES, 2)
    upsert(elite_svc, "ARRIVAL", "DOMESTIC", 8000.00, BLR_DOMESTIC_ARRIVAL_SHARED_FEATURES, 3)

    # International Departure
    upsert(silver_svc, "DEPARTURE", "INTERNATIONAL", 6000.00, BLR_INTERNATIONAL_DEPARTURE_SHARED_FEATURES, 1)
    upsert(gold_svc, "DEPARTURE", "INTERNATIONAL", 9000.00, BLR_INTERNATIONAL_DEPARTURE_SHARED_FEATURES, 2)
    upsert(elite_svc, "DEPARTURE", "INTERNATIONAL", 13000.00, BLR_INTERNATIONAL_DEPARTURE_SHARED_FEATURES, 3)

    # International Arrival
    upsert(silver_svc, "ARRIVAL", "INTERNATIONAL", 6000.00, BLR_INTERNATIONAL_ARRIVAL_SHARED_FEATURES, 1)
    upsert(gold_svc, "ARRIVAL", "INTERNATIONAL", 9000.00, BLR_INTERNATIONAL_ARRIVAL_SHARED_FEATURES, 2)
    upsert(elite_svc, "ARRIVAL", "INTERNATIONAL", 13000.00, BLR_INTERNATIONAL_ARRIVAL_SHARED_FEATURES, 3)

    db.flush()
    print("  + Configured BLR production packages: 12 authoritative mappings active (Dom Dep 3, Dom Arr 3, Intl Dep 3, Intl Arr 3)")
