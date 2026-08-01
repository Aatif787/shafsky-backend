from typing import Optional, Dict, Any
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

import uuid
from datetime import datetime, timezone
from sqlalchemy import select, update, delete
from app.models.schema import AirportManagement, FeatureFlag, Coupon

from app.database import get_db
from app.schemas.admin import (
    AdminApiResponse,
    RoleUpdateRequest,
    StaffAssignRequest,
    ShiftCreateRequest,
    AirportCreateRequest
)
from app.services.admin_service import AdminService
from app.security.dependencies import (
    get_required_admin,
    get_required_super_admin
)

router = APIRouter(prefix="/api/admin", tags=["Admin & Super Admin Engine"])

@router.get("/dashboard", response_model=AdminApiResponse)
async def get_admin_dashboard(
    db: Session = Depends(get_db),
    _admin_context: Dict[str, Any] = Depends(get_required_admin)
):
    daily = AdminService.generate_daily_report(db)
    return AdminApiResponse(
        success=True,
        data={
            "status": "Active",
            "dailyRevenueINR": daily["dailyRevenueINR"],
            "todayBookings": daily["totalBookings"],
            "completedToday": daily["completedBookings"],
            "engine": "FastAPI Enterprise Admin Engine"
        }
    )

# Analytics Reports
@router.get("/reports/daily", response_model=AdminApiResponse)
async def get_daily_report(
    db: Session = Depends(get_db),
    _admin_context: Dict[str, Any] = Depends(get_required_admin)
):
    data = AdminService.generate_daily_report(db)
    return AdminApiResponse(success=True, data=data)

@router.get("/reports/weekly", response_model=AdminApiResponse)
async def get_weekly_report(
    db: Session = Depends(get_db),
    _admin_context: Dict[str, Any] = Depends(get_required_admin)
):
    data = AdminService.generate_weekly_report(db)
    return AdminApiResponse(success=True, data=data)

@router.get("/reports/monthly", response_model=AdminApiResponse)
async def get_monthly_report(
    db: Session = Depends(get_db),
    _admin_context: Dict[str, Any] = Depends(get_required_admin)
):
    data = AdminService.generate_monthly_report(db)
    return AdminApiResponse(success=True, data=data)

@router.get("/reports/revenue", response_model=AdminApiResponse)
async def get_revenue_report(
    db: Session = Depends(get_db),
    _admin_context: Dict[str, Any] = Depends(get_required_admin)
):
    data = AdminService.generate_revenue_report(db)
    return AdminApiResponse(success=True, data=data)

@router.get("/reports/staff-performance", response_model=AdminApiResponse)
async def get_staff_performance_report(
    db: Session = Depends(get_db),
    _admin_context: Dict[str, Any] = Depends(get_required_admin)
):
    data = AdminService.generate_staff_performance(db)
    return AdminApiResponse(success=True, data=data)

@router.get("/reports/airport-stats", response_model=AdminApiResponse)
async def get_airport_stats_report(
    db: Session = Depends(get_db),
    _admin_context: Dict[str, Any] = Depends(get_required_admin)
):
    data = AdminService.generate_airport_stats(db)
    return AdminApiResponse(success=True, data=data)

# Task Assignments
@router.post("/assignments", response_model=AdminApiResponse)
async def assign_staff_to_booking(
    payload: StaffAssignRequest,
    db: Session = Depends(get_db),
    admin_context: Dict[str, Any] = Depends(get_required_admin)
):
    admin_email = admin_context.get("email", "admin@shafskyaviation.com")
    result = AdminService.assign_staff(db, payload, admin_email=admin_email)
    return AdminApiResponse(success=True, data=result)

@router.get("/assignments/booking/{booking_id}", response_model=AdminApiResponse)
async def get_booking_assignments(
    booking_id: str,
    db: Session = Depends(get_db),
    _admin_context: Dict[str, Any] = Depends(get_required_admin)
):
    assignments = AdminService.get_booking_assignments(db, booking_id)
    return AdminApiResponse(success=True, data=assignments)

