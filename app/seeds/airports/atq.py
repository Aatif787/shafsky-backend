import uuid
from typing import Iterable, Optional
from sqlalchemy.orm import Session
from app.models.journey_models import SupportedAirport, Service, AirportService


ATQ_DOMESTIC_DEPARTURE_DESCRIPTION = (
    "Domestic departure assist from the curbside area to the boarding gate."
)
ATQ_DOMESTIC_DEPARTURE_FEATURES = [
    "WELCOME GUEST FROM CURBSIDE AREA",
    "PORTER SERVICE WITH DEDICATED STAFF",
    "WHEELCHAIR SERVICE AVAILABLE (THROUGH AIRLINES)",
    "ASSIST FROM SEPARATE ENTRY GATE",
    "ASSIST TO BAGGAGE WRAPPING FACILITIES",
    "ASSIST AT SEPARATE CHECKIN PROCESS AT COUNTERS",
    "ASSIST IN S.H.A.(SECURITY HOLD AREA)",
    "LOUNGE SERVICE FACILITY AVAILABLE (CHARGES APPLICABLE)",
    "ASSIST GUEST TILL THE BOARDING GATE",
]

ATQ_DOMESTIC_ARRIVAL_DESCRIPTION = (
    "Domestic arrival assist from the aerobridge to the car parking area."
)
ATQ_DOMESTIC_ARRIVAL_FEATURES = [
    "WELCOME GUEST FROM END OF THE AEROBRIDGE",
    "DEDICATED STAFF WITH PLACARD",
    "PORTER SERVICE WITH DEDICATED STAFF AT ARRIVALS",
    "WHEELCHAIR SERVICE AVAILABLE (THROUGH AIRLINES)",
    "ASSIST IN BAGGAGE BELT AREA",
    "ASSIST GUEST TILL THE CAR PARKING AREA",
]

ATQ_INTERNATIONAL_DEPARTURE_DESCRIPTION = (
    "International departure assist from the curbside area to the boarding gate."
)
ATQ_INTERNATIONAL_DEPARTURE_FEATURES = [
    "WELCOME GUEST FROM CURBSIDE AREA",
    "PORTER SERVICE WITH DEDICATED STAFF",
    "WHEELCHAIR SERVICE AVAILABLE (THROUGH AIRLINES)",
    "ASSIST FROM SEPARATE ENTRY GATE",
    "ASSIST TO BAGGAGE WRAPPING FACILITIES",
    "ASSIST AT SEPARATE CHECK IN PROCESS AT COUNTERS",
    "ASSIST IN S.H.A.(SECURITY HOLD AREA)",
    "LOUNGE SERVICE FACILITY AVAILABLE (CHARGES APPLICABLE)",
    "ASSIST GUEST TILL THE BOARDING GATE",
]

ATQ_INTERNATIONAL_ARRIVAL_DESCRIPTION = (
    "International arrival assist from post-immigration to the parking area."
)
ATQ_INTERNATIONAL_ARRIVAL_FEATURES = [
    "WELCOME GUEST FROM POST IMMIGRATION.",
    "PORTER SERVICE WITH DEDICATED STAFF AT POST IMMIGRATION AREA.",
    "ASSIST IN BAGGAGE BELT AREA.",
    "ASSIST IN CUSTOMS.",
    "COORDINATION WITH RECEIVING PARTY.",
    "ASSIST GUEST TILL THE CAR PARKING AREA",
]

ATQ_TRANSIT_DOMESTIC_DOMESTIC_DESCRIPTION = (
    "Domestic-to-Domestic transit assist from arrival gate to the connecting boarding gate."
)
ATQ_TRANSIT_DOMESTIC_DOMESTIC_FEATURES = [
    "WELCOME GUEST FROM END OF THE AEROBRIDGE",
    "DEDICATED STAFF WITH PLACARD",
    "PORTER SERVICE WITH DEDICATED STAFF AT ARRIVALS",
    "WHEELCHAIR SERVICE AVAILABLE (THROUGH AIRLINES)",
    "ASSIST IN BAGGAGE BELT AREA (IF REQUIRED)",
    "ASSIST AT SEPARATE CHECKIN PROCESS AT COUNTERS",
    "ASSIST IN S.H.A.(SECURITY HOLD AREA)",
    "LOUNGE SERVICE FACILITY AVAILABLE (CHARGES APPLICABLE)",
    "ASSIST GUEST TILL THE BOARDING GATE",
]

