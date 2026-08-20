"""
FastAPI Router for Configuration, Feature Flags, Airport Config, and Coupons.
Provides /api/config/feature-flags, /api/airports/{code}, /api/coupons endpoints.
"""

from typing import Optional, Dict, Any, List
import uuid
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, Query, Body, status
from sqlalchemy.orm import Session
from sqlalchemy import select, update, delete

from app.database import get_db
from app.models.schema import FeatureFlag, AirportManagement, Coupon, BrandingProfile
from app.security.dependencies import get_required_admin, get_required_user
from app.schemas.admin import AdminApiResponse

from app.services.service_config_service import ServiceConfigService

router = APIRouter(tags=["System Configuration & Feature Flags"])

# ─── AIRPORT HUB SPECIFIC CONFIGURATION ───────────────────────────────────────

@router.get("/api/config/airports/{code}", response_model=AdminApiResponse)
@router.get("/api/airports/{code}/config", response_model=AdminApiResponse)
async def get_airport_hub_configuration(code: str, db: Session = Depends(get_db)):
    """Return database & catalog-driven packages, services, and rules for specified airport hub."""
    config_data = ServiceConfigService.get_airport_configuration(code, db=db)
    return AdminApiResponse(success=True, data=config_data)

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

@router.get("/api/airports/search")
async def search_airports_global(
    q: str = Query("", description="Search by IATA code, city, or airport name"),
    scope: str = Query("global", description="global = airports.csv only; supported = existing supported_airports table only"),
    journey_type: Optional[str] = Query(None, description="When scope=supported, optionally filter by ARRIVAL/DEPARTURE/TRANSIT mappings"),
    db: Session = Depends(get_db),
):
    """
    Two isolated sources:
    - scope=global → ./airports.csv (or ./data/airports.csv). Never used for service availability.
    - scope=supported → existing Neon supported_airports. Never mixed with CSV.
    """
    query = (q or "").strip()
    if (scope or "").lower() == "supported":
        from app.services.journey_engine import JourneyDetectionEngine

        airports = JourneyDetectionEngine.get_supported_airports(db, journey_type=journey_type)

        q_up = query.upper()
        rows = []
        for a in airports:
            if not query or q_up in a.iata_code or q_up in (a.airport_name or "").upper() or q_up in (a.city or "").upper() or q_up in (a.country or "").upper():
                rows.append({
                    "id": str(a.id),
                    "code": a.iata_code,
                    "iata_code": a.iata_code,
                    "name": a.airport_name,
                    "airport_name": a.airport_name,
                    "city": a.city,
                    "country": a.country,
                    "timezone": a.timezone,
                    "is_supported": True,
                })
        return {"success": True, "source": "supported_airports", "data": rows}

    from app.flight.csv_airports import search_global_csv_airports

    try:
        rows = search_global_csv_airports(query)
    except FileNotFoundError as exc:
        return {"success": False, "source": "airports.csv", "error": str(exc), "data": []}
    return {
        "success": True,
        "source": "airports.csv",
        "data": rows,
    }


@router.get("/api/airports")
async def list_public_airports(db: Session = Depends(get_db)):
    """Public booking list: airports actually configured in supported_airports."""
    from app.services.journey_engine import JourneyDetectionEngine

    airports = JourneyDetectionEngine.get_supported_airports(db)
    data = [
        {
            "id": str(a.id),
            "code": a.iata_code,
            "iata_code": a.iata_code,
            "name": a.airport_name,
            "airport_name": a.airport_name,
            "city": a.city,
            "country": a.country,
            "timezone": a.timezone,
            "is_supported": bool(a.is_supported and a.is_active),
            "isActive": a.is_active,
        }
        for a in airports
        if a.is_supported
    ]
    return {"success": True, "total": len(data), "data": data}


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


# ─── BRANDING ─────────────────────────────────────────────────────────────────

@router.get("/api/branding/active", response_model=AdminApiResponse)
async def get_active_branding(db: Session = Depends(get_db)):
    try:
        bp = db.scalar(select(BrandingProfile).where(BrandingProfile.is_active.is_(True)))
        if bp:
            res = {
                "id": str(bp.id),
                "company_name": bp.company_name,
                "company_tagline": bp.tagline,
                "tagline": bp.tagline,
                "logo_url": bp.logo_url,
                "primary_color": bp.primary_color,
                "secondary_color": bp.secondary_color,
                "is_active": bp.is_active,
                **(bp.metadata_fields or {}),
            }
            return AdminApiResponse(success=True, data=res)
    except Exception:
        pass

    return AdminApiResponse(success=True, data={
        "company_name": "Shafsky Aviation",
        "company_tagline": "Premier Aviation & Concierge Services",
        "tagline": "Premier Aviation & Concierge Services",
        "primary_color": "#5ed3ff",
        "secondary_color": "#06090f",
        "is_active": True,
    })


