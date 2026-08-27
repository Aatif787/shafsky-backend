"""
Service-selection catalog tests: route classification is applied BEFORE packages load.

Client flight_type must not determine the catalog when origin and destination are known.
Does not modify airport_service rows or prices.
"""
import uuid
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.database import SessionLocal
from app.integrations.whatsapp.service import WhatsAppBookingStateMachine
from app.main import app
from app.models.journey_models import AirportService, Service, SupportedAirport
from app.services.service_config_service import ServiceConfigService

client = TestClient(app)

PACKAGE_SLUGS = {"silver", "gold", "elite", "elite_plus", "platinum", "bronze", "diamond"}


def _pkg_map(packages):
    return {
        str(p.get("id") or "").lower(): float(p.get("basePrice") or p.get("price") or 0)
        for p in (packages or [])
        if str(p.get("id") or "").lower() in PACKAGE_SLUGS
    }


def _db_package_price_sets(db, iata: str, journey_type: str, flight_type: str):
    airport = db.scalar(select(SupportedAirport).where(SupportedAirport.iata_code == iata))
    assert airport is not None, f"{iata} must exist in supported_airports"
    rows = list(
        db.execute(
            select(AirportService, Service)
            .join(Service, Service.id == AirportService.service_id)
            .where(
                AirportService.airport_id == airport.id,
                AirportService.journey_type == journey_type,
                AirportService.flight_type.in_([flight_type, "ALL"]),
                AirportService.is_available.is_(True),
            )
        ).all()
    )
    prices = {}
    for aps, svc in rows:
        slug = (svc.slug or "").lower()
        if slug not in PACKAGE_SLUGS:
            continue
        prices.setdefault(slug, set()).add(float(aps.price))
    return prices


