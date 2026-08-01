"""
FastAPI Router for Configuration, Feature Flags, Airport Config, and Coupons.
Provides /api/config/feature-flags, /api/airports/{code}, /api/coupons endpoints.
"""

from typing import Optional, Dict, Any, List
import uuid
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session
from sqlalchemy import select, update, delete

from app.database import get_db
from app.models.schema import FeatureFlag, AirportManagement, Coupon
from app.security.dependencies import get_required_admin, get_required_user
from app.schemas.admin import AdminApiResponse

router = APIRouter(tags=["System Configuration & Feature Flags"])

# ─── FEATURE FLAGS ─────────────────────────────────────────────────────────────

@router.get("/api/config/feature-flags", response_model=AdminApiResponse)
@router.get("/api/feature-flags", response_model=AdminApiResponse)
async def get_config_feature_flags(db: Session = Depends(get_db)):
    flags = list(db.scalars(select(FeatureFlag)).all())
    data = {f.id: f.is_enabled for f in flags} if flags else {
        "MOCK_DATA": False,
        "SMS_NOTIFICATIONS": True,
        "WHATSAPP_NOTIFICATIONS": True,
        "AUTO_CONFIRM": False,
        "SIX_HOUR_RULE": True,
    }
    return AdminApiResponse(success=True, data=data)


@router.patch("/api/config/feature-flags", response_model=AdminApiResponse)
@router.patch("/api/feature-flags", response_model=AdminApiResponse)
async def patch_config_feature_flags(
    payload: Dict[str, Any],
    db: Session = Depends(get_db),
    _admin = Depends(get_required_admin)
):
    updated = []
    for flag_key, val in payload.items():
        flag = db.scalar(select(FeatureFlag).where(FeatureFlag.id == flag_key))
        enabled = bool(val.get("is_enabled") if isinstance(val, dict) else val)
        if not flag:
            flag = FeatureFlag(
                id=flag_key,
                name=flag_key.replace("_", " ").title(),
                description=f"Feature flag {flag_key}",
                is_enabled=enabled,
                rules={},
            )
            db.add(flag)
        else:
            flag.is_enabled = enabled
            flag.updated_at = datetime.now(timezone.utc)
        updated.append(flag_key)

    db.commit()
    return AdminApiResponse(success=True, data={"updated": updated, "message": "Feature flags updated."})


# ─── AIRPORTS ───────────────────────────────────────────────────────────────

@router.get("/api/airports", response_model=AdminApiResponse)
async def list_public_airports(db: Session = Depends(get_db)):
    airports = list(db.scalars(select(AirportManagement).where(AirportManagement.is_active.is_(True)).order_by(AirportManagement.code)).all())
    data = [
        {
            "id": str(a.id),
            "code": a.code,
            "name": a.name,
            "city": a.city,
            "country": a.country,
            "operatingHours": a.operating_hours,
            "isActive": a.is_active,
            "servicesConfig": a.services_config,
        }
        for a in airports
    ]
    return AdminApiResponse(success=True, data=data)


@router.patch("/api/airports/{airport_code}", response_model=AdminApiResponse)
async def patch_airport_by_code(
    airport_code: str,
    payload: Dict[str, Any],
    db: Session = Depends(get_db),
    _admin = Depends(get_required_admin)
):
    code = airport_code.upper().strip()
    airport = db.scalar(select(AirportManagement).where(AirportManagement.code == code))
    if not airport:
        raise HTTPException(status_code=404, detail=f"Airport '{code}' not found.")

    if "name" in payload:
        airport.name = payload["name"]
    if "city" in payload:
        airport.city = payload["city"]
    if "country" in payload:
        airport.country = payload["country"]
    if "is_active" in payload:
        airport.is_active = bool(payload["is_active"])
    if "operating_hours" in payload:
        airport.operating_hours = payload["operating_hours"]
    if "services_config" in payload:
        airport.services_config = payload["services_config"]

    airport.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(airport)

    return AdminApiResponse(
        success=True,
        data={
            "id": str(airport.id),
            "code": airport.code,
            "name": airport.name,
            "city": airport.city,
            "country": airport.country,
            "operatingHours": airport.operating_hours,
            "isActive": airport.is_active,
        }
    )


@router.delete("/api/airports/{airport_code}", response_model=AdminApiResponse)
async def delete_airport_by_code(
    airport_code: str,
    db: Session = Depends(get_db),
    _admin = Depends(get_required_admin)
):
    code = airport_code.upper().strip()
    airport = db.scalar(select(AirportManagement).where(AirportManagement.code == code))
    if not airport:
        raise HTTPException(status_code=404, detail=f"Airport '{code}' not found.")

    airport.is_active = False
    airport.updated_at = datetime.now(timezone.utc)
    db.commit()

    return AdminApiResponse(success=True, data={"code": code, "message": f"Airport '{code}' deactivated."})


# ─── COUPONS ────────────────────────────────────────────────────────────────

@router.get("/api/coupons", response_model=AdminApiResponse)
async def list_public_coupons(
    db: Session = Depends(get_db),
    _user = Depends(get_required_user)
):
    coupons = list(db.scalars(select(Coupon).where(Coupon.is_active.is_(True)).order_by(Coupon.created_at.desc())).all())
    data = [
        {
            "id": str(c.id),
            "code": c.code,
            "discountPercent": c.discount_percent,
            "discountAmount": c.discount_amount,
            "maxUses": c.max_uses,
            "usedCount": c.used_count,
            "isActive": c.is_active,
            "expiresAt": c.expires_at.isoformat() if c.expires_at else None,
        }
        for c in coupons
    ]
    return AdminApiResponse(success=True, data=data)


@router.patch("/api/coupons/{coupon_id}/status", response_model=AdminApiResponse)
async def patch_coupon_status(
    coupon_id: str,
    payload: Optional[Dict[str, Any]] = None,
    db: Session = Depends(get_db),
    _admin = Depends(get_required_admin)
):
    try:
        c_uuid = uuid.UUID(coupon_id)
        cp = db.scalar(select(Coupon).where(Coupon.id == c_uuid))
    except Exception:
        cp = db.scalar(select(Coupon).where(Coupon.code == coupon_id.upper()))

    if not cp:
        raise HTTPException(status_code=404, detail=f"Coupon '{coupon_id}' not found.")

    if payload and "is_active" in payload:
        cp.is_active = bool(payload["is_active"])
    elif payload and "status" in payload:
        cp.is_active = payload["status"].upper() == "ACTIVE"
    else:
        cp.is_active = not cp.is_active

    db.commit()
    db.refresh(cp)

    return AdminApiResponse(
        success=True,
        data={"id": str(cp.id), "code": cp.code, "isActive": cp.is_active}
    )