# Duty Shift Roster
@router.post("/shifts", response_model=AdminApiResponse)
async def create_shift_record(
    payload: ShiftCreateRequest,
    db: Session = Depends(get_db),
    admin_context: Dict[str, Any] = Depends(get_required_admin)
):
    admin_email = admin_context.get("email", "admin@shafskyaviation.com")
    shift = AdminService.create_shift(db, payload, admin_email=admin_email)
    return AdminApiResponse(success=True, data=shift)

@router.get("/shifts", response_model=AdminApiResponse)
async def get_shift_roster(
    airport_code: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    _admin_context: Dict[str, Any] = Depends(get_required_admin)
):
    roster = AdminService.get_shift_roster(db, airport_code=airport_code)
    return AdminApiResponse(success=True, data=roster)

# Airport Operations Config
@router.post("/airports", response_model=AdminApiResponse)
async def manage_airport_config(
    payload: AirportCreateRequest,
    db: Session = Depends(get_db),
    admin_context: Dict[str, Any] = Depends(get_required_admin)
):
    admin_email = admin_context.get("email", "admin@shafskyaviation.com")
    airport = AdminService.manage_airport(db, payload, admin_email=admin_email)
    return AdminApiResponse(success=True, data=airport)

@router.get("/airports", response_model=AdminApiResponse)
async def list_airports(
    db: Session = Depends(get_db),
    _admin_context: Dict[str, Any] = Depends(get_required_admin)
):
    airports = AdminService.list_airports(db)
    return AdminApiResponse(success=True, data=airports)

# Audit Trail Logs
@router.get("/audit-logs", response_model=AdminApiResponse)
async def get_audit_logs(
    limit: int = Query(100, le=500),
    db: Session = Depends(get_db),
    _admin_context: Dict[str, Any] = Depends(get_required_admin)
):
    logs = AdminService.get_audit_logs(db, limit=limit)
    return AdminApiResponse(success=True, data=logs)

# Super Admin Role Management
@router.patch("/users/{target_user_id}/role", response_model=AdminApiResponse)
@router.patch("/users/{target_user_id}/roles", response_model=AdminApiResponse)
async def update_user_role(
    target_user_id: str,
    payload: RoleUpdateRequest,
    db: Session = Depends(get_db),
    super_admin_context: Dict[str, Any] = Depends(get_required_super_admin)
):
    admin_email = super_admin_context.get("email", "superadmin@shafskyaviation.com")
    result = AdminService.update_user_role(db, target_user_id, payload.role, admin_email=admin_email)
    return AdminApiResponse(success=True, data=result)


# Airport Management PATCH & DELETE
@router.patch("/airports/{airport_code}", response_model=AdminApiResponse)
async def patch_airport_config(
    airport_code: str,
    payload: Dict[str, Any],
    db: Session = Depends(get_db),
    _admin_context: Dict[str, Any] = Depends(get_required_admin)
):
    code = airport_code.upper().strip()
    airport = db.scalar(select(AirportManagement).where(AirportManagement.code == code))
    if not airport:
        return AdminApiResponse(success=False, error=f"Airport '{code}' not found.")

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
            "servicesConfig": airport.services_config,
        }
    )


@router.delete("/airports/{airport_code}", response_model=AdminApiResponse)
async def delete_airport_config(
    airport_code: str,
    db: Session = Depends(get_db),
    _admin_context: Dict[str, Any] = Depends(get_required_admin)
):
    code = airport_code.upper().strip()
    airport = db.scalar(select(AirportManagement).where(AirportManagement.code == code))
    if not airport:
        return AdminApiResponse(success=False, error=f"Airport '{code}' not found.")

    airport.is_active = False
    airport.updated_at = datetime.now(timezone.utc)
    db.commit()

    return AdminApiResponse(
        success=True,
        data={"code": code, "message": f"Airport '{code}' has been deactivated."}
    )


# Feature Flags Management
@router.get("/feature-flags", response_model=AdminApiResponse)
async def get_feature_flags(
    db: Session = Depends(get_db),
    _admin_context: Dict[str, Any] = Depends(get_required_admin)
):
    flags = list(db.scalars(select(FeatureFlag)).all())
    data = [
        {
            "id": f.id,
            "name": f.name,
            "description": f.description,
            "isEnabled": f.is_enabled,
            "rules": f.rules,
            "updatedAt": f.updated_at.isoformat() if f.updated_at else None,
        }
        for f in flags
    ]
    return AdminApiResponse(success=True, data=data)


