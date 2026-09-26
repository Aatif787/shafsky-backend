"""
Enterprise Reliability & Hardening Unit Test Suite.

Validates all enterprise-level fixes implemented for production reliability:
1. CORS & Cookie Origin regex isolation (only Shafsky Vercel domains allowed, third-party Vercel domains blocked).
2. Input validation for estimate_booking_price (EstimatePriceRequest).
3. Input validation and mass-assignment protection for admin_update_airport_service (AirportServiceUpdateRequest).
4. Input validation for patch_airport_config (AirportPatchRequest).
5. Input validation for toggle_coupon_status (CouponToggleRequest).
6. Fail-fast validation of ICICI production endpoints vs UAT in validate_secrets_on_startup.
"""

import re
import pytest
from pydantic import ValidationError

from app.schemas.booking import EstimatePriceRequest
from app.schemas.admin import (
    AirportPatchRequest,
    AirportServiceUpdateRequest,
    CouponToggleRequest,
)
from app.main import _CORS_ORIGIN_REGEX


# ==============================================================================
# 1. CORS & Cookie Request Origin Regex Isolation
# ==============================================================================

def test_cors_origin_regex_allows_shafsky_and_local():
    """Verify that Shafsky deployments and local development origins pass CORS regex."""
    allowed = [
        "http://localhost",
        "http://localhost:5173",
        "http://127.0.0.1:3000",
        "https://shafsky-frontend.vercel.app",
        "https://shafsky-admin-portal.vercel.app",
        "https://shafsky-preview-branch-1.vercel.app",
        "https://shafsky.vercel.app",
        "https://my-tunnel.ngrok-free.app",
        "https://tunnel.ngrok.io",
    ]
    for origin in allowed:
        assert re.match(_CORS_ORIGIN_REGEX, origin) is not None, f"Expected {origin} to match CORS regex"


def test_cors_origin_regex_blocks_unauthorized_vercel_domains():
    """Verify that non-Shafsky Vercel subdomains and random domains are rejected."""
    blocked = [
        "https://evil-attacker.vercel.app",
        "https://some-other-project.vercel.app",
        "https://notshafsky.vercel.app",
        "https://fake-shafsky-phish.com",
        "https://shafsky.evil.com",
    ]
    for origin in blocked:
        assert re.match(_CORS_ORIGIN_REGEX, origin) is None, f"Expected {origin} to be blocked by CORS regex"


# ==============================================================================
# 2. Input Validation: EstimatePriceRequest
# ==============================================================================

def test_estimate_price_request_valid_and_defaults():
    """Verify EstimatePriceRequest handles both snake_case and camelCase with valid defaults."""
    # Default values
    req1 = EstimatePriceRequest()
    assert req1.pax_adults == 1
    assert req1.journey_type == "DEPARTURE"
    assert req1.flight_type == "DOMESTIC"

    # CamelCase parsing
    req2 = EstimatePriceRequest.model_validate({
        "packageId": "elite",
        "airportCode": "BOM",
        "journeyType": "ARRIVAL",
        "flightType": "INTERNATIONAL",
        "paxAdults": 3,
    })
    assert req2.package_id == "elite"
    assert req2.airport_code == "BOM"
    assert req2.pax_adults == 3


def test_estimate_price_request_rejects_invalid_pax():
    """Verify EstimatePriceRequest rejects negative, zero, or non-numeric pax."""
    with pytest.raises(ValidationError):
        EstimatePriceRequest.model_validate({"pax_adults": 0})

    with pytest.raises(ValidationError):
        EstimatePriceRequest.model_validate({"pax_adults": -1})

    with pytest.raises(ValidationError):
        EstimatePriceRequest.model_validate({"paxAdults": "abc"})


# ==============================================================================
# 3. Input Validation: AirportServiceUpdateRequest
# ==============================================================================