class TestAirportServicesCatalogAuthority:
    def test_1_dxb_del_client_domestic_returns_international_catalog(self):
        db = SessionLocal()
        try:
            res = ServiceConfigService.resolve_catalog_services(
                db=db,
                airport_code="DEL",
                journey_type="arrival",
                flight_type="domestic",
                origin_code="DXB",
                dest_code="DEL",
            )
            assert res.get("success") is True
            assert res["flightType"] == "international"
            assert res["flight_type"] == "international"
            pkgs = _pkg_map(res.get("packages"))
            assert pkgs, "International catalog must return packages"
            expected = _db_package_price_sets(db, "DEL", "ARRIVAL", "INTERNATIONAL")
            for slug, price in pkgs.items():
                assert slug in expected
                assert price in expected[slug]
            domestic = _db_package_price_sets(db, "DEL", "ARRIVAL", "DOMESTIC")
            if domestic and expected and domestic != expected:
                assert pkgs != {k: next(iter(v)) for k, v in domestic.items()}
        finally:
            db.close()

        http = client.get(
            "/api/airport/services",
            params={
                "airport": "DEL",
                "journey_type": "arrival",
                "origin": "DXB",
                "destination": "DEL",
                "flight_type": "domestic",
            },
        )
        assert http.status_code == 200
        body = http.json()
        assert body["flightType"] == "international"
        assert _pkg_map(body.get("packages"))

        journey = client.get(
            "/api/journey/airports/DEL/services",
            params={
                "journey_type": "ARRIVAL",
                "flight_type": "DOMESTIC",
                "origin": "DXB",
                "destination": "DEL",
            },
        )
        assert journey.status_code == 200
        jbody = journey.json()
        assert jbody["flight_type"] == "INTERNATIONAL"
        assert jbody["total"] > 0
        for item in jbody["data"]:
            assert item["flight_type"] in ("INTERNATIONAL", "ALL")

    def test_2_del_dxb_client_domestic_returns_international_catalog(self):
        db = SessionLocal()
        try:
            res = ServiceConfigService.resolve_catalog_services(
                db=db,
                airport_code="DEL",
                journey_type="departure",
                flight_type="domestic",
                origin_code="DEL",
                dest_code="DXB",
            )
            assert res["flightType"] == "international"
            pkgs = _pkg_map(res.get("packages"))
            assert pkgs
            expected = _db_package_price_sets(db, "DEL", "DEPARTURE", "INTERNATIONAL")
            for slug, price in pkgs.items():
                assert slug in expected
                assert price in expected[slug]
        finally:
            db.close()

    def test_3_del_bom_client_international_returns_domestic_catalog(self):
        db = SessionLocal()
        try:
            res = ServiceConfigService.resolve_catalog_services(
                db=db,
                airport_code="DEL",
                journey_type="departure",
                flight_type="international",
                origin_code="DEL",
                dest_code="BOM",
            )
            assert res["flightType"] == "domestic"
            pkgs = _pkg_map(res.get("packages"))
            assert pkgs
            expected = _db_package_price_sets(db, "DEL", "DEPARTURE", "DOMESTIC")
            for slug, price in pkgs.items():
                assert slug in expected
                assert price in expected[slug]
        finally:
            db.close()

    def test_4_bom_del_client_international_returns_domestic_catalog(self):
        db = SessionLocal()
        try:
            res = ServiceConfigService.resolve_catalog_services(
                db=db,
                airport_code="DEL",
                journey_type="arrival",
                flight_type="international",
                origin_code="BOM",
                dest_code="DEL",
            )
            assert res["flightType"] == "domestic"
            pkgs = _pkg_map(res.get("packages"))
            assert pkgs
            expected = _db_package_price_sets(db, "DEL", "ARRIVAL", "DOMESTIC")
            for slug, price in pkgs.items():
                assert slug in expected
                assert price in expected[slug]
        finally:
            db.close()

    def test_5_lhr_jfk_is_international(self):
        from app.services.service_airport_rules import derive_flight_type_from_route

        db = SessionLocal()
        try:
            derived = derive_flight_type_from_route(db, "LHR", "JFK", "ARRIVAL")
            assert derived == "INTERNATIONAL"
            res = ServiceConfigService.resolve_catalog_services(
                db=db,
                airport_code="DEL",
                journey_type="arrival",
                flight_type="domestic",
                origin_code="LHR",
                dest_code="JFK",
            )
            assert res.get("flightType") == "international"
            assert res.get("airport", {}).get("code") != "DEL" or not res.get("packages")
        finally:
            db.close()

    def test_6_unknown_airport_validation_error(self):
        db = SessionLocal()
        try:
            res = ServiceConfigService.resolve_catalog_services(
                db=db,
                airport_code="DEL",
                journey_type="arrival",
                flight_type="domestic",
                origin_code="ZZZ",
                dest_code="DEL",
            )
            assert res.get("success") is False
            assert res.get("packages") == []
            assert res.get("flightType") is None
            assert res.get("error")
            assert "domestic" not in (res.get("flight_type") or "")
        finally:
            db.close()

        http = client.get(
            "/api/airport/services",
            params={
                "airport": "DEL",
                "journey_type": "arrival",
                "origin": "ZZZ",
                "destination": "DEL",
                "flight_type": "domestic",
            },
        )
        assert http.status_code == 200
        body = http.json()
        assert body.get("success") is False
        assert body.get("packages") == []
        assert body.get("error")

        journey = client.get(
            "/api/journey/airports/DEL/services",
            params={
                "journey_type": "ARRIVAL",
                "flight_type": "DOMESTIC",
                "origin": "ZZZ",
                "destination": "DEL",
            },
        )
        assert journey.status_code == 400

    def test_7_transit_service_selection_unchanged(self):
        db = SessionLocal()
        try:
            res = ServiceConfigService.resolve_catalog_services(
                db=db,
                airport_code="DEL",
                journey_type="transit",
                flight_type="DOMESTIC_DOMESTIC",
                origin_code="BOM",
                dest_code="HYD",
            )
            assert res.get("flightType") == "domestic_domestic"
            pkgs = _pkg_map(res.get("packages"))
            if pkgs:
                expected = _db_package_price_sets(db, "DEL", "TRANSIT", "DOMESTIC_DOMESTIC")
                for slug, price in pkgs.items():
                    if slug in expected:
                        assert price in expected[slug]
        finally:
            db.close()

        journey = client.get(
            "/api/journey/airports/DEL/services",
            params={
                "journey_type": "TRANSIT",
                "flight_type": "DOMESTIC_DOMESTIC",
                "origin": "BOM",
                "destination": "HYD",
            },
        )
        assert journey.status_code == 200
        body = journey.json()
        assert body["flight_type"] == "DOMESTIC_DOMESTIC"
        for item in body["data"]:
            assert item["flight_type"] in ("DOMESTIC_DOMESTIC", "ALL", "DOMESTIC")