@router.post("/api/admin/branding", response_model=AdminApiResponse)
async def upsert_branding(
    body: Dict[str, Any] = Body(...),
    db: Session = Depends(get_db),
    _admin = Depends(get_required_admin)
):
    bpid_str = body.get("id")
    bp = None
    if bpid_str:
        try:
            bpid = uuid.UUID(bpid_str)
            bp = db.scalar(select(BrandingProfile).where(BrandingProfile.id == bpid))
        except ValueError:
            bp = None

    if not bp:
        bp = db.scalar(select(BrandingProfile).where(BrandingProfile.is_active.is_(True)))

    company_name = body.get("company_name") or "Shafsky Aviation"
    tagline = body.get("company_tagline") or body.get("tagline") or "Premier Aviation & Concierge Services"
    logo_url = body.get("logo_url")
    primary_color = body.get("primary_color") or "#5ed3ff"
    secondary_color = body.get("secondary_color") or "#06090f"

    metadata_fields = {
        k: v for k, v in body.items() if k not in [
            "id", "company_name", "company_tagline", "tagline", "logo_url", "primary_color", "secondary_color", "is_active"
        ]
    }

    if bp:
        bp.company_name = company_name
        bp.tagline = tagline
        bp.logo_url = logo_url
        bp.primary_color = primary_color
        bp.secondary_color = secondary_color
        bp.metadata_fields = metadata_fields
        bp.updated_at = datetime.now(timezone.utc)
    else:
        bp = BrandingProfile(
            company_name=company_name,
            tagline=tagline,
            logo_url=logo_url,
            primary_color=primary_color,
            secondary_color=secondary_color,
            is_active=True,
            metadata_fields=metadata_fields
        )
        db.add(bp)

    db.commit()
    db.refresh(bp)

    res = {
        "id": str(bp.id),
        "company_name": bp.company_name,
        "company_tagline": bp.tagline,
        "tagline": bp.tagline,
        "logo_url": bp.logo_url,
        "primary_color": bp.primary_color,
        "secondary_color": bp.secondary_color,
        "is_active": bp.is_active,
        **(bp.metadata_fields or {}),
    }
    return AdminApiResponse(success=True, data=res)


# ─── SERVICE CATALOG & SERVICE CONFIGURATION ─────────────────────────────────

from app.services.service_config_service import ServiceConfigService

@router.get("/api/services/catalog", response_model=AdminApiResponse)
@router.get("/api/services/categories", response_model=AdminApiResponse)
async def get_public_service_catalog(db: Session = Depends(get_db)):
    catalog = ServiceConfigService.get_public_catalog(db)
    return AdminApiResponse(success=True, data=catalog)


@router.get("/api/admin/services/config", response_model=AdminApiResponse)
async def get_admin_services_config(
    db: Session = Depends(get_db),
    _admin = Depends(get_required_admin)
):
    catalog = ServiceConfigService.get_admin_catalog(db)
    return AdminApiResponse(success=True, data=catalog)


@router.patch("/api/admin/services/config/{service_id}", response_model=AdminApiResponse)
@router.post("/api/admin/services/config", response_model=AdminApiResponse)
async def patch_admin_service_config(
    service_id: Optional[str] = None,
    payload: Dict[str, Any] = Body(...),
    db: Session = Depends(get_db),
    _admin = Depends(get_required_admin)
):
    target_id = service_id or payload.get("id") or payload.get("serviceId")
    if not target_id:
        raise HTTPException(status_code=400, detail="Service ID is required.")

    updated_sc = ServiceConfigService.update_service_config(db, target_id, payload)
    return AdminApiResponse(
        success=True,
        data={
            "id": updated_sc.id,
            "title": updated_sc.title,
            "category": updated_sc.category,
            "description": updated_sc.description,
            "basePrice": float(updated_sc.base_price),
            "currency": updated_sc.currency,
            "isActive": updated_sc.is_active,
            "isHidden": updated_sc.is_hidden,
            "sortOrder": updated_sc.sort_order,
            "features": updated_sc.features,
            "optionsSchema": updated_sc.options_schema
        }
    )