ATQ_TRANSIT_INTERNATIONAL_DOMESTIC_DESCRIPTION = (
    "International-to-Domestic transit assist from post-immigration to the connecting boarding gate."
)
ATQ_TRANSIT_INTERNATIONAL_DOMESTIC_FEATURES = [
    "WELCOME GUEST FROM POST IMMIGRATION.",
    "DEDICATED STAFF WITH PLACARD",
    "PORTER SERVICE WITH DEDICATED STAFF AT POST IMMIGRATION AREA.",
    "WHEELCHAIR SERVICE AVAILABLE (THROUGH AIRLINES)",
    "ASSIST IN BAGGAGE BELT AREA.",
    "ASSIST IN CUSTOMS.",
    "ASSIST AT SEPARATE CHECKIN PROCESS AT COUNTERS",
    "ASSIST IN S.H.A.(SECURITY HOLD AREA)",
    "LOUNGE SERVICE FACILITY AVAILABLE (CHARGES APPLICABLE)",
    "ASSIST GUEST TILL THE BOARDING GATE",
]

ATQ_TRANSIT_DOMESTIC_INTERNATIONAL_DESCRIPTION = (
    "Domestic-to-International transit assist from arrival gate to the international boarding gate."
)
ATQ_TRANSIT_DOMESTIC_INTERNATIONAL_FEATURES = [
    "WELCOME GUEST FROM END OF THE AEROBRIDGE",
    "DEDICATED STAFF WITH PLACARD",
    "PORTER SERVICE WITH DEDICATED STAFF",
    "WHEELCHAIR SERVICE AVAILABLE (THROUGH AIRLINES)",
    "ASSIST IN BAGGAGE BELT AREA (IF REQUIRED)",
    "ASSIST AT SEPARATE CHECK IN PROCESS AT COUNTERS",
    "ASSIST IN S.H.A.(SECURITY HOLD AREA)",
    "LOUNGE SERVICE FACILITY AVAILABLE (CHARGES APPLICABLE)",
    "ASSIST GUEST TILL THE BOARDING GATE",
]


def _upsert_atq_service(
    db: Session,
    airport_id: uuid.UUID,
    service: Service,
    journey_type: str,
    flight_type: str,
    short_description: str,
    features: list[str],
    price: float,
    display_priority: int,
    min_booking_notice_hours: int = 6,
    terminal: Optional[str] = None,
) -> AirportService:
    """
    Idempotently upsert an ATQ AirportService row without deleting existing records.
    Preserves the primary row's UUID across repeated seed executions and deactivates
    any duplicate rows matching the same composite key.
    """
    existing_rows = (
        db.query(AirportService)
        .filter(
            AirportService.airport_id == airport_id,
            AirportService.service_id == service.id,
            AirportService.journey_type == journey_type,
            AirportService.flight_type == flight_type,
            AirportService.terminal.is_(None)
            if terminal is None
            else AirportService.terminal == terminal,
        )
        .order_by(AirportService.is_available.desc(), AirportService.created_at.asc())
        .all()
    )

    if existing_rows:
        primary = existing_rows[0]
        primary.short_description = short_description
        primary.features = list(features)
        primary.additional_benefits = []
        primary.min_booking_notice_hours = min_booking_notice_hours
        primary.is_available = True
        primary.display_priority = display_priority
        primary.price = price
        primary.currency = "INR"
        for duplicate in existing_rows[1:]:
            duplicate.is_available = False
        return primary

    row = AirportService(
        id=uuid.uuid4(),
        airport_id=airport_id,
        service_id=service.id,
        journey_type=journey_type,
        flight_type=flight_type,
        terminal=terminal,
        short_description=short_description,
        features=list(features),
        additional_benefits=[],
        min_booking_notice_hours=min_booking_notice_hours,
        is_available=True,
        display_priority=display_priority,
        price=price,
        currency="INR",
    )
    db.add(row)
    return row


