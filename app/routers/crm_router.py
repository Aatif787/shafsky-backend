from fastapi import APIRouter, Depends, HTTPException, Header, Query
from sqlalchemy.orm import Session
from typing import Optional, List, Dict, Any

from app.database import get_db
from app.schemas.crm import (
    CustomerCreate,
    CustomerUpdate,
    CaseCreate,
    CaseUpdate,
    CrmApiResponse
)
from app.services.crm_service import CrmService
from app.services.auth_service import AuthService

router = APIRouter(prefix="/api/crm", tags=["Enterprise CRM & Customer Management"])

def get_required_staff_or_admin(authorization: Optional[str] = Header(None)) -> Dict[str, Any]:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid authorization token.")
    token = authorization.split(" ")[1]
    try:
        decoded = AuthService.decode_access_token(token)
        allowed_roles = [
            "SUPER_ADMIN", "ADMIN", "OPERATIONS_MANAGER", "DUTY_OFFICER",
            "MEET_AND_ASSIST_STAFF", "CONCIERGE_TEAM", "CUSTOMER_SUPPORT"
        ]
        if decoded.get("role") not in allowed_roles:
            raise HTTPException(status_code=403, detail="Access denied. Staff or administrative privileges required.")
        return decoded
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=401, detail="Token expired or invalid.")

@router.post("/customers", response_model=CrmApiResponse)
async def create_customer(
    payload: CustomerCreate,
    db: Session = Depends(get_db),
    actor_context: Dict[str, Any] = Depends(get_required_staff_or_admin)
):
    customer = CrmService.create_customer(db, payload, actor_email=actor_context.get("sub", "system@shafsky.com"))
    return CrmApiResponse(success=True, data=customer)

@router.get("/customers", response_model=CrmApiResponse)
async def search_customers(
    query: Optional[str] = Query(None, description="Search by Name, Email, Phone, Company, Passport"),
    vip_tier: Optional[str] = Query(None, description="Filter by VIP Tier"),
    limit: int = Query(50, le=500),
    db: Session = Depends(get_db),
    actor_context: Dict[str, Any] = Depends(get_required_staff_or_admin)
):
    results = CrmService.search_customers(db, query=query, vip_tier=vip_tier, limit=limit)
    return CrmApiResponse(success=True, data=results)

@router.get("/customers/{customer_id}", response_model=CrmApiResponse)
async def get_customer_details(
    customer_id: str,
    db: Session = Depends(get_db),
    actor_context: Dict[str, Any] = Depends(get_required_staff_or_admin)
):
    details = CrmService.get_customer_details_and_stats(db, customer_id)
    return CrmApiResponse(success=True, data=details)

@router.put("/customers/{customer_id}", response_model=CrmApiResponse)
async def update_customer(
    customer_id: str,
    payload: CustomerUpdate,
    db: Session = Depends(get_db),
    actor_context: Dict[str, Any] = Depends(get_required_staff_or_admin)
):
    updated = CrmService.update_customer(db, customer_id, payload, actor_email=actor_context.get("sub", "system@shafsky.com"))
    return CrmApiResponse(success=True, data=updated)

@router.delete("/customers/{customer_id}", response_model=CrmApiResponse)
async def soft_delete_customer(
    customer_id: str,
    db: Session = Depends(get_db),
    actor_context: Dict[str, Any] = Depends(get_required_staff_or_admin)
):
    res = CrmService.soft_delete_customer(db, customer_id, actor_email=actor_context.get("sub", "system@shafsky.com"))
    return CrmApiResponse(success=True, data=res)

@router.get("/customers/{customer_id}/timeline", response_model=CrmApiResponse)
async def get_customer_timeline(
    customer_id: str,
    db: Session = Depends(get_db),
    actor_context: Dict[str, Any] = Depends(get_required_staff_or_admin)
):
    timeline = CrmService.get_customer_timeline(db, customer_id)
    return CrmApiResponse(success=True, data=timeline)

@router.post("/cases", response_model=CrmApiResponse)
async def create_case(
    payload: CaseCreate,
    db: Session = Depends(get_db),
    actor_context: Dict[str, Any] = Depends(get_required_staff_or_admin)
):
    case = CrmService.create_case(db, payload, actor_email=actor_context.get("sub", "system@shafsky.com"))
    return CrmApiResponse(success=True, data=case)

@router.get("/cases", response_model=CrmApiResponse)
async def list_cases(
    status: Optional[str] = Query(None, description="Filter by Case Status"),
    db: Session = Depends(get_db),
    actor_context: Dict[str, Any] = Depends(get_required_staff_or_admin)
):
    cases = CrmService.list_cases(db, status=status)
    return CrmApiResponse(success=True, data=cases)

@router.patch("/cases/{case_id}", response_model=CrmApiResponse)
async def update_case(
    case_id: str,
    payload: CaseUpdate,
    db: Session = Depends(get_db),
    actor_context: Dict[str, Any] = Depends(get_required_staff_or_admin)
):
    updated = CrmService.update_case(db, case_id, payload, actor_email=actor_context.get("sub", "system@shafsky.com"))
    return CrmApiResponse(success=True, data=updated)

@router.get("/reports/stats", response_model=CrmApiResponse)
async def get_crm_stats(
    db: Session = Depends(get_db),
    actor_context: Dict[str, Any] = Depends(get_required_staff_or_admin)
):
    stats = CrmService.get_crm_stats(db)
    return CrmApiResponse(success=True, data=stats)