class TestWhatsAppCatalogAuthority:
    @patch("app.integrations.whatsapp.client.WhatsAppClient.send_interactive_list")
    def test_dxb_del_client_domestic_shows_international_packages(self, mock_list):
        mock_list.return_value = {"success": True, "message_id": "wamid.dxb_del"}
        db = SessionLocal()
        try:
            phone = f"91{uuid.uuid4().int % 10**10:010d}"
            conv, _ = WhatsAppBookingStateMachine.get_or_create_conversation(db, phone)
            conv.selected_category = "Airport Services"
            conv.selected_airport_iata = "DEL"
            conv.selected_airport_name = "Indira Gandhi International Airport"
            conv.flight_details_json = {
                "journey_type": "ARRIVAL",
                "travel_type": "DOMESTIC",
                "flight_type": "DOMESTIC",
                "origin_iata": "DXB",
                "destination_iata": "DEL",
            }
            conv.current_state = "SERVICE_SELECTION"
            db.commit()

            res = WhatsAppBookingStateMachine._send_airport_services_menu(db, conv)
            assert res["status"] == "services_menu_sent"
            db.refresh(conv)
            meta = conv.flight_details_json or {}
            assert meta.get("travel_type") == "INTERNATIONAL"
            assert mock_list.called

            control_phone = f"91{uuid.uuid4().int % 10**10:010d}"
            control, _ = WhatsAppBookingStateMachine.get_or_create_conversation(db, control_phone)
            control.selected_category = "Airport Services"
            control.selected_airport_iata = "DEL"
            control.selected_airport_name = "Indira Gandhi International Airport"
            control.flight_details_json = {
                "journey_type": "ARRIVAL",
                "travel_type": "INTERNATIONAL",
                "flight_type": "INTERNATIONAL",
            }
            control.current_state = "SERVICE_SELECTION"
            db.commit()
            mock_list.reset_mock()
            control_res = WhatsAppBookingStateMachine._send_airport_services_menu(db, control)
            assert control_res["status"] == "services_menu_sent"
        finally:
            db.close()

    def test_helper_del_bom_overrides_international(self):
        db = SessionLocal()
        try:
            phone = f"91{uuid.uuid4().int % 10**10:010d}"
            conv, _ = WhatsAppBookingStateMachine.get_or_create_conversation(db, phone)
            conv.flight_details_json = {
                "journey_type": "DEPARTURE",
                "travel_type": "INTERNATIONAL",
                "origin_iata": "DEL",
                "destination_iata": "BOM",
            }
            tt, err = WhatsAppBookingStateMachine._authoritative_catalog_travel_type(
                db, conv, "DEPARTURE", "INTERNATIONAL"
            )
            assert err is None
            assert tt == "DOMESTIC"
        finally:
            db.close()

    def test_helper_transit_unchanged(self):
        db = SessionLocal()
        try:
            phone = f"91{uuid.uuid4().int % 10**10:010d}"
            conv, _ = WhatsAppBookingStateMachine.get_or_create_conversation(db, phone)
            conv.flight_details_json = {
                "journey_type": "TRANSIT",
                "travel_type": "DOMESTIC_INTERNATIONAL",
                "origin_iata": "DXB",
                "destination_iata": "LHR",
            }
            tt, err = WhatsAppBookingStateMachine._authoritative_catalog_travel_type(
                db, conv, "TRANSIT", "DOMESTIC_INTERNATIONAL"
            )
            assert err is None
            assert tt == "DOMESTIC_INTERNATIONAL"
        finally:
            db.close()
