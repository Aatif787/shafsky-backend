from fastapi import APIRouter, Depends, HTTPException, Header, Query
from sqlalchemy.orm import Session
from typing import Optional, List, Dict, Any

from app.database import get_db
from app.schemas.admin import (
    AdminApiResponse,
    RoleUpdateRequest,
    StaffAssignRequest,
    ShiftCreateRequest,
    AirportCreateRequest
)
from app.services.admin_service import AdminService
from app.services.auth_service import AuthService

router = APIRouter(prefix="/api/admin", tags=["Admin & Super Admin Engine"])

def get_required_admin(authorization: Optional[str] = Header(None)) -> Dict[str, Any]:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid authorization token.")
    
    token = authorization.split(" ")[1]
    try:
        decoded = AuthService.decode_access_token(token)
        role = decoded.get("role")
        allowed_roles = [
            "SUPER_ADMIN", "ADMIN", "OPERATIONS_MANAGER",
            "DUTY_OFFICER", "CONCIERGE_TEAM", "CUSTOMER_SUPPORT", "DISPATCHER"
        ]
        if role not in allowed_roles:
            raise HTTPException(status_code=403, detail="Access denied. Insufficient administrative permissions.")
        return decoded
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=401, detail="Token expired or invalid.")

def get_required_super_admin(authorization: Optional[str] = Header(None)) -> Dict[str, Any]:
    user = get_required_admin(authorization)
    if user.get("role") != "SUPER_ADMIN":
        raise HTTPException(status_code=403, detail="Access denied. Super Admin privileges required.")
    return user

@router.get("/dashboard", response_model=AdminApiResponse)
async def get_admin_dashboard(
    db: Session = Depends(get_db),
    admin_context: Dict[str, Any] = Depends(get_required_admin)
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
    admin_context: Dict[str, Any] = Depends(get_required_admin)
):
    data = AdminService.generate_daily_report(db)
    return AdminApiResponse(success=True, data=data)

@router.get("/reports/weekly", response_model=AdminApiResponse)
async def get_weekly_report(
    db: Session = Depends(get_db),
    admin_context: Dict[str, Any] = Depends(get_required_admin)
):
    data = AdminService.generate_weekly_report(db)
    return AdminApiResponse(success=True, data=data)

@router.get("/reports/monthly", response_model=AdminApiResponse)
async def get_monthly_report(
    db: Session = Depends(get_db),
    admin_context: Dict[str, Any] = Depends(get_required_admin)
):
    data = AdminService.generate_monthly_report(db)
    return AdminApiResponse(success=True, data=data)

@router.get("/reports/revenue", response_model=AdminApiResponse)
async def get_revenue_report(
    db: Session = Depends(get_db),
    admin_context: Dict[str, Any] = Depends(get_required_admin)
):
    data = AdminService.generate_revenue_report(db)
    return AdminApiResponse(success=True, data=data)

@router.get("/reports/staff-performance", response_model=AdminApiResponse)
async def get_staff_performance_report(
    db: Session = Depends(get_db),
    admin_context: Dict[str, Any] = Depends(get_required_admin)
):
    data = AdminService.generate_staff_performance(db)
    return AdminApiResponse(success=True, data=data)

@router.get("/reports/airport-stats", response_model=AdminApiResponse)
async def get_airport_stats_report(
    db: Session = Depends(get_db),
    admin_context: Dict[str, Any] = Depends(get_required_admin)
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
    admin_context: Dict[str, Any] = Depends(get_required_admin)
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
    admin_context: Dict[str, Any] = Depends(get_required_admin)
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
    admin_context: Dict[str, Any] = Depends(get_required_admin)
):
    airports = AdminService.list_airports(db)
    return AdminApiResponse(success=True, data=airports)

# Audit Trail Logs
@router.get("/audit-logs", response_model=AdminApiResponse)
async def get_audit_logs(
    limit: int = Query(100, le=500),
    db: Session = Depends(get_db),
    admin_context: Dict[str, Any] = Depends(get_required_admin)
):
    logs = AdminService.get_audit_logs(db, limit=limit)
    return AdminApiResponse(success=True, data=logs)

# Super Admin Role Management
@router.patch("/users/{target_user_id}/role", response_model=AdminApiResponse)
async def update_user_role(
    target_user_id: str,
    payload: RoleUpdateRequest,
    db: Session = Depends(get_db),
    super_admin_context: Dict[str, Any] = Depends(get_required_super_admin)
):
    admin_email = super_admin_context.get("email", "superadmin@shafskyaviation.com")
    result = AdminService.update_user_role(db, target_user_id, payload.role, admin_email=admin_email)
    return AdminApiResponse(success=True, data=result)