@router.patch("/feature-flags", response_model=AdminApiResponse)
async def patch_feature_flags(
    payload: Dict[str, Any],
    db: Session = Depends(get_db),
    _admin_context: Dict[str, Any] = Depends(get_required_admin)
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
            if isinstance(val, dict) and "rules" in val:
                flag.rules = val["rules"]
            flag.updated_at = datetime.now(timezone.utc)
        updated.append(flag_key)

    db.commit()
    return AdminApiResponse(success=True, data={"updated": updated, "message": "Feature flags updated."})


# Roles & Permissions Catalog
@router.get("/roles", response_model=AdminApiResponse)
async def get_roles(
    _admin_context: Dict[str, Any] = Depends(get_required_admin)
):
    roles = [
        {"name": "SUPER_ADMIN", "description": "Root-level system administrator."},
        {"name": "ADMIN", "description": "Operations administrator."},
        {"name": "OPERATIONS_MANAGER", "description": "Airport operations manager."},
        {"name": "DUTY_OFFICER", "description": "Duty officer on shift."},
        {"name": "MEET_AND_ASSIST_STAFF", "description": "Ground concierge staff."},
        {"name": "DRIVER", "description": "Chauffeur and transport driver."},
        {"name": "CONCIERGE_TEAM", "description": "Concierge support team."},
        {"name": "CUSTOMER_SUPPORT", "description": "Customer care representative."},
        {"name": "CUSTOMER", "description": "End-user customer."},
    ]
    return AdminApiResponse(success=True, data=roles)


@router.get("/permissions", response_model=AdminApiResponse)
async def get_permissions(
    _admin_context: Dict[str, Any] = Depends(get_required_admin)
):
    permissions = [
        {"id": "bookings:read", "description": "Read all bookings"},
        {"id": "bookings:write", "description": "Create/update bookings"},
        {"id": "bookings:assign", "description": "Assign staff to bookings"},
        {"id": "customers:read", "description": "Read customer profiles"},
        {"id": "customers:write", "description": "Update customer profiles"},
        {"id": "services:read", "description": "Read services catalog"},
        {"id": "services:write", "description": "Modify service pricing"},
        {"id": "audit:read", "description": "Read audit logs"},
        {"id": "settings:write", "description": "Update settings"},
    ]
    matrix = {
        "SUPER_ADMIN": [p["id"] for p in permissions],
        "ADMIN": [p["id"] for p in permissions if p["id"] != "settings:write"],
        "CUSTOMER": [],
    }
    return AdminApiResponse(success=True, data={"permissions": permissions, "matrix": matrix})


# Coupons Management
@router.get("/coupons", response_model=AdminApiResponse)
async def list_coupons(
    db: Session = Depends(get_db),
    _admin_context: Dict[str, Any] = Depends(get_required_admin)
):
    coupons = list(db.scalars(select(Coupon).order_by(Coupon.created_at.desc())).all())
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
            "createdAt": c.created_at.isoformat() if c.created_at else None,
        }
        for c in coupons
    ]
    return AdminApiResponse(success=True, data=data)


@router.patch("/coupons/{coupon_id}/status", response_model=AdminApiResponse)
@router.patch("/coupons/{coupon_id}/toggle", response_model=AdminApiResponse)
async def toggle_coupon_status(
    coupon_id: str,
    payload: Optional[Dict[str, Any]] = None,
    db: Session = Depends(get_db),
    _admin_context: Dict[str, Any] = Depends(get_required_admin)
):
    try:
        c_uuid = uuid.UUID(coupon_id)
        cp = db.scalar(select(Coupon).where(Coupon.id == c_uuid))
    except Exception:
        cp = db.scalar(select(Coupon).where(Coupon.code == coupon_id.upper()))

    if not cp:
        return AdminApiResponse(success=False, error=f"Coupon '{coupon_id}' not found.")

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
        data={
            "id": str(cp.id),
            "code": cp.code,
            "isActive": cp.is_active,
            "message": f"Coupon '{cp.code}' status updated to {cp.is_active}.",
        }
    )