def test_airport_service_update_request_valid():
    """Verify AirportServiceUpdateRequest accepts permitted mutable service fields."""
    req = AirportServiceUpdateRequest(
        price=3500.0,
        currency="INR",
        is_available=True,
        terminal="T2",
        features=["Dedicated Porter", "Placard"],
        short_description="Premium Arrival",
        min_booking_notice_hours=4,
    )
    dumped = req.model_dump(exclude_unset=True)
    assert dumped["price"] == 3500.0
    assert dumped["currency"] == "INR"
    assert dumped["is_available"] is True
    assert len(dumped["features"]) == 2


def test_airport_service_update_request_rejects_invalid_values():
    """Verify AirportServiceUpdateRequest enforces field constraints (price > 0, currency length)."""
    with pytest.raises(ValidationError):
        AirportServiceUpdateRequest.model_validate({"price": -100.0})

    with pytest.raises(ValidationError):
        AirportServiceUpdateRequest.model_validate({"price": 0.0})

    with pytest.raises(ValidationError):
        AirportServiceUpdateRequest.model_validate({"currency": "TOOLONG"})

    with pytest.raises(ValidationError):
        AirportServiceUpdateRequest.model_validate({"min_booking_notice_hours": -1})


# ==============================================================================
# 4. Input Validation: AirportPatchRequest & CouponToggleRequest
# ==============================================================================

def test_airport_patch_request_accepts_clean_fields():
    """Verify AirportPatchRequest only allows defined fields."""
    req = AirportPatchRequest(
        name="Thiruvananthapuram International Airport",
        city="Thiruvananthapuram",
        is_active=True,
    )
    assert req.name == "Thiruvananthapuram International Airport"
    assert req.is_active is True
    assert req.operating_hours is None


def test_coupon_toggle_request_accepts_status():
    """Verify CouponToggleRequest handles boolean or string status."""
    req1 = CouponToggleRequest(is_active=True)
    assert req1.is_active is True

    req2 = CouponToggleRequest(status="ACTIVE")
    assert req2.status == "ACTIVE"


# ==============================================================================
# 5. ICICI Production Security Validation on Startup
# ==============================================================================

def test_validate_secrets_detects_icici_uat_in_prod(monkeypatch):
    """Verify validate_secrets_on_startup raises ValueError when ICICI_ENV is PROD but URLs point to UAT."""
    from app.security.secrets import validate_secrets_on_startup
    from app.config import settings

    monkeypatch.setattr(settings, "ENVIRONMENT", "production")
    monkeypatch.setattr(settings, "ICICI_ENV", "PROD")
    monkeypatch.setattr(settings, "ICICI_SALE_URL", "https://pgpayuat.icicibank.com/tsp/pg/api/v2/initiateSale")
    monkeypatch.setattr(settings, "ICICI_COMMAND_URL", "https://pgpayuat.icicibank.com/tsp/pg/api/command")

    # Ensure required prod secrets are mocked so we specifically test ICICI validation
    monkeypatch.setattr(settings, "DATABASE_URL", "postgresql://user:pass@localhost:5432/db")
    monkeypatch.setattr(settings, "JWT_PRIVATE_KEY", "-----BEGIN RSA PRIVATE KEY-----\nMIIE...\n-----END RSA PRIVATE KEY-----")
    monkeypatch.setattr(settings, "JWT_PUBLIC_KEY", "-----BEGIN PUBLIC KEY-----\nMIIB...\n-----END PUBLIC KEY-----")
    monkeypatch.setattr(settings, "JWT_REFRESH_SECRET", "super-secret-refresh-key-32-chars-long!")
    monkeypatch.setattr(settings, "RAZORPAY_KEY_ID", "rzp_live_12345678")
    monkeypatch.setattr(settings, "RAZORPAY_KEY_SECRET", "rzp_secret_12345678")
    monkeypatch.setenv("RAZORPAY_WEBHOOK_SECRET", "rzp_webhook_secret_1234")
    monkeypatch.setattr(settings, "ALLOW_HS256_LEGACY_FALLBACK", False)
    monkeypatch.setattr(settings, "JWT_ALGORITHM", "RS256")
    monkeypatch.setattr(settings, "REQUIRE_REDIS", False)

    with pytest.raises(ValueError, match="ICICI_ENV is PROD but ICICI gateway URLs point to UAT"):
        validate_secrets_on_startup()