def _deactivate_superseded_atq_rows(
    db: Session,
    airport_id: uuid.UUID,
    journey_types: Iterable[str],
    flight_types: Iterable[str],
    target_service_ids: set[uuid.UUID],
) -> None:
    """
    Non-destructively mark explicitly superseded ATQ package rows unavailable
    without deleting existing mappings or touching unrelated add-on/service records.
    """
    if not target_service_ids:
        return
    rows = (
        db.query(AirportService)
        .filter(
            AirportService.airport_id == airport_id,
            AirportService.journey_type.in_(list(journey_types)),
            AirportService.flight_type.in_(list(flight_types)),
            AirportService.service_id.in_(list(target_service_ids)),
            AirportService.terminal.is_(None),
            AirportService.is_available.is_(True),
        )
        .all()
    )
    for row in rows:
        row.is_available = False


def seed_atq_production_packages(
    db: Session,
    atq_airport: SupportedAirport,
    service_map: dict[str, Service],
):
    """
    For Amritsar Airport (ATQ — Sri Guru Ram Dass Jee International Airport):
    Non-destructively upserts production Silver, Elite, and Transit packages:
      - Domestic Departure & Arrival: Silver (₹2,500), Elite (₹4,000)
      - International Arrival: Silver (₹2,500), Elite (₹4,500)
      - International Departure: Silver (₹2,500), Elite (₹4,500)
      - Transit (D→D, I→D, D→I): ₹5,000 each
    Preserves existing ATQ inclusions/descriptions and never deletes existing records.
    """
    print("\n-- Configuring Production Packages for Amritsar (ATQ) --")

    silver_svc = service_map.get("silver")
    elite_svc = service_map.get("elite")
    meet_greet_svc = service_map.get("meet_greet")

    if not silver_svc or not elite_svc or not meet_greet_svc:
        print("  ! Error: Silver, Elite, or Meet & Greet service not found in service_map")
        return

    # Ensure legacy meet_greet Arrival/Departure rows are non-destructively deactivated
    # while preserving Silver & Elite as the authoritative ATQ package mappings and
    # leaving unrelated add-on/other service records untouched.
    _deactivate_superseded_atq_rows(
        db=db,
        airport_id=atq_airport.id,
        journey_types=("DEPARTURE", "ARRIVAL"),
        flight_types=("DOMESTIC", "INTERNATIONAL"),
        target_service_ids={meet_greet_svc.id},
    )

    # Ensure any unsupported International-to-International transit package row is inactive for ATQ.
    _deactivate_superseded_atq_rows(
        db=db,
        airport_id=atq_airport.id,
        journey_types=("TRANSIT",),
        flight_types=("INTERNATIONAL_INTERNATIONAL",),
        target_service_ids={meet_greet_svc.id, silver_svc.id, elite_svc.id},
    )

    # ── 1. DOMESTIC SERVICES ──
    # Domestic Departure: Silver (₹2,500), Elite (₹4,000)
    _upsert_atq_service(
        db=db,
        airport_id=atq_airport.id,
        service=silver_svc,
        journey_type="DEPARTURE",
        flight_type="DOMESTIC",
        short_description=ATQ_DOMESTIC_DEPARTURE_DESCRIPTION,
        features=ATQ_DOMESTIC_DEPARTURE_FEATURES,
        price=2500.00,
        display_priority=1,
    )
    _upsert_atq_service(
        db=db,
        airport_id=atq_airport.id,
        service=elite_svc,
        journey_type="DEPARTURE",
        flight_type="DOMESTIC",
        short_description=ATQ_DOMESTIC_DEPARTURE_DESCRIPTION,
        features=ATQ_DOMESTIC_DEPARTURE_FEATURES,
        price=4000.00,
        display_priority=2,
    )

    # Domestic Arrival: Silver (₹2,500), Elite (₹4,000)
    _upsert_atq_service(
        db=db,
        airport_id=atq_airport.id,
        service=silver_svc,
        journey_type="ARRIVAL",
        flight_type="DOMESTIC",
        short_description=ATQ_DOMESTIC_ARRIVAL_DESCRIPTION,
        features=ATQ_DOMESTIC_ARRIVAL_FEATURES,
        price=2500.00,
        display_priority=1,
    )
    _upsert_atq_service(
        db=db,
        airport_id=atq_airport.id,
        service=elite_svc,
        journey_type="ARRIVAL",
        flight_type="DOMESTIC",
        short_description=ATQ_DOMESTIC_ARRIVAL_DESCRIPTION,
        features=ATQ_DOMESTIC_ARRIVAL_FEATURES,
        price=4000.00,
        display_priority=2,
    )

    # ── 2. INTERNATIONAL SERVICES ──
    # International Departure: Silver (₹2,500), Elite (₹4,500)
    _upsert_atq_service(
        db=db,
        airport_id=atq_airport.id,
        service=silver_svc,
        journey_type="DEPARTURE",
        flight_type="INTERNATIONAL",
        short_description=ATQ_INTERNATIONAL_DEPARTURE_DESCRIPTION,
        features=ATQ_INTERNATIONAL_DEPARTURE_FEATURES,
        price=2500.00,
        display_priority=1,
    )
    _upsert_atq_service(
        db=db,
        airport_id=atq_airport.id,
        service=elite_svc,
        journey_type="DEPARTURE",
        flight_type="INTERNATIONAL",
        short_description=ATQ_INTERNATIONAL_DEPARTURE_DESCRIPTION,
        features=ATQ_INTERNATIONAL_DEPARTURE_FEATURES,
        price=4500.00,
        display_priority=2,
    )

    # International Arrival: Silver (₹2,500), Elite (₹4,500)
    _upsert_atq_service(
        db=db,
        airport_id=atq_airport.id,
        service=silver_svc,
        journey_type="ARRIVAL",
        flight_type="INTERNATIONAL",
        short_description=ATQ_INTERNATIONAL_ARRIVAL_DESCRIPTION,
        features=ATQ_INTERNATIONAL_ARRIVAL_FEATURES,
        price=2500.00,
        display_priority=1,
    )
    _upsert_atq_service(
        db=db,
        airport_id=atq_airport.id,
        service=elite_svc,
        journey_type="ARRIVAL",
        flight_type="INTERNATIONAL",
        short_description=ATQ_INTERNATIONAL_ARRIVAL_DESCRIPTION,
        features=ATQ_INTERNATIONAL_ARRIVAL_FEATURES,
        price=4500.00,
        display_priority=2,
    )

    # ── 3. TRANSIT SERVICES (All 3 combinations at ₹5,000) ──
    _upsert_atq_service(
        db=db,
        airport_id=atq_airport.id,
        service=meet_greet_svc,
        journey_type="TRANSIT",
        flight_type="DOMESTIC_DOMESTIC",
        short_description=ATQ_TRANSIT_DOMESTIC_DOMESTIC_DESCRIPTION,
        features=ATQ_TRANSIT_DOMESTIC_DOMESTIC_FEATURES,
        price=5000.00,
        display_priority=1,
    )
    _upsert_atq_service(
        db=db,
        airport_id=atq_airport.id,
        service=meet_greet_svc,
        journey_type="TRANSIT",
        flight_type="INTERNATIONAL_DOMESTIC",
        short_description=ATQ_TRANSIT_INTERNATIONAL_DOMESTIC_DESCRIPTION,
        features=ATQ_TRANSIT_INTERNATIONAL_DOMESTIC_FEATURES,
        price=5000.00,
        display_priority=2,
    )
    _upsert_atq_service(
        db=db,
        airport_id=atq_airport.id,
        service=meet_greet_svc,
        journey_type="TRANSIT",
        flight_type="DOMESTIC_INTERNATIONAL",
        short_description=ATQ_TRANSIT_DOMESTIC_INTERNATIONAL_DESCRIPTION,
        features=ATQ_TRANSIT_DOMESTIC_INTERNATIONAL_FEATURES,
        price=5000.00,
        display_priority=3,
    )

    # Deactivate conflicting package-tier rows on supported TRANSIT routes without touching unrelated records
    _deactivate_superseded_atq_rows(
        db=db,
        airport_id=atq_airport.id,
        journey_types=("TRANSIT",),
        flight_types=("DOMESTIC_DOMESTIC", "INTERNATIONAL_DOMESTIC", "DOMESTIC_INTERNATIONAL"),
        target_service_ids={silver_svc.id, elite_svc.id},
    )

    db.flush()
    print(
        "  + Upserted ATQ Production Packages: "
        "Domestic Silver (₹2,500)/Elite (₹4,000), "
        "International Silver (₹2,500)/Elite (₹4,500), "
        "Transit D->D / I->D / D->I (₹5,000)"
    )
