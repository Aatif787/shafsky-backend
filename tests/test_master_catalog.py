"""
Unit Tests for Unified Master Airport Catalog Resolution Engine.
"""

import pytest
from sqlalchemy import select
from app.database import SessionLocal
from app.models.schema import AirportManagement
from app.services.service_config_service import ServiceConfigService


def test_bom_to_del_arrival_catalog():
    """Test 1: BOM -> DEL Arrival resolves to Delhi master arrival catalog without synthetic packages."""
    db = SessionLocal()
    try:
        # Ensure clean state for DEL
        del_ap = db.scalar(select(AirportManagement).where(AirportManagement.code == "DEL"))
        if del_ap:
            del_ap.services_config = None
            db.commit()

        res = ServiceConfigService.resolve_catalog_services(
            db=db,
            airport_code="DEL",
            journey_type="arrival",
            origin_code="BOM",
            dest_code="DEL"
        )
        assert res["covered"] is True
        assert res["airport"]["code"] == "DEL"
        assert res["journeyType"] == "arrival"
        assert res["flightType"] == "domestic"
        
        # Verify real master catalog packages are present
        pkg_titles = [p["title"] for p in res["packages"]]
        assert "Silver Escort" in pkg_titles or "Gold VIP Sanctuary" in pkg_titles or "Elite Presidential" in pkg_titles
    finally:
        db.close()


def test_bom_to_del_departure_catalog():
    """Test 2: BOM -> DEL Departure resolves to BOM master departure catalog."""
    db = SessionLocal()
    try:
        res = ServiceConfigService.resolve_catalog_services(
            db=db,
            airport_code="BOM",
            journey_type="departure",
            origin_code="BOM",
            dest_code="DEL"
        )
        assert res["covered"] is True
        assert res["airport"]["code"] == "BOM"
        assert res["journeyType"] == "departure"
        assert res["flightType"] == "domestic"
    finally:
        db.close()


def test_domestic_vs_international_delhi_arrival():
    """Test 3 & 4: Domestic vs International Delhi Arrival dynamic flightType resolution."""
    db = SessionLocal()
    try:
        # Domestic BOM -> DEL
        dom_res = ServiceConfigService.resolve_catalog_services(
            db=db,
            airport_code="DEL",
            journey_type="arrival",
            origin_code="BOM",
            dest_code="DEL"
        )
        assert dom_res["flightType"] == "domestic"

        # International DXB -> DEL
        intl_res = ServiceConfigService.resolve_catalog_services(
            db=db,
            airport_code="DEL",
            journey_type="arrival",
            origin_code="DXB",
            dest_code="DEL"
        )
        assert intl_res["flightType"] == "international"
    finally:
        db.close()


def test_terminal_filtering():
    """Test 5: Terminal-specific service filtering."""
    db = SessionLocal()
    try:
        res = ServiceConfigService.resolve_catalog_services(
            db=db,
            airport_code="DEL",
            journey_type="arrival",
            terminal="T3"
        )
        assert res["covered"] is True
        assert res["terminal"] == "T3"
    finally:
        db.close()


def test_uncovered_airport_handling():
    """Test 6: Uncovered airport returns covered=False with no services from another airport."""
    db = SessionLocal()
    try:
        res = ServiceConfigService.resolve_catalog_services(
            db=db,
            airport_code="UNCOVERED_AIRPORT_XYZ",
            journey_type="arrival"
        )
        assert res["covered"] is False
        assert len(res["packages"]) == 0
        assert len(res["individualServices"]) == 0
    finally:
        db.close()


def test_db_master_catalog_override():
    """Test 7, 8 & 9: DB catalog price changes, package renaming, and disabled service handling."""
    db = SessionLocal()
    try:
        del_ap = db.scalar(select(AirportManagement).where(AirportManagement.code == "DEL"))
        if not del_ap:
            del_ap = AirportManagement(
                code="DEL",
                name="Delhi Indira Gandhi International Airport",
                city="New Delhi",
                country="India",
                is_active=True
            )
            db.add(del_ap)

        del_ap.services_config = {
            "code": "DEL",
            "name": "Delhi Custom Renamed Airport",
            "city": "New Delhi",
            "country": "India",
            "currency": "INR",
            "packages": [
                {
                    "id": "custom_pkg",
                    "title": "Custom Master Gold Package",
                    "basePrice": 9999.0,
                    "currency": "INR",
                    "serviceIds": ["meet_greet"]
                }
            ],
            "individualServices": [
                {"id": "meet_greet", "title": "Meet & Greet Escort", "price": 2999.0, "isAvailable": True},
                {"id": "disabled_svc", "title": "Disabled Service", "price": 500.0, "isAvailable": False}
            ]
        }
        db.commit()

        res = ServiceConfigService.resolve_catalog_services(db=db, airport_code="DEL", journey_type="arrival")
        assert res["covered"] is True
        
        # Test 8: Package renamed in master DB catalog
        pkg_titles = [p["title"] for p in res["packages"]]
        assert "Custom Master Gold Package" in pkg_titles

        # Test 7: Price changed in master DB catalog
        custom_pkg = next(p for p in res["packages"] if p["id"] == "custom_pkg")
        assert custom_pkg["basePrice"] == 9999.0

        # Test 9: Disabled service (isAvailable=False) is excluded
        svc_ids = [s["id"] for s in res["individualServices"]]
        assert "disabled_svc" not in svc_ids
    finally:
        # Reset del_ap services_config
        del_ap = db.scalar(select(AirportManagement).where(AirportManagement.code == "DEL"))
        if del_ap:
            del_ap.services_config = None
            db.commit()
        db.close()
