import uuid
from sqlalchemy.orm import Session
from app.models.journey_models import SupportedAirport, Service, AirportService



# ==============================================================================
# IXC — CHANDIGARH AIRPORT PRODUCTION PACKAGES
# ==============================================================================

IXC_DOMESTIC_DEPARTURE_SILVER_FEATURES = [
    "WELCOME GUEST AT DEPARTURE CURB SIDE / CAR DROP AREA.",
    "PORTER SERVICE WITH DEDICATED STAFF.",
    "WHEELCHAIR SERVICE AVAILABLE (Through Airlines)",
    "ASSIST SEPARATE BAGGAGE CHECK-IN AT AIRLINE COUNTER.",
    "ASSIST IN S.H.A.(SECURITY HOLD AREA).",
    "ASSIST GUEST UPTO BOARDING GATE.",
]

IXC_DOMESTIC_DEPARTURE_GOLD_FEATURES = [
    "WELCOME GUEST AT DEPARTURE CURB SIDE / CAR DROP AREA.",
    "PORTER SERVICE WITH DEDICATED STAFF.",
    "WHEELCHAIR SERVICE AVAILABLE (Through Airlines)",
    "ASSIST SEPARATE BAGGAGE CHECK-IN AT AIRLINE COUNTER.",
    "ASSIST IN S.H.A.(SECURITY HOLD AREA).",
    "LOUNGE ACCESS FOR 2 HOURS.",
    "ASSIST GUEST UPTO BOARDING GATE.",
]

IXC_DOMESTIC_ARRIVAL_SILVER_FEATURES = [
    "WELCOME GUEST FROM END OF THE AEROBRIDGE.",
    "DEDICATED STAFF WITH PLACARD.",
    "PORTER SERVICE WITH DEDICATED STAFF AT ARRIVALS.",
    "WHEELCHAIR SERVICE AVAILABLE (Through Airlines).",
    "ASSIST IN BAGGAGE BELT AREA.",
    "ASSIST GUEST TILL THE CAR PARKING AREA",
]

IXC_INTERNATIONAL_DEPARTURE_SILVER_FEATURES = [
    "WELCOME GUEST FROM CURB SIDE AREA/CAR DROP AREA.",
    "PORTER SERVICE WITH DEDICATED STAFF.",
    "WHEELCHAIR SERVICE AVAILABLE (THROUGH AIRLINES).",
    "ASSIST TO MONEY EXCHANGE COUNTER.",
    "ASSIST TO BAGGAGE WRAPPING FACILITY.",
    "ASSIST SEPARATE BAGGAGE CHECK-IN AT AIRLINE COUNTER.",
    "ASSIST FOR IMMIGRATION COUNTERS.",
    "ASSIST IN S.H.A (SECURITY HOLD AREA).",
    "ASSIST GUEST UPTO BOARDING GATE.",
]

IXC_INTERNATIONAL_ARRIVAL_SILVER_FEATURES = [
    "WELCOME GUEST FROM POST CUSTOMS.",
    "PORTER SERVICE WITH DEDICATED STAFF. (UP TO 3 BAGS PER PASSENGERS).",
    "ASSIST AT BAGGAGE BELT AREA.",
    "COORDINATION TO THE RECEIVING PERSON.",
    "DROP OFF CAR PARKING AREA",
]


def seed_ixc_production_packages(db: Session, ixc_airport: SupportedAirport, service_map: dict[str, Service]):
    """
    Configure authoritative production packages for Chandigarh Airport (IXC).
    Domestic Departure: Silver (INR 2500), Gold (INR 4000)
    Domestic Arrival: Silver (INR 2500)
    International Departure: Silver (INR 3000)
    International Arrival: Silver (INR 2500)
    All other tiers and transit: Not configured (is_available = False).
    """
    silver_svc = service_map.get("silver")
    gold_svc = service_map.get("gold")

    if not (silver_svc and gold_svc):
        print("  ! Skipping IXC: Required services (silver, gold) missing from service_map")
        return

    # Deactivate all other IXC mappings
    existing_mappings = db.query(AirportService).filter_by(airport_id=ixc_airport.id).all()
    for m in existing_mappings:
        m.is_available = False

    def upsert(svc, journey, flight, price, features, priority):
        existing = (
            db.query(AirportService)
            .filter_by(
                airport_id=ixc_airport.id,
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
                airport_id=ixc_airport.id,
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

    upsert(silver_svc, "DEPARTURE", "DOMESTIC", 2500.00, IXC_DOMESTIC_DEPARTURE_SILVER_FEATURES, 1)
    upsert(gold_svc, "DEPARTURE", "DOMESTIC", 4000.00, IXC_DOMESTIC_DEPARTURE_GOLD_FEATURES, 2)
    upsert(silver_svc, "ARRIVAL", "DOMESTIC", 2500.00, IXC_DOMESTIC_ARRIVAL_SILVER_FEATURES, 1)
    upsert(silver_svc, "DEPARTURE", "INTERNATIONAL", 3000.00, IXC_INTERNATIONAL_DEPARTURE_SILVER_FEATURES, 1)
    upsert(silver_svc, "ARRIVAL", "INTERNATIONAL", 2500.00, IXC_INTERNATIONAL_ARRIVAL_SILVER_FEATURES, 1)

    db.flush()
    print("  + Configured IXC production packages: 5 authoritative mappings active (Dom Dep Silver INR 2500, Dom Dep Gold INR 4000, Dom Arr Silver INR 2500, Int Dep Silver INR 3000, Int Arr Silver INR 2500)")
