"""
FastAPI Router for Phase B.4 — Workflow Administration & Operations API.
"""

from typing import Optional, List, Dict, Any
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session
from app.database import get_db
from app.security.dependencies import (
    get_required_user,
    get_required_staff_or_admin,
    get_required_admin,
)
from app.schemas.workflow import WorkflowInstanceResponse
from app.schemas.workflow_admin import (
    PaginatedActiveDashboardResponse,
    WorkflowSearchRequest,
    WorkflowSearchResponse,
    WorkflowMetricsResponse,
    UnifiedTimelineResponse,
    PaginatedFailedWorkflowsResponse,
    WorkflowAdminActionRequest,
    ForceTransitionRequest,
    WorkflowSystemHealthResponse,
)
from app.services.workflow_admin_service import WorkflowAdminService
from app.workflow.engine import WorkflowEngine

router = APIRouter(prefix="/api/workflows/admin", tags=["Workflow Administration & Operations"])


# ─────────────────────────────────────────────
# 1. Active Workflow Dashboard
# ─────────────────────────────────────────────

@router.get(
    "/dashboard",
    response_model=PaginatedActiveDashboardResponse,
    status_code=status.HTTP_200_OK,
    summary="Active Workflow Dashboard",
    description="Retrieves paginated active workflows with optional filters for service, state, staff, and airport."
)
def get_dashboard_endpoint(
    service_type: Optional[str] = Query(None, description="Filter by service type"),
    state: Optional[str] = Query(None, description="Filter by state name"),
    assigned_staff: Optional[str] = Query(None, description="Filter by assigned staff ID"),
    airport: Optional[str] = Query(None, description="Filter by airport code"),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    sort_by: str = Query("created_at", description="Field to sort by"),
    sort_order: str = Query("desc", pattern="^(asc|desc)$"),
    db: Session = Depends(get_db),
    current_user = Depends(get_required_staff_or_admin)
):
    result = WorkflowAdminService.get_active_workflows(
        db,
        service_type=service_type,
        state=state,
        assigned_staff=assigned_staff,
        airport=airport,
        limit=limit,
        offset=offset,
        sort_by=sort_by,
        sort_order=sort_order
    )
    return PaginatedActiveDashboardResponse(
        success=True,
        total=result["total"],
        limit=result["limit"],
        offset=result["offset"],
        data=result["data"]
    )


# ─────────────────────────────────────────────
# 2. Workflow Multi-field Search
# ─────────────────────────────────────────────

@router.post(
    "/search",
    response_model=WorkflowSearchResponse,
    status_code=status.HTTP_200_OK,
    summary="Multi-field Workflow Search",
    description="Search workflows by Workflow ID, Booking ID, Entity ID, Passenger Name, Flight #, AWB, Visa Ref, Hotel Conf, PNR."
)
def search_workflows_endpoint(
    data: WorkflowSearchRequest,
    db: Session = Depends(get_db),
    current_user = Depends(get_required_staff_or_admin)
):
    result = WorkflowAdminService.search_workflows(
        db,
        query_str=data.query,
        service_type=data.service_type,
        state=data.state,
        limit=data.limit,
        offset=data.offset
    )
    return WorkflowSearchResponse(
        success=True,
        query=data.query,
        total=result["total"],
        limit=result["limit"],
        offset=result["offset"],
        results=result["results"]
    )


# ─────────────────────────────────────────────
# 3. Workflow Engine Metrics
# ─────────────────────────────────────────────

@router.get(
    "/metrics",
    response_model=WorkflowMetricsResponse,
    status_code=status.HTTP_200_OK,
    summary="Workflow Performance Metrics",
    description="Calculates system-wide totals, active, completed, cancelled, completion time, SLA breaches, and transition counts."
)
def get_metrics_endpoint(
    db: Session = Depends(get_db),
    current_user = Depends(get_required_staff_or_admin)
):
    return WorkflowAdminService.get_workflow_metrics(db)


# ─────────────────────────────────────────────
# 4. Unified Timeline API
# ─────────────────────────────────────────────

@router.get(
    "/instances/{instance_id}/timeline",
    response_model=UnifiedTimelineResponse,
    status_code=status.HTTP_200_OK,
    summary="Unified Instance Timeline",
    description="Synthesizes a time-sorted unified timeline merging transition history, audit logs, and event bus records."
)
def get_unified_timeline_endpoint(
    instance_id: UUID,
    db: Session = Depends(get_db),
    current_user = Depends(get_required_staff_or_admin)
):
    try:
        res = WorkflowAdminService.get_unified_timeline(db, instance_id)
        return UnifiedTimelineResponse(
            success=True,
            instance_id=instance_id,
            total=res["total"],
            timeline=res["timeline"]
        )
    except ValueError as err:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(err)) from err


# ─────────────────────────────────────────────
# 5. Failed Workflow Monitoring
# ─────────────────────────────────────────────

