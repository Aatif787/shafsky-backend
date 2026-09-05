"""
Journey Detection Engine — Database Seeder (Phase 7 Production Packages).

Master Facade & Orchestrator for Shafsky Aviation Airport Concierge Catalog.
Modularized per airport under `app/seeds/airports/`.
All airport constants and seed functions are re-exported here for 100% backward compatibility.
"""

import uuid
from sqlalchemy import text
from sqlalchemy.orm import Session
from app.database import SessionLocal
from app.models.journey_models import SupportedAirport, Service, AirportService
from app.seeds.airports import *
from app.seeds.airports import AIRPORT_SEEDERS

AIRPORTS = [
    {
        "airport_name": "Indira Gandhi International Airport",
        "iata_code": "DEL",
        "icao_code": "VIDP",
        "city": "Delhi",
        "country": "India",
        "timezone": "Asia/Kolkata",
        "is_supported": True,
        "is_active": True,
    },
    {
        "airport_name": "Chhatrapati Shivaji Maharaj International Airport",
        "iata_code": "BOM",
        "icao_code": "VABB",
        "city": "Mumbai",
        "country": "India",
        "timezone": "Asia/Kolkata",
        "is_supported": True,
        "is_active": True,
    },
    {
        "airport_name": "Sardar Vallabhbhai Patel International Airport",
        "iata_code": "AMD",
        "icao_code": "VAAH",
        "city": "Ahmedabad",
        "country": "India",
        "timezone": "Asia/Kolkata",
        "is_supported": True,
        "is_active": True,
    },
    {
        "airport_name": "Rajiv Gandhi International Airport",
        "iata_code": "HYD",
        "icao_code": "VOHS",
        "city": "Hyderabad",
        "country": "India",
        "timezone": "Asia/Kolkata",
        "is_supported": True,
        "is_active": True,
    },
    {
        "airport_name": "Chaudhary Charan Singh International Airport",
        "iata_code": "LKO",
        "icao_code": "VILK",
        "city": "Lucknow",
        "country": "India",
        "timezone": "Asia/Kolkata",
        "is_supported": True,
        "is_active": True,
    },
    {
        "airport_name": "Netaji Subhas Chandra Bose International Airport",
        "iata_code": "CCU",
        "icao_code": "VECC",
        "city": "Kolkata",
        "country": "India",
        "timezone": "Asia/Kolkata",
        "is_supported": True,
        "is_active": True,
    },
    {
        "airport_name": "Dabolim International Airport",
        "iata_code": "GOI",
        "icao_code": "VOGO",
        "city": "Goa",
        "country": "India",
        "timezone": "Asia/Kolkata",
        "is_supported": True,
        "is_active": True,
    },
    {
        "airport_name": "Jaipur International Airport",
        "iata_code": "JAI",
        "icao_code": "VIJP",
        "city": "Jaipur",
        "country": "India",
        "timezone": "Asia/Kolkata",
        "is_supported": True,
        "is_active": True,
    },
    {
        "airport_name": "Sri Guru Ram Dass Jee International Airport",
        "iata_code": "ATQ",
        "icao_code": "VIAR",
        "city": "Amritsar",
        "country": "India",
        "timezone": "Asia/Kolkata",
        "is_supported": True,
        "is_active": True,
    },
    {
        "airport_name": "Kempegowda International Airport",
        "iata_code": "BLR",
        "icao_code": "VOBL",
        "city": "Bengaluru",
        "country": "India",
        "timezone": "Asia/Kolkata",
        "is_supported": True,
        "is_active": True,
    },
    {
        "airport_name": "Chennai International Airport",
        "iata_code": "MAA",
        "icao_code": "VOMM",
        "city": "Chennai",
        "country": "India",
        "timezone": "Asia/Kolkata",
        "is_supported": True,
        "is_active": True,
    },
    {
        "airport_name": "Cochin International Airport",
        "iata_code": "COK",
        "icao_code": "VOCI",
        "city": "Kochi",
        "country": "India",
        "timezone": "Asia/Kolkata",
        "is_supported": True,
        "is_active": True,
    },
    {
        "airport_name": "Thiruvananthapuram International Airport",
        "iata_code": "TRV",
        "icao_code": "VOTV",
        "city": "Thiruvananthapuram",
        "country": "India",
        "timezone": "Asia/Kolkata",
        "is_supported": True,
        "is_active": True,
    },
    {
        "airport_name": "Visakhapatnam International Airport",
        "iata_code": "VTZ",
        "icao_code": "VOVZ",
        "city": "Visakhapatnam",
        "country": "India",
        "timezone": "Asia/Kolkata",
        "is_supported": True,
        "is_active": True,
    },
    {
        "airport_name": "Biju Patnaik International Airport",
        "iata_code": "BBI",
        "icao_code": "VEBS",
        "city": "Bhubaneswar",
        "country": "India",
        "timezone": "Asia/Kolkata",
        "is_supported": True,
        "is_active": True,
    },
    {
        "airport_name": "Shaheed Bhagat Singh International Airport",
        "iata_code": "IXC",
        "icao_code": "VICG",
        "city": "Chandigarh",
        "country": "India",
        "timezone": "Asia/Kolkata",
        "is_supported": True,
        "is_active": True,
    },
    {
        "airport_name": "Manohar International Airport (Mopa)",
        "iata_code": "GOX",
        "icao_code": "VOMY",
        "city": "Goa Mopa",
        "country": "India",
        "timezone": "Asia/Kolkata",
        "is_supported": True,
        "is_active": True,
    },
    {
        "airport_name": "Lokpriya Gopinath Bordoloi International Airport",
        "iata_code": "GAU",
        "icao_code": "VEGT",
        "city": "Guwahati",
        "country": "India",
        "timezone": "Asia/Kolkata",
        "is_supported": True,
        "is_active": True,
    },
    {
        "airport_name": "Mangaluru International Airport",
        "iata_code": "IXE",
        "icao_code": "VOML",
        "city": "Mangaluru",
        "country": "India",
        "timezone": "Asia/Kolkata",
        "is_supported": True,
        "is_active": True,
    },
    {
        "airport_name": "Birsa Munda Airport",
        "iata_code": "IXR",
        "icao_code": "VERC",
        "city": "Ranchi",
        "country": "India",
        "timezone": "Asia/Kolkata",
        "is_supported": True,
        "is_active": True,
    },
]

