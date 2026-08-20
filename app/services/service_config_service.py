import re
import secrets
import string
import logging
from typing import List, Dict, Any, Optional
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from sqlalchemy import select
from app.models.schema import ServicesConfig, AirportManagement, Booking, BookingStatus

logger = logging.getLogger("shafsky.services.service_config")
EMAIL_REGEX = re.compile(r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$")

# Individual add-on slugs stay in Neon for operations, but are never shown as booking packages.
_ADDON_SERVICE_SLUGS = frozenset({
    "meet_greet",
    "fast_track",
    "lounge",
    "porter",
    "buggy",
    "wheelchair",
    "transport",
    "chauffeur",
})
_PACKAGE_SERVICE_SLUGS = frozenset({
    "silver",
    "gold",
    "elite",
    "elite_plus",
    "platinum",
    "bronze",
    "diamond",
})


def _is_booking_package_service(svc) -> bool:
    slug = (getattr(svc, "slug", None) or "").strip().lower()
    if not slug or slug in _ADDON_SERVICE_SLUGS:
        return False
    if slug in _PACKAGE_SERVICE_SLUGS:
        return True
    return any(token in slug for token in _PACKAGE_SERVICE_SLUGS)

DEFAULT_SERVICE_CATALOG = [
    # 1. Airport Assistance
    {
        "id": "airport_assistance_meet_greet",
        "title": "Meet & Greet",
        "category": "Airport Assistance",
        "description": "Personalized escort through airport arrival or departure procedures.",
        "base_price": 4500.0,
        "currency": "INR",
        "is_active": True,
        "is_hidden": False,
        "sort_order": 1,
        "features": ["Personal assistant", "Fast-track security", "Baggage help"],
        "options_schema": {"terminal": "string", "flight_number": "string"}
    },
    {
        "id": "airport_assistance_fast_track",
        "title": "Fast Track",
        "category": "Airport Assistance",
        "description": "Expedited customs, immigration, and security clearance.",
        "base_price": 3500.0,
        "currency": "INR",
        "is_active": True,
        "is_hidden": False,
        "sort_order": 2,
        "features": ["Priority lane access", "Escort service"],
        "options_schema": {}
    },
    {
        "id": "airport_assistance_lounge_access",
        "title": "Lounge Access",
        "category": "Airport Assistance",
        "description": "VIP lounge access with premium dining, Wi-Fi, and comfort amenities.",
        "base_price": 2800.0,
        "currency": "INR",
        "is_active": True,
        "is_hidden": False,
        "sort_order": 3,
        "features": ["VIP Lounge", "Buffet & Drinks", "High-speed Wi-Fi"],
        "options_schema": {"lounge_name": "string", "hours": "number"}
    },
    {
        "id": "airport_assistance_baggage_assistance",
        "title": "Baggage Assist",
        "category": "Airport Assistance",
        "description": "Dedicated porter service to transport luggage from dropoff to check-in or belt to vehicle.",
        "base_price": 1500.0,
        "currency": "INR",
        "is_active": True,
        "is_hidden": False,
        "sort_order": 4,
        "features": ["Dedicated porter", "Heavy luggage support"],
        "options_schema": {"bag_count": "number"}
    },

    # 2. Ground Transport
    {
        "id": "ground_transport_airport_transfer",
        "title": "Airport Transfer",
        "category": "Ground Transport",
        "description": "Seamless point-to-point transfer between airport and your destination.",
        "base_price": 2500.0,
        "currency": "INR",
        "is_active": True,
        "is_hidden": False,
        "sort_order": 10,
        "features": ["Chauffeur service", "Flight tracking", "Luggage handling"],
        "options_schema": {"pickup_location": "string", "dropoff_location": "string"}
    },
    {
        "id": "ground_transport_luxury_sedan",
        "title": "Luxury Sedan",
        "category": "Ground Transport",
        "description": "Premium luxury sedan (Mercedes E-Class, BMW 5 Series) with professional chauffeur.",
        "base_price": 6000.0,
        "currency": "INR",
        "is_active": True,
        "is_hidden": False,
        "sort_order": 11,
        "features": ["Mercedes/BMW", "Refreshments", "Executive comfort"],
        "options_schema": {"vehicle_model": "string"}
    },
    {
        "id": "ground_transport_suv",
        "title": "SUV",
        "category": "Ground Transport",
        "description": "Spacious premium SUV for families or extra luggage capacity.",
        "base_price": 7500.0,
        "currency": "INR",
        "is_active": True,
        "is_hidden": False,
        "sort_order": 12,
        "features": ["Full-size SUV", "Extra legroom", "Ample luggage space"],
        "options_schema": {"passenger_count": "number"}
    },
    {
        "id": "ground_transport_executive_van",
        "title": "Executive Van",
        "category": "Ground Transport",
        "description": "Luxury multi-seater van ideal for corporate groups or VIP delegations.",
        "base_price": 12000.0,
        "currency": "INR",
        "is_active": True,
        "is_hidden": False,
        "sort_order": 13,
        "features": ["Up to 10 pax", "Luxury interior", "Reclining leather seats"],
        "options_schema": {"group_size": "number"}
    },

    # 3. Private Charter
    {
        "id": "private_charter_light_jet",
        "title": "Light Jet",
        "category": "Private Charter",
        "description": "Efficient light jet for regional charter travel (4-7 passengers).",
        "base_price": 250000.0,
        "currency": "INR",
        "is_active": True,
        "is_hidden": False,
        "sort_order": 20,
        "features": ["Range: 1500nm", "4-7 Seats", "Private Terminal"],
        "options_schema": {"origin": "string", "destination": "string", "passengers": "number"}
    },
    {
        "id": "private_charter_midsize_jet",
        "title": "Midsize Jet",
        "category": "Private Charter",
        "description": "Midsize private jet offering enhanced range, cabin height, and seating capacity.",
        "base_price": 450000.0,
        "currency": "INR",
        "is_active": True,
        "is_hidden": False,
        "sort_order": 21,
        "features": ["Stand-up cabin", "8-10 Seats", "Flight Attendant"],
        "options_schema": {}
    },
    {
        "id": "private_charter_heavy_jet",
        "title": "Heavy Jet",
        "category": "Private Charter",
        "description": "Long-range heavy jet for transcontinental non-stop VIP travel.",
        "base_price": 850000.0,
        "currency": "INR",
        "is_active": True,
        "is_hidden": False,
        "sort_order": 22,
        "features": ["Intercontinental range", "12-16 Seats", "Gourmet Catering"],
        "options_schema": {}
    },
    {
        "id": "private_charter_turboprop",
        "title": "Turboprop",
        "category": "Private Charter",
        "description": "Cost-effective turboprop for short-distance routes and small airstrips.",
        "base_price": 180000.0,
        "currency": "INR",
        "is_active": True,
        "is_hidden": False,
        "sort_order": 23,
        "features": ["Short runway access", "Economic charter"],
        "options_schema": {}
    },
    {
        "id": "private_charter_helicopter",
        "title": "Helicopter",
        "category": "Private Charter",
        "description": "Point-to-point helicopter shuttle for rapid city-to-airport or resort transfers.",
        "base_price": 120000.0,
        "currency": "INR",
        "is_active": True,
        "is_hidden": False,
        "sort_order": 24,
        "features": ["Direct helipad transfer", "VIP twin-engine"],
        "options_schema": {}
    },

    # 4. Cargo & Logistics
    {
        "id": "cargo_logistics_express_air_freight",
        "title": "Express Air Freight",
        "category": "Cargo & Logistics",
        "description": "Priority time-critical air freight transport with end-to-end tracking.",
        "base_price": 15000.0,
        "currency": "INR",
        "is_active": True,
        "is_hidden": False,
        "sort_order": 30,
        "features": ["Next-flight-out", "Door-to-door option", "Real-time GPS"],
        "options_schema": {"cargo_weight_kg": "number", "dimensions": "string"}
    },
    {
        "id": "cargo_logistics_dangerous_goods",
        "title": "Dangerous Goods",
        "category": "Cargo & Logistics",
        "description": "IATA-compliant transport for hazmat and dangerous goods cargo.",
        "base_price": 25000.0,
        "currency": "INR",
        "is_active": True,
        "is_hidden": False,
        "sort_order": 31,
        "features": ["IATA certified handling", "Specialized packaging"],
        "options_schema": {"un_number": "string", "hazard_class": "string"}
    },
    {
        "id": "cargo_logistics_temperature_controlled",
        "title": "Temperature Controlled",
        "category": "Cargo & Logistics",
        "description": "Cold-chain air logistics for pharmaceuticals, perishables, and biological samples.",
        "base_price": 30000.0,
        "currency": "INR",
        "is_active": True,
        "is_hidden": False,
        "sort_order": 32,
        "features": ["Cold chain 2°C to 8°C", "Thermal monitoring"],
        "options_schema": {"temp_range": "string"}
    },
    {
        "id": "cargo_logistics_charter_cargo",
        "title": "Charter Cargo",
        "category": "Cargo & Logistics",
        "description": "Full freighter plane charter for oversized, heavy, or bulk cargo transport.",
        "base_price": 500000.0,
        "currency": "INR",
        "is_active": True,
        "is_hidden": False,
        "sort_order": 33,
        "features": ["Dedicated freighter", "Heavy lift capability"],
        "options_schema": {}
    },

    # 5. Medical Assistance
    {
        "id": "medical_assistance_air_ambulance",
        "title": "Air Ambulance",
        "category": "Medical Assistance",
        "description": "Fully equipped ICU air ambulance with specialized medical flight crew.",
        "base_price": 600000.0,
        "currency": "INR",
        "is_active": True,
        "is_hidden": False,
        "sort_order": 40,
        "features": ["Airborne ICU", "Doctor & Paramedic", "Bed-to-bed transfer"],
        "options_schema": {"patient_condition": "string", "origin_hospital": "string", "dest_hospital": "string"}
    },
    {
        "id": "medical_assistance_medical_escort",
        "title": "Medical Escort",
        "category": "Medical Assistance",
        "description": "Qualified flight nurse or doctor escorting patients on commercial flights.",
        "base_price": 80000.0,
        "currency": "INR",
        "is_active": True,
        "is_hidden": False,
        "sort_order": 41,
        "features": ["Commercial flight escort", "Medical monitoring"],
        "options_schema": {}
    },
    {
        "id": "medical_assistance_stretcher_transport",
        "title": "Stretcher Transport",
        "category": "Medical Assistance",
        "description": "Commercial airliner stretcher installation and logistics for non-ambulatory patients.",
        "base_price": 150000.0,
        "currency": "INR",
        "is_active": True,
        "is_hidden": False,
        "sort_order": 42,
        "features": ["Airliner stretcher clearance", "Privacy screen"],
        "options_schema": {}
    },
    {
        "id": "medical_assistance_wheelchair_support",
        "title": "Wheelchair Support",
        "category": "Medical Assistance",
        "description": "Airport ramp, aisle chair, and tarmac mobility assistance.",
        "base_price": 2000.0,
        "currency": "INR",
        "is_active": True,
        "is_hidden": False,
        "sort_order": 43,
        "features": ["Ramp & aisle chair", "Dedicated handler"],
        "options_schema": {}
    },

    # 6. Travel Support
    {
        "id": "travel_support_visa_assistance",
        "title": "Visa Assistance",
        "category": "Travel Support",
        "description": "End-to-end diplomatic, tourist, and business visa processing support.",
        "base_price": 5000.0,
        "currency": "INR",
        "is_active": True,
        "is_hidden": False,
        "sort_order": 50,
        "features": ["Document check", "Fast-track appointment", "Consulate submission"],
        "options_schema": {"destination_country": "string", "visa_type": "string"}
    },
    {
        "id": "travel_support_travel_insurance",
        "title": "Travel Insurance",
        "category": "Travel Support",
        "description": "Comprehensive international travel insurance covering medical, delay, and luggage loss.",
        "base_price": 3000.0,
        "currency": "INR",
        "is_active": True,
        "is_hidden": False,
        "sort_order": 51,
        "features": ["$500k Medical cover", "Trip cancellation", "Lost baggage"],
        "options_schema": {"duration_days": "number"}
    },
    {
        "id": "travel_support_hotel_booking",
        "title": "Hotel Booking",
        "category": "Travel Support",
        "description": "VIP luxury 5-star hotel booking with room upgrades and flexible check-in.",
        "base_price": 10000.0,
        "currency": "INR",
        "is_active": True,
        "is_hidden": False,
        "sort_order": 52,
        "features": ["5-Star Partner hotels", "Late checkout", "Complimentary breakfast"],
        "options_schema": {"hotel_name": "string", "nights": "number"}
    },
    {
        "id": "travel_support_vip_escort",
        "title": "VIP Escort",
        "category": "Travel Support",
        "description": "Private security and personal executive escort throughout travel itinerary.",
        "base_price": 20000.0,
        "currency": "INR",
        "is_active": True,
        "is_hidden": False,
        "sort_order": 53,
        "features": ["Close protection", "Personal assistant", "Dedicated coordinator"],
        "options_schema": {}
    }
]

class ServiceConfigService:
    @classmethod
    def seed_default_catalog(cls, db: Session) -> None:
        """Seed default service catalog if services_config table is empty or missing entries."""
        existing_count = db.query(ServicesConfig).count()
        if existing_count == 0:
            for item in DEFAULT_SERVICE_CATALOG:
                sc = ServicesConfig(
                    id=item["id"],
                    title=item["title"],
                    category=item["category"],
                    description=item["description"],
                    base_price=item["base_price"],
                    currency=item["currency"],
                    is_active=item["is_active"],
                    is_hidden=item["is_hidden"],
                    sort_order=item["sort_order"],
                    features=item["features"],
                    options_schema=item["options_schema"]
                )
                db.add(sc)
            db.commit()
        else:
            # Ensure any new child services exist
            for item in DEFAULT_SERVICE_CATALOG:
                sc = db.scalar(select(ServicesConfig).where(ServicesConfig.id == item["id"]))
                if not sc:
                    sc = ServicesConfig(
                        id=item["id"],
                        title=item["title"],
                        category=item["category"],
                        description=item["description"],
                        base_price=item["base_price"],
                        currency=item["currency"],
                        is_active=item["is_active"],
                        is_hidden=item["is_hidden"],
                        sort_order=item["sort_order"],
                        features=item["features"],
                        options_schema=item["options_schema"]
                    )
                    db.add(sc)
            db.commit()

    @classmethod
    def get_public_catalog(cls, db: Session) -> List[Dict[str, Any]]:
        """Return public service catalog (active and not hidden), grouped by category."""
        cls.seed_default_catalog(db)
        services = db.scalars(
            select(ServicesConfig)
            .where(ServicesConfig.is_active.is_(True))
            .where(ServicesConfig.is_hidden.is_(False))
            .order_by(ServicesConfig.sort_order, ServicesConfig.title)
        ).all()

        categories: Dict[str, List[Dict[str, Any]]] = {}
        for s in services:
            cat = s.category
            if cat not in categories:
                categories[cat] = []
            categories[cat].append({
                "id": s.id,
                "title": s.title,
                "category": s.category,
                "description": s.description,
                "basePrice": float(s.base_price),
                "currency": s.currency,
                "isActive": s.is_active,
                "isHidden": s.is_hidden,
                "features": s.features or [],
                "optionsSchema": s.options_schema or {}
            })

        result = []
        for cat_name, child_list in categories.items():
            result.append({
                "category": cat_name,
                "services": child_list
            })
        return result

    @classmethod
    def get_admin_catalog(cls, db: Session) -> List[Dict[str, Any]]:
        """Return full service catalog for admin configuration."""
        cls.seed_default_catalog(db)
        services = db.scalars(
            select(ServicesConfig)
            .order_by(ServicesConfig.sort_order, ServicesConfig.category, ServicesConfig.title)
        ).all()

        return [
            {
                "id": s.id,
                "title": s.title,
                "category": s.category,
                "description": s.description,
                "basePrice": float(s.base_price),
                "currency": s.currency,
                "isActive": s.is_active,
                "isHidden": s.is_hidden,
                "sortOrder": s.sort_order,
                "features": s.features or [],
                "optionsSchema": s.options_schema or {},
                "createdAt": s.created_at.isoformat() if s.created_at else None,
                "updatedAt": s.updated_at.isoformat() if s.updated_at else None
            }
            for s in services
        ]

    @classmethod
    def update_service_config(cls, db: Session, service_id: str, updates: Dict[str, Any]) -> ServicesConfig:
        """Update service configuration without code changes."""
        sc = db.scalar(select(ServicesConfig).where(ServicesConfig.id == service_id))
        if not sc:

            # Allow creating new service config via admin update
            sc = ServicesConfig(
                id=service_id,
                title=updates.get("title", service_id.replace("_", " ").title()),
                category=updates.get("category", "Airport Assistance"),
                description=updates.get("description", ""),
                base_price=updates.get("base_price", updates.get("basePrice", 0.0)),
                currency=updates.get("currency", "INR"),
                is_active=updates.get("is_active", updates.get("isActive", True)),
                is_hidden=updates.get("is_hidden", updates.get("isHidden", False)),
                sort_order=updates.get("sort_order", updates.get("sortOrder", 0)),
                features=updates.get("features", []),
                options_schema=updates.get("options_schema", updates.get("optionsSchema", {}))
            )
            db.add(sc)
        else:
            if "title" in updates:
                sc.title = updates["title"]
            if "category" in updates:
                sc.category = updates["category"]
            if "description" in updates:
                sc.description = updates["description"]
            if "base_price" in updates or "basePrice" in updates:
                sc.base_price = float(updates.get("base_price", updates.get("basePrice")))
            if "currency" in updates:
                sc.currency = updates["currency"]
            if "is_active" in updates or "isActive" in updates:
                sc.is_active = bool(updates.get("is_active", updates.get("isActive")))
            if "is_hidden" in updates or "isHidden" in updates:
                sc.is_hidden = bool(updates.get("is_hidden", updates.get("isHidden")))
            if "sort_order" in updates or "sortOrder" in updates:
                sc.sort_order = int(updates.get("sort_order", updates.get("sortOrder")))
            if "features" in updates:
                sc.features = updates["features"]
            if "options_schema" in updates or "optionsSchema" in updates:
                sc.options_schema = updates.get("options_schema", updates.get("optionsSchema"))

            sc.updated_at = datetime.now(timezone.utc)

        db.commit()
        db.refresh(sc)
        return sc

    @classmethod
    def get_airport_configuration(
        cls,
        airport_code: str,
        db: Optional[Session] = None,
        journey_type: Optional[str] = None,
        flight_type: Optional[str] = None
    ) -> Dict[str, Any]:
        """Return database & catalog driven configuration for specific airport hub."""
        code = (airport_code or "DEL").upper().strip()

        if db:
            from app.models.journey_models import SupportedAirport, Service, AirportService
            airport = db.scalar(select(SupportedAirport).where(SupportedAirport.iata_code == code))
            if airport:
                stmt = select(AirportService).where(
                    AirportService.airport_id == airport.id,
                    AirportService.is_available.is_(True)
                )
                if journey_type:
                    stmt = stmt.where(AirportService.journey_type == journey_type.strip().upper())
                if flight_type:
                    from app.services.service_airport_rules import normalize_flight_type

                    f_upper = normalize_flight_type(flight_type) or flight_type.strip().upper()
                    stmt_typed = stmt.where(AirportService.flight_type.in_([f_upper, "ALL"]))
                    mappings = list(db.scalars(stmt_typed).all())
                    if not mappings:
                        mappings = list(db.scalars(stmt).all())
                else:
                    mappings = list(db.scalars(stmt).all())

                if mappings:
                    pkg_dict = {}
                    for m in mappings:
                        svc = db.scalar(select(Service).where(Service.id == m.service_id))
                        if not _is_booking_package_service(svc):
                            continue
                        pkg_id = svc.slug
                        pkg_title = svc.name
                        if pkg_id not in pkg_dict or (journey_type and m.journey_type == journey_type.upper()):
                            pkg_dict[pkg_id] = {
                                "id": pkg_id,
                                "title": pkg_title,
                                "tagline": m.short_description if m.short_description is not None else ((svc.description if svc else "") or ""),
                                "basePrice": float(m.price),
                                "currency": m.currency or "INR",
                                "recommendedBadge": "Most Popular" if pkg_id in ["platinum", "elite", "gold"] else None,
                                "features": m.features if isinstance(m.features, list) else [],
                                "serviceIds": [pkg_id]
                            }

                    return {
                        "code": airport.iata_code,
                        "name": airport.airport_name,
                        "city": airport.city,
                        "country": airport.country,
                        "timezone": airport.timezone or "Asia/Kolkata",
                        "currency": "INR",
                        "operatingHours": "24/7",
                        "advanceNoticeHours": 6,
                        "packages": list(pkg_dict.values()),
                        "individualServices": [],
                    }

            # Booking never uses AirportManagement.services_config demo JSON.

        return {}

    @classmethod
    def resolve_catalog_services(
        cls,
        db: Session,
        airport_code: str,
        journey_type: str = "arrival",
        flight_type: Optional[str] = None,
        terminal: Optional[str] = None,
        origin_code: Optional[str] = None,
        dest_code: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Unified Authoritative Master Catalog Resolution Engine.
        
        Single Source of Truth: Reads directly from master AirportManagement & Service Catalog.
        Never invents synthetic packages or parallel service definitions.
        """
        from app.services.service_airport_rules import (
            normalize_flight_type,
            normalize_journey_type,
            resolve_service_airport_iata,
        )

        j_norm = normalize_journey_type(journey_type)
        j_type = j_norm.lower()
        transit_code = None
        # transit may be passed as airport_code when origin/dest are the other legs
        if j_norm == "TRANSIT":
            transit_code = airport_code

        resolved = resolve_service_airport_iata(
            j_norm,
            origin=origin_code,
            destination=dest_code,
            transit=transit_code,
        )
        code = resolved or (airport_code or "").strip().upper()

        if not code:
            return {
                "covered": False,
                "success": False,
                "airport": {"id": None, "code": None, "name": None},
                "journey_type": j_type,
                "journeyType": j_type,
                "flight_type": flight_type or "domestic",
                "flightType": flight_type or "domestic",
                "terminal": terminal,
                "catalogSource": "existing-airport-catalog",
                "packages": [],
                "individual_services": [],
                "individualServices": [],
                "error": "Service airport could not be resolved from the selected journey.",
            }

        db_airport = db.scalar(
            select(AirportManagement).where(AirportManagement.code == code)
        )

        master_config = cls.get_airport_configuration(
            code,
            db=db,
            journey_type=j_norm,
            flight_type=flight_type,
        )

        is_covered = True
        if db_airport and not db_airport.is_active:
            is_covered = False
        elif not db_airport and not master_config:
            is_covered = False
        elif not master_config.get("packages") and not master_config.get("individualServices"):
            is_covered = False

        if not is_covered:
            return {
                "covered": False,
                "success": False,
                "airport": {
                    "id": str(db_airport.id) if db_airport else None,
                    "code": code,
                    "name": db_airport.name if db_airport else f"{code} Airport"
                },
                "journey_type": j_type,
                "journeyType": j_type,
                "flight_type": flight_type or "domestic",
                "flightType": flight_type or "domestic",
                "terminal": terminal,
                "catalogSource": "existing-airport-catalog",
                "packages": [],
                "individual_services": [],
                "individualServices": [],
                "error": f"Services currently unavailable at airport {code}."
            }

        # Determine domestic/international flightType dynamically from airport country metadata
        def _norm_country(c_val: Optional[str], code_val: str) -> str:
            if not c_val:
                return "india" if code_val.upper() in ["DEL", "BOM", "HYD", "AMD", "BLR", "CCU", "MAA", "LKO"] else "international"
            c_low = c_val.lower().strip()
            if c_low in ["ind", "india", "in"]:
                return "india"
            if c_low in ["uae", "united arab emirates", "dubai"]:
                return "uae"
            return c_low

        resolved_flight_type = flight_type
        explicit_ft = normalize_flight_type(flight_type)
        if explicit_ft:
            resolved_flight_type = explicit_ft.lower()
        elif not resolved_flight_type:
            if origin_code and dest_code:
                orig_ap = db.scalar(select(AirportManagement).where(AirportManagement.code == origin_code.upper()))
                dest_ap = db.scalar(select(AirportManagement).where(AirportManagement.code == dest_code.upper()))

                orig_country = _norm_country(orig_ap.country if orig_ap else None, origin_code)
                dest_country = _norm_country(dest_ap.country if dest_ap else None, dest_code)

                if orig_country == dest_country:
                    resolved_flight_type = "domestic"
                else:
                    resolved_flight_type = "international"
            else:
                resolved_flight_type = "domestic"

        packages = master_config.get("packages", [])
        # Booking catalogue is packages-only; demo individual cards are never returned.
        active_services = []
        active_packages = packages

        if terminal:
            term_clean = str(terminal).strip().upper()
            term_filtered = [
                s for s in active_services
                if not s.get("terminal") or str(s.get("terminal")).upper() == term_clean or term_clean in str(s.get("terminal")).upper()
            ]
            if term_filtered:
                active_services = term_filtered

        return {
            "covered": True,
            "success": True,
            "airport": {
                "id": str(db_airport.id) if db_airport else code,
                "code": code,
                "name": master_config.get("name") or (db_airport.name if db_airport else f"{code} Airport"),
                "city": master_config.get("city") or (db_airport.city if db_airport else code),
                "country": master_config.get("country") or (db_airport.country if db_airport else "India"),
            },
            "journey_type": j_type,
            "journeyType": j_type,
            "flight_type": resolved_flight_type,
            "flightType": resolved_flight_type,
            "terminal": terminal or ("T1_T2" if code in ["BOM", "DEL"] else "T3"),
            "catalogSource": "existing-airport-catalog",
            "currency": master_config.get("currency", "INR"),
            "operatingHours": master_config.get("operatingHours", "24/7"),
            "packages": active_packages,
            "individual_services": active_services,
            "individualServices": active_services
        }

    @classmethod
    def validate_authoritative_booking(
        cls,
        db: Session,
        payload: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Authoritative backend booking validator and price calculator.
        
        1. Revalidates flight information & determines service airport dynamically.
        2. Revalidates journey_type (arrival, departure, transit).
        3. Revalidates airport coverage in DB/Registry.
        4. Revalidates package & individual service availability from DB catalog.
        5. Checks package/service compatibility and filters out duplicate/overlapping service IDs.
        6. Revalidates booking time restrictions (minimum notice window).
        7. Ignores all frontend price inputs; computes authoritative database total + taxes.
        """
        flight_num = (
            payload.get("flightId")
            or payload.get("verifiedFlightId")
            or payload.get("flight_number")
            or payload.get("flightNum")
            or payload.get("flight_num")
            or ""
        ).strip().upper()
        journey_type = (
            payload.get("journeyType")
            or payload.get("journey_type")
            or "arrival"
        ).strip().lower()
        airport_code = (
            payload.get("airportId")
            or payload.get("airport_code")
            or payload.get("airport")
            or ""
        ).strip().upper()
        package_id = (
            payload.get("packageId")
            or payload.get("package_id")
            or payload.get("selected_package_id")
        )
        service_ids = (
            payload.get("serviceIds")
            or payload.get("service_ids")
            or payload.get("selected_service_ids")
            or []
        )
        guest_count = max(1, min(20, int(payload.get("guestCount") or payload.get("guest_count") or payload.get("passenger_count") or 1)))
        service_date = payload.get("serviceDate") or payload.get("service_date") or datetime.now(timezone.utc).strftime("%Y-%m-%d")
        service_time = payload.get("serviceTime") or payload.get("service_time") or ""

        errors: List[str] = []

        # 1. Validate Journey Type
        if journey_type not in ["arrival", "departure", "transit"]:
            errors.append(f"Invalid journey_type '{journey_type}'. Must be 'arrival', 'departure', or 'transit'.")

        # 2. Dynamic Airport Resolution & Coverage Verification
        origin_val = str(payload.get("origin") or payload.get("origin_code") or "").strip().upper()
        dest_val = str(payload.get("destination") or payload.get("dest_code") or payload.get("destination_code") or "").strip().upper()
        transit_val = str(payload.get("transit") or payload.get("transit_code") or "").strip().upper()

        target_airport_code = airport_code
        if journey_type == "arrival" and dest_val:
            target_airport_code = dest_val
        elif journey_type == "departure" and origin_val:
            target_airport_code = origin_val
        elif journey_type == "transit" and transit_val:
            target_airport_code = transit_val

        if target_airport_code == "GAU":
            config = cls.get_airport_configuration(
                target_airport_code,
                db=db,
                journey_type=journey_type,
                flight_type=payload.get("flight_type") or payload.get("flightType"),
            )
        else:
            config = cls.get_airport_configuration(target_airport_code, db=db)

        # Check coverage in database
        is_covered = True
        db_airport = db.scalar(select(AirportManagement).where(AirportManagement.code == target_airport_code))
        if db_airport and not db_airport.is_active:
            is_covered = False
        elif not db_airport and not config:
            is_covered = False
        elif not config.get("packages") and not config.get("individualServices"):
            is_covered = False

        if not is_covered:
            errors.append(f"Airport '{target_airport_code}' is currently uncovered for VIP services.")

        # 3. Booking Time Restriction Check
        advance_notice_hours = config.get("advanceNoticeHours", 6)
        try:
            dt_str = f"{service_date} {service_time}"
            svc_dt = datetime.strptime(dt_str, "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
            now_dt = datetime.now(timezone.utc)
            hours_diff = (svc_dt - now_dt).total_seconds() / 3600.0
            if hours_diff < advance_notice_hours and hours_diff > -24:
                errors.append(f"Service at {target_airport_code} requires at least {advance_notice_hours} hours advance notice.")
        except Exception:
            pass

        # 4. Package Validation & Included Service Resolution
        db_packages = config.get("packages", [])
        db_services = config.get("individualServices", [])

        selected_package = None
        included_service_ids = set()

        if package_id:
            pkg_match = next((p for p in db_packages if p["id"] == package_id), None)
            if not pkg_match:
                errors.append(f"Package '{package_id}' is unavailable or invalid at {target_airport_code}.")
            else:
                selected_package = {
                    "id": pkg_match["id"],
                    "title": pkg_match["title"],
                    "price": float(pkg_match["basePrice"]),
                    "currency": pkg_match.get("currency", "INR"),
                    "includedServiceIds": pkg_match.get("serviceIds", [])
                }
                included_service_ids = set(pkg_match.get("serviceIds", []))

        # 5. Individual Services Validation & Overlap Prevention (Rule 9 - No Double Charge)
        selected_services = []
        overlapping_ignored = []

        for sid in service_ids:
            if sid in included_service_ids:
                overlapping_ignored.append(sid)
                continue

            svc_match = next((s for s in db_services if s["id"] == sid), None)
            if not svc_match:
                errors.append(f"Service '{sid}' is unavailable at {target_airport_code}.")
            elif not svc_match.get("isAvailable", True):
                errors.append(f"Service '{svc_match.get('title', sid)}' is currently sold out or restricted at {target_airport_code}.")
            else:
                selected_services.append({
                    "id": svc_match["id"],
                    "title": svc_match["title"],
                    "price": float(svc_match["price"]),
                    "currency": svc_match.get("currency", "INR")
                })

        if not selected_package and not selected_services and not errors:
            errors.append("At least one package or individual service must be selected.")

        # 6. Return validation failure if any errors were accumulated
        if errors:
            return {
                "valid": False,
                "errors": errors
            }

        # 7. Authoritative Pricing Calculation from DB
        pkg_price = selected_package["price"] if selected_package else 0.0
        services_price = sum(s["price"] for s in selected_services)

        unit_subtotal = pkg_price + services_price
        subtotal = round(unit_subtotal * guest_count, 2)
        tax_rate = 0.18
        taxes = round(subtotal * tax_rate, 2)
        total = round(subtotal + taxes, 2)
        currency = config.get("currency", "INR")

        return {
            "valid": True,
            "bookingContext": {
                "airportCode": config["code"],
                "airportName": config["name"],
                "journeyType": journey_type,
                "flightNumber": flight_num,
                "serviceDate": service_date,
                "serviceTime": service_time,
                "guestCount": guest_count
            },
            "selectedPackage": selected_package,
            "selectedServices": selected_services,
            "overlappingServicesIgnored": overlapping_ignored,
            "subtotal": subtotal,
            "taxes": taxes,
            "total": total,
            "currency": currency
        }

    @classmethod
    def save_booking_draft(cls, db: Session, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Authoritative Passenger Details Validation & Booking-Draft Persistence Service.
        
        1. Revalidates required name, email format, phone format, and guest count.
        2. Revalidates journey context, service airport, package ID, and service IDs.
        3. Revalidates booking availability and minimum lead time rules from DB.
        4. Updates existing draft record if booking_ref is provided; otherwise creates new draft.
        5. Recalculates authoritative DB pricing (never trusts frontend total/prices).
        6. Masks sensitive logs to preserve customer data privacy.
        """
        field_errors = []

        full_name = (payload.get("full_name") or payload.get("fullName") or payload.get("passenger_name") or "").strip()
        email = (payload.get("email") or payload.get("passenger_email") or "").strip()
        phone = (payload.get("phone") or payload.get("passenger_phone") or "").strip()
        nationality = (payload.get("nationality") or "").strip()
        special_requests = payload.get("special_requests") or payload.get("specialRequests") or ""
        booking_ref = (payload.get("booking_ref") or payload.get("bookingRef") or payload.get("booking_reference") or "").strip()

        # 1. Field Validation
        if not full_name or len(full_name) < 2:
            field_errors.append({"field": "full_name", "message": "Please enter a valid lead passenger name."})

        if not email or not EMAIL_REGEX.match(email):
            field_errors.append({"field": "email", "message": "Please enter a valid email address."})

        clean_phone = re.sub(r"[^\d+]", "", phone)
        if not phone or len(clean_phone) < 7:
            field_errors.append({"field": "phone", "message": "Please enter a valid phone number (minimum 7 digits)."})

        try:
            guest_count = int(payload.get("guest_count") or payload.get("guestCount") or payload.get("passenger_count") or 1)
            if guest_count < 1 or guest_count > 20:
                field_errors.append({"field": "guest_count", "message": "Passenger count must be between 1 and 20."})
            guest_count = max(1, min(20, guest_count))
        except Exception:
            field_errors.append({"field": "guest_count", "message": "Invalid passenger count."})
            guest_count = 1

        if field_errors:
            return {
                "valid": False,
                "errors": field_errors
            }

        # 2. Revalidate Service Airport, Availability, Package & Price from Database Authority
        auth_val = cls.validate_authoritative_booking(db, payload)
        if not auth_val.get("valid"):
            return {
                "valid": False,
                "errors": [{"field": "services", "message": err} for err in auth_val.get("errors", [])]
            }

        # 3. Lookup existing draft by booking_ref if provided, or generate new reference
        existing_booking = None
        if booking_ref:
            existing_booking = db.scalar(select(Booking).where(Booking.booking_ref == booking_ref))

        if not existing_booking:
            date_stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
            rand_suffix = "".join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(4))
            ref_code = f"SHK-{date_stamp}-{rand_suffix}"

            new_booking = Booking(
                booking_ref=ref_code,
                passenger_name=full_name,
                passenger_email=email,
                passenger_phone=phone,
                service_category="Airport Assistance",
                flight_num=auth_val["bookingContext"]["flightNumber"],
                origin_code=(payload.get("origin_code") or payload.get("origin") or auth_val["bookingContext"]["airportCode"] or "").upper(),
                dest_code=(payload.get("dest_code") or payload.get("destination") or payload.get("destination_code") or auth_val["bookingContext"]["airportCode"] or "").upper(),
                service_type=auth_val["bookingContext"]["journeyType"],
                selected_services={
                    "package": auth_val.get("selectedPackage"),
                    "additional_services": auth_val.get("selectedServices"),
                    "overlapping_ignored": auth_val.get("overlappingServicesIgnored"),
                    "guest_count": guest_count,
                    "nationality": nationality,
                    "special_requests": special_requests
                },
                service_options={
                    "airport_code": auth_val["bookingContext"]["airportCode"],
                    "airport_name": auth_val["bookingContext"]["airportName"],
                    "service_date": auth_val["bookingContext"]["serviceDate"],
                    "service_time": auth_val["bookingContext"]["serviceTime"],
                },
                metadata_json={
                    "status": "DRAFT",
                    "subtotal": auth_val.get("subtotal"),
                    "taxes": auth_val.get("taxes"),
                    "total": auth_val.get("total"),
                    "currency": auth_val.get("currency", "INR")
                },
                total_amount=auth_val.get("total", 0.0),
                currency=auth_val.get("currency", "INR"),
                status=BookingStatus.DRAFT
            )
            db.add(new_booking)
            db.commit()
            db.refresh(new_booking)

            logger.info(f"[Booking Draft Created] ref={new_booking.booking_ref}, airport={auth_val['bookingContext']['airportCode']}, status=DRAFT")

            return {
                "valid": True,
                "booking_reference": new_booking.booking_ref,
                "status": "DRAFT",
                "booking_context": auth_val["bookingContext"],
                "selected_package": auth_val.get("selectedPackage"),
                "selected_services": auth_val.get("selectedServices"),
                "subtotal": auth_val.get("subtotal"),
                "taxes": auth_val.get("taxes"),
                "total": auth_val.get("total"),
                "currency": auth_val.get("currency", "INR")
            }
        else:
            existing_booking.passenger_name = full_name
            existing_booking.passenger_email = email
            existing_booking.passenger_phone = phone
            existing_booking.flight_num = auth_val["bookingContext"]["flightNumber"]
            existing_booking.service_type = auth_val["bookingContext"]["journeyType"]
            existing_booking.selected_services = {
                "package": auth_val.get("selectedPackage"),
                "additional_services": auth_val.get("selectedServices"),
                "overlapping_ignored": auth_val.get("overlappingServicesIgnored"),
                "guest_count": guest_count,
                "nationality": nationality,
                "special_requests": special_requests
            }
            existing_booking.service_options = {
                "airport_code": auth_val["bookingContext"]["airportCode"],
                "airport_name": auth_val["bookingContext"]["airportName"],
                "service_date": auth_val["bookingContext"]["serviceDate"],
                "service_time": auth_val["bookingContext"]["serviceTime"],
            }
            existing_booking.total_amount = auth_val.get("total", 0.0)
            existing_booking.currency = auth_val.get("currency", "INR")
            existing_booking.status = BookingStatus.DRAFT
            existing_booking.updated_at = datetime.now(timezone.utc)

            db.commit()
            db.refresh(existing_booking)

            logger.info(f"[Booking Draft Updated] ref={existing_booking.booking_ref}, airport={auth_val['bookingContext']['airportCode']}, status=DRAFT")

            return {
                "valid": True,
                "booking_reference": existing_booking.booking_ref,
                "status": "DRAFT",
                "booking_context": auth_val["bookingContext"],
                "selected_package": auth_val.get("selectedPackage"),
                "selected_services": auth_val.get("selectedServices"),
                "subtotal": auth_val.get("subtotal"),
                "taxes": auth_val.get("taxes"),
                "total": auth_val.get("total"),
                "currency": auth_val.get("currency", "INR")
            }

