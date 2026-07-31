from typing import Optional, Dict, Any
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas.crm import (
    CustomerCreate,
    CustomerUpdate,
    CaseCreate,
    CaseUpdate,
    CrmApiResponse
)
from app.services.crm_service import CrmService
from app.security.dependencies import get_required_staff_or_admin

router = APIRouter(prefix="/api/crm", tags=["Enterprise CRM & Customer Management"])

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
    _actor_context: Dict[str, Any] = Depends(get_required_staff_or_admin)
):
    results = CrmService.search_customers(db, query=query, vip_tier=vip_tier, limit=limit)
    return CrmApiResponse(success=True, data=results)

@router.get("/customers/{customer_id}", response_model=CrmApiResponse)
async def get_customer_details(
    customer_id: str,
    db: Session = Depends(get_db),
    _actor_context: Dict[str, Any] = Depends(get_required_staff_or_admin)
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
    _actor_context: Dict[str, Any] = Depends(get_required_staff_or_admin)
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
    _actor_context: Dict[str, Any] = Depends(get_required_staff_or_admin)
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
    _actor_context: Dict[str, Any] = Depends(get_required_staff_or_admin)
):
    stats = CrmService.get_crm_stats(db)
    return CrmApiResponse(success=True, data=stats)