SERVICES = [
    {
        "name": "Platinum Service",
        "slug": "platinum",
        "description": "Premium airport arrival assist with baggage support and meet & greet.",
        "icon": "Crown",
        "display_order": 1,
        "is_active": True,
    },
    {
        "name": "Elite Service",
        "slug": "elite",
        "description": "Premium airport arrival assist with dedicated baggage support and complete arrival coordination.",
        "icon": "Sparkles",
        "display_order": 2,
        "is_active": True,
    },
    {
        "name": "Elite Plus Service",
        "slug": "elite_plus",
        "description": "Complete premium airport assist with flexible booking benefits.",
        "icon": "Award",
        "display_order": 3,
        "is_active": True,
    },
    {
        "name": "Silver Service",
        "slug": "silver",
        "description": "Premium domestic departure assist from curbside to the boarding gate with dedicated airport support.",
        "icon": "ShieldCheck",
        "display_order": 4,
        "is_active": True,
    },
    {
        "name": "Gold Service",
        "slug": "gold",
        "description": "Premium domestic departure assist with lounge access and airport support from curbside to the boarding gate.",
        "icon": "Award",
        "display_order": 4,
        "is_active": True,
    },
    {
        "name": "Meet & Greet Escort",
        "slug": "meet_greet",
        "description": "Personal escort through terminal arrival, security & baggage retrieval.",
        "icon": "Sparkles",
        "display_order": 4,
        "is_active": True,
    },
    {
        "name": "VIP Fast Track",
        "slug": "fast_track",
        "description": "Priority queue access through security & immigration channels.",
        "icon": "Zap",
        "display_order": 5,
        "is_active": True,
    },
    {
        "name": "VIP Lounge Access",
        "slug": "lounge",
        "description": "Access to executive lounge sanctuary with dining & refreshment.",
        "icon": "Hotel",
        "display_order": 6,
        "is_active": True,
    },
]

JOURNEY_TYPES = ["ARRIVAL", "DEPARTURE", "TRANSIT"]


def seed_airports(db: Session) -> dict[str, SupportedAirport]:
    airport_map = {}
    for data in AIRPORTS:
        existing = db.query(SupportedAirport).filter_by(iata_code=data["iata_code"]).first()
        if existing:
            print(f"  [OK] Airport {data['iata_code']} exists.")
            airport_map[data["iata_code"]] = existing
        else:
            airport = SupportedAirport(id=uuid.uuid4(), **data)
            db.add(airport)
            airport_map[data["iata_code"]] = airport
            print(f"  + Created airport: {data['iata_code']}")
    db.flush()
    return airport_map


def seed_services(db: Session) -> dict[str, Service]:
    service_map = {}
    for data in SERVICES:
        existing = db.query(Service).filter_by(slug=data["slug"]).first()
        if existing:
            existing.name = str(data["name"])
            existing.description = str(data["description"])
            service_map[data["slug"]] = existing
            print(f"  [OK] Service '{data['slug']}' exists.")
        else:
            service = Service(id=uuid.uuid4(), **data)
            db.add(service)
            service_map[data["slug"]] = service
            print(f"  + Created service: {data['slug']}")
    db.flush()
    return service_map


def seed_other_airport_services(db: Session, airport_map: dict[str, SupportedAirport], service_map: dict[str, Service]):
    """
    Seed default services for remaining airports without creating unwanted standalone mappings.
    Idempotent: Preserves existing deactivations and does not pollute package menus.
    """
    custom_airports = {
        "AMD", "BOM", "GOI", "JAI", "ATQ", "GAU", "BBI", "VTZ", "MAA", "IXE",
        "DEL", "HYD", "LKO", "CCU", "COK", "CNN", "GOX", "IXC", "IXR", "TRV", "BLR"
    }
    # All 20 airports have designated authoritative packages or specific configurations.
    # We do not automatically seed active standalone items (meet_greet, fast_track, lounge)
    # into the primary package selection catalog.
    for code, airport in airport_map.items():
        if code in custom_airports:
            continue
        # For any future unconfigured airport, only configure if explicitly defined.
    db.flush()


def run_seed():
    print("\n==================================================")
    print("  Shafsky Aviation -- Production Package Seeder   ")
    print("==================================================\n")

    db = SessionLocal()
    try:
        from sqlalchemy import text
        db.execute(text("ALTER TABLE airport_services ADD COLUMN IF NOT EXISTS terminal VARCHAR(50);"))
        db.execute(text("ALTER TABLE airport_services DROP CONSTRAINT IF EXISTS uq_airport_service_journey_flight;"))
        db.commit()

        airport_map = seed_airports(db)
        service_map = seed_services(db)

        for code, seeder_fn in AIRPORT_SEEDERS.items():
            if code in airport_map:
                seeder_fn(db, airport_map[code], service_map)

        seed_other_airport_services(db, airport_map, service_map)

        db.commit()
        print("\n[OK] Production packages seeded successfully!\n")
    except Exception as e:
        db.rollback()
        print(f"\n[ERROR] Seeding failed: {e}\n")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    run_seed()
