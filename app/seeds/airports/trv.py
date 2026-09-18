import uuid
from sqlalchemy.orm import Session
from app.models.journey_models import SupportedAirport, Service, AirportService


TRV_DOMESTIC_DEPARTURE_SILVER_FEATURES = [
    "WELCOME GUEST FROM CURBSIDE AREA",
    "PORTER SERVICE WITH DEDICATED STAFF",
    "ASSIST IN SEPARATE ENTRY GATE",
    "ASSIST IN AIRLINE CHECK IN BAGGAGE",
    "ASSIST IN S.H.A (SECURITY HOLD AREA)",
    "DROP OFF TILL BOARDING GATE.",
]

TRV_DOMESTIC_DEPARTURE_ELITE_FEATURES = [
    "WELCOME GUEST FROM CURBSIDE AREA",
    "PORTER SERVICE WITH DEDICATED STAFF",
    "ASSIST IN SEPARATE ENTRY GATE",
    "ASSIST IN AIRLINE CHECK IN BAGGAGE",
    "ASSIST IN S.H.A (SECURITY HOLD AREA)",
    "LOUNGE ACCESS FOR 2 HOURS",
    "DROP OFF TILL BOARDING GATE.",
]

TRV_DOMESTIC_ARRIVAL_SILVER_FEATURES = [
    "WELCOME GUEST FROM END OF THE AEROBRIDGE.",
    "DEDICATED STAFF WITH PLACARD.",
    "ASSIST IN BAGGAGE BELT AREA",
    "COORDINATION WITH RECEIVING PARTY.",
    "DROP OFF TILL CAR PARKING AREA.",
]

TRV_DOMESTIC_ARRIVAL_ELITE_FEATURES = [
    "WELCOME GUEST FROM END OF THE AEROBRIDGE.",
    "DEDICATED STAFF WITH PLACARD.",
    "ASSIST IN BAGGAGE BELT AREA",
    "COORDINATION WITH RECEIVING PARTY.",
    "LOUNGE ACCESS FOR 2 HOURS",
    "DROP OFF TILL CAR PARKING AREA.",
]

TRV_INTERNATIONAL_DEPARTURE_SILVER_FEATURES = [
    "WELCOME GUEST FROM CURBSIDE AREA",
    "PORTER SERVICE WITH DEDICATED STAFF",
    "ASSIST IN SEPARATE ENTRY GATE",
    "ASSIST AT IMMIGRATION COUNTERS",
    "ASSIST IN S.H.A (SECURITY HOLD AREA)",
    "DROP OFF TILL BOARDING GATE.",
]

TRV_INTERNATIONAL_ARRIVAL_SILVER_FEATURES = [
    "WELCOME GUEST FROM END OF THE AEROBRIDGE.",
    "DEDICATED STAFF WITH PLACARD.",
    "ASSIST THROUGH IMMIGRATION",
    "ASSIST IN BAGGAGE BELT AREA",
    "DROP OFF TILL CAR PARKING AREA.",
]


def seed_trv_production_packages(db: Session, trv_airport: SupportedAirport, service_map: dict[str, Service]):
    """Thiruvananthapuram (TRV) — 6 active packages (4 domestic + 2 international silver)."""
    print("\n-- Configuring Production Packages for Thiruvananthapuram Airport (TRV) --")

    silver_svc = service_map.get("silver")
    elite_svc = service_map.get("elite")
    if not silver_svc or not elite_svc:
        raise RuntimeError("TRV requires catalog services slug=silver and slug=elite")

    db.query(AirportService).filter(AirportService.airport_id == trv_airport.id).delete(synchronize_session=False)
    db.flush()

    packages = [
        (silver_svc, "DEPARTURE", "DOMESTIC", 3500.00, TRV_DOMESTIC_DEPARTURE_SILVER_FEATURES, 1,
         "Domestic departure assist from curbside to the boarding gate."),
        (elite_svc, "DEPARTURE", "DOMESTIC", 5500.00, TRV_DOMESTIC_DEPARTURE_ELITE_FEATURES, 2,
         "Premium domestic departure assist with lounge access."),
        (silver_svc, "ARRIVAL", "DOMESTIC", 3500.00, TRV_DOMESTIC_ARRIVAL_SILVER_FEATURES, 1,
         "Domestic arrival assist from the aerobridge to car parking."),
        (elite_svc, "ARRIVAL", "DOMESTIC", 5500.00, TRV_DOMESTIC_ARRIVAL_ELITE_FEATURES, 2,
         "Premium domestic arrival assist with lounge access."),
        (silver_svc, "DEPARTURE", "INTERNATIONAL", 4500.00, TRV_INTERNATIONAL_DEPARTURE_SILVER_FEATURES, 1,
         "International departure assist from curbside through immigration to the gate."),
        (silver_svc, "ARRIVAL", "INTERNATIONAL", 4500.00, TRV_INTERNATIONAL_ARRIVAL_SILVER_FEATURES, 1,
         "International arrival assist from the aerobridge through immigration to parking."),
    ]
    for svc, journey, flight, price, features, priority, desc in packages:
        db.add(AirportService(
            id=uuid.uuid4(),
            airport_id=trv_airport.id,
            service_id=svc.id,
            journey_type=journey,
            flight_type=flight,
            short_description=desc,
            features=list(features),
            additional_benefits=[],
            min_booking_notice_hours=6,
            is_available=True,
            display_priority=priority,
            price=price,
            currency="INR",
        ))
    db.flush()
    print("  + Created TRV Production Packages: 6 active (Dom Dep/Arr Silver+Elite, Intl Dep/Arr Silver)")