@router.get(
    "/failed",
    response_model=PaginatedFailedWorkflowsResponse,
    status_code=status.HTTP_200_OK,
    summary="Failed Workflows Monitoring",
    description="Lists rejected transitions, guard failures, system exceptions, and error details with pagination."
)
def get_failed_workflows_endpoint(
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    current_user = Depends(get_required_staff_or_admin)
):
    res = WorkflowAdminService.get_failed_workflows(db, limit=limit, offset=offset)
    return PaginatedFailedWorkflowsResponse(
        success=True,
        total=res["total"],
        limit=res["limit"],
        offset=res["offset"],
        data=res["data"]
    )


# ─────────────────────────────────────────────
# 6. Admin Retry Operations
# ─────────────────────────────────────────────

@router.post(
    "/instances/{instance_id}/retry",
    response_model=WorkflowInstanceResponse,
    status_code=status.HTTP_200_OK,
    summary="Admin Retry Operation",
    description="Admin-only retry operation for failed workflows, recording an explicit audit trail."
)
def retry_workflow_endpoint(
    instance_id: UUID,
    data: Optional[WorkflowAdminActionRequest] = None,
    db: Session = Depends(get_db),
    current_user = Depends(get_required_admin)
):
    actor_id = current_user.get("sub") or current_user.get("email") or "ADMIN"
    reason = data.reason if data else None
    try:
        return WorkflowAdminService.retry_workflow(db, instance_id, actor_id=actor_id, reason=reason)
    except ValueError as err:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(err)) from err


# ─────────────────────────────────────────────
# 7. Workflow Engine System Health
# ─────────────────────────────────────────────

@router.get(
    "/health",
    response_model=WorkflowSystemHealthResponse,
    status_code=status.HTTP_200_OK,
    summary="Workflow System Deep Health",
    description="Runs deep health check diagnostics across Workflow Engine, Redis, Database pool, and Event Bus."
)
def get_workflow_health_endpoint(
    db: Session = Depends(get_db),
    current_user = Depends(get_required_staff_or_admin)
):
    return WorkflowAdminService.get_workflow_system_health(db)


# ─────────────────────────────────────────────
# 8. Administration Lifecycle APIs
# ─────────────────────────────────────────────

@router.post(
    "/instances/{instance_id}/freeze",
    response_model=WorkflowInstanceResponse,
    status_code=status.HTTP_200_OK,
    summary="Freeze Workflow Instance",
    description="Admin-only endpoint to freeze a workflow instance, blocking non-admin transitions."
)
def freeze_workflow_endpoint(
    instance_id: UUID,
    data: Optional[WorkflowAdminActionRequest] = None,
    db: Session = Depends(get_db),
    current_user = Depends(get_required_admin)
):
    actor_id = current_user.get("sub") or current_user.get("email") or "ADMIN"
    reason = data.reason if data else None
    try:
        return WorkflowEngine.freeze_instance(db, instance_id, actor_id=actor_id, reason=reason)
    except ValueError as err:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(err)) from err


@router.post(
    "/instances/{instance_id}/resume",
    response_model=WorkflowInstanceResponse,
    status_code=status.HTTP_200_OK,
    summary="Resume Workflow Instance",
    description="Admin-only endpoint to resume a frozen workflow instance."
)
def resume_workflow_endpoint(
    instance_id: UUID,
    db: Session = Depends(get_db),
    current_user = Depends(get_required_admin)
):
    actor_id = current_user.get("sub") or current_user.get("email") or "ADMIN"
    try:
        return WorkflowEngine.resume_instance(db, instance_id, actor_id=actor_id)
    except ValueError as err:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(err)) from err


@router.post(
    "/instances/{instance_id}/cancel",
    response_model=WorkflowInstanceResponse,
    status_code=status.HTTP_200_OK,
    summary="Cancel Workflow Instance",
    description="Admin-only endpoint to cancel a workflow instance and mark it completed."
)
def cancel_workflow_endpoint(
    instance_id: UUID,
    data: Optional[WorkflowAdminActionRequest] = None,
    db: Session = Depends(get_db),
    current_user = Depends(get_required_admin)
):
    actor_id = current_user.get("sub") or current_user.get("email") or "ADMIN"
    reason = data.reason if data else None
    try:
        return WorkflowEngine.cancel_instance(db, instance_id, actor_id=actor_id, reason=reason)
    except ValueError as err:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(err)) from err


@router.post(
    "/instances/{instance_id}/force-transition",
    response_model=WorkflowInstanceResponse,
    status_code=status.HTTP_200_OK,
    summary="Force State Transition",
    description="Admin-only endpoint to force transition a workflow to target state, bypassing standard guards with an audit trail."
)
def force_transition_endpoint(
    instance_id: UUID,
    data: ForceTransitionRequest,
    db: Session = Depends(get_db),
    current_user = Depends(get_required_admin)
):
    actor_id = current_user.get("sub") or current_user.get("email") or "ADMIN"
    actor_role = current_user.get("role", "SUPER_ADMIN")
    try:
        return WorkflowEngine.force_transition(
            db,
            instance_id=instance_id,
            target_state=data.target_state,
            actor_id=actor_id,
            actor_role=actor_role,
            reason=data.reason
        )
    except ValueError as err:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(err)) from err
