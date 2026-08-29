"""
Regression: payment amount must equal the catalog package price × guests.

Root cause fixed: calculate_authoritative_price used Service.name.ilike('%gold%')
which could resolve Gold → Elite Gold (or any higher tier containing the substring)
and silently inflate the Razorpay charge after the review step showed the package price.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from app.services.booking_service import BookingService


def test_normalize_package_slug_aliases_and_prefixes():
    assert BookingService.normalize_package_slug("Gold") == "gold"
    assert BookingService.normalize_package_slug("gold-service") == "gold"
    assert BookingService.normalize_package_slug("elite_plus") == "elite-plus"
    assert BookingService.normalize_package_slug("package-silver") == "silver"
    assert BookingService.normalize_package_slug("  PLATINUM  ") == "platinum"


def test_authoritative_price_uses_exact_slug_not_ilike_upgrade():
    """Selecting 'gold' must never charge the Elite Gold row."""
    gold = SimpleNamespace(id="svc-gold", slug="gold", name="Gold")

    gold_row = SimpleNamespace(
        journey_type="DEPARTURE",
        flight_type="DOMESTIC",
        is_available=True,
        price=5000.0,
        service_id=gold.id,
    )
    # Even if a higher-priced row is present for another service, mappings are
    # scoped to the resolved service_id — Elite Gold must not be in this list.
    airport = SimpleNamespace(id="ap-1", iata_code="DEL")

    db = MagicMock()
    db.scalar.side_effect = [airport, gold]
    db.scalars.return_value.all.return_value = [gold_row]

    total = BookingService.calculate_authoritative_price(
        db=db,
        airport_code="DEL",
        service_tier_or_slug="gold",
        journey_type="DEPARTURE",
        flight_type="DOMESTIC",
        pax_count=1,
    )

    assert total == 5000.0
    # Ensure we queried by slug, never by loose name substring.
    assert db.scalar.call_count == 2


def test_authoritative_price_multiplies_guests_once():
    gold = SimpleNamespace(id="svc-gold", slug="gold", name="Gold")
    gold_row = SimpleNamespace(
        journey_type="ARRIVAL",
        flight_type="INTERNATIONAL",
        is_available=True,
        price=8000.0,
        service_id=gold.id,
    )
    airport = SimpleNamespace(id="ap-1", iata_code="BOM")
    db = MagicMock()
    db.scalar.side_effect = [airport, gold]
    db.scalars.return_value.all.return_value = [gold_row]

    total = BookingService.calculate_authoritative_price(
        db=db,
        airport_code="BOM",
        service_tier_or_slug="gold",
        journey_type="ARRIVAL",
        flight_type="INTERNATIONAL",
        pax_count=3,
    )
    assert total == 24000.0


def test_authoritative_price_rejects_unknown_slug_instead_of_first_package():
    """
    Previously a failed slug match fell through to the first AirportService row
    for the journey (often a premium tier), inflating the charge.
    """
    airport = SimpleNamespace(id="ap-1", iata_code="DEL")
    db = MagicMock()
    # airport found, then slug miss, then exact-name miss
    db.scalar.side_effect = [airport, None, None]

    with pytest.raises(HTTPException) as exc:
        BookingService.calculate_authoritative_price(
            db=db,
            airport_code="DEL",
            service_tier_or_slug="not-a-real-tier",
            journey_type="DEPARTURE",
            flight_type="DOMESTIC",
            pax_count=1,
        )
    assert exc.value.status_code == 400
    assert "not found" in str(exc.value.detail).lower()


def test_authoritative_price_falls_back_to_all_flight_type_row():
    gold = SimpleNamespace(id="svc-gold", slug="gold", name="Gold")
    all_row = SimpleNamespace(
        journey_type="DEPARTURE",
        flight_type="ALL",
        is_available=True,
        price=4500.0,
        service_id=gold.id,
    )
    airport = SimpleNamespace(id="ap-1", iata_code="HYD")
    db = MagicMock()
    db.scalar.side_effect = [airport, gold]
    db.scalars.return_value.all.return_value = [all_row]

    total = BookingService.calculate_authoritative_price(
        db=db,
        airport_code="HYD",
        service_tier_or_slug="gold",
        journey_type="DEPARTURE",
        flight_type="DOMESTIC",
        pax_count=2,
    )
    assert total == 9000.0
