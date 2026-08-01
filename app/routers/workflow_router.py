"""
FastAPI Router for Enterprise Workflow Engine API (Phase B.3).

Provides generic, reusable REST APIs for workflow definitions, instances,
state transitions, execution history, and audit trail logs.
"""

from typing import Dict, Any, List, Optional
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Query, Header, status
from sqlalchemy.orm import Session
from sqlalchemy import desc, asc
from app.database import get_db
from app.models.schema import WorkflowDefinition, WorkflowInstance, WorkflowHistory, WorkflowAuditLog, Role
from app.security.dependencies import get_current_user_auth, require_role
from app.workflow.engine import WorkflowEngine
from app.workflow.definitions import seed_default_workflows
from app.schemas.workflow import (
    WorkflowDefinitionCreate,
    WorkflowDefinitionResponse,
    WorkflowInstanceCreate,
    WorkflowInstanceResponse,
    WorkflowTransitionRequest,
    WorkflowHistoryResponse,
    WorkflowAuditLogResponse,
    WorkflowHistoryDetailsResponse,
    PaginatedWorkflowHistoryResponse,
    PaginatedWorkflowAuditResponse
)

router = APIRouter(prefix="/api/workflows", tags=["Workflow Engine"])


@router.post(
    "/definitions/seed",
    response_model=Dict[str, Any],
    status_code=status.HTTP_201_CREATED,
    summary="Seed Default Workflow Definitions",
    description="Populates active workflow definitions for Airport, Ticketing, Hotel, Visa, and Cargo domains."
)
def seed_workflows_endpoint(
    db: Session = Depends(get_db),
    current_user = Depends(require_role([Role.ADMIN, Role.SUPER_ADMIN]))
):
    """Seed or update default workflow definitions for all 5 aviation service domains."""
    seeded = seed_default_workflows(db)
    return {"success": True, "seeded": seeded}


@router.post(
    "/definitions",
    response_model=WorkflowDefinitionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register Workflow Definition",
    description="Registers or updates a versioned workflow definition schema for a service type."
)
def create_definition_endpoint(
    data: WorkflowDefinitionCreate,
    db: Session = Depends(get_db),
    current_user = Depends(require_role([Role.ADMIN, Role.SUPER_ADMIN]))
):
    """Register a new active version of a workflow definition."""
    service_type = data.service_type.strip().upper()
    existing = db.query(WorkflowDefinition).filter(
        WorkflowDefinition.service_type == service_type,
        WorkflowDefinition.is_active == True
    ).first()

    if existing:
        existing.is_active = False
        db.flush()

    new_version = (existing.version + 1) if existing else 1
    new_def = WorkflowDefinition(
        service_type=service_type,
        name=data.name,
        version=new_version,
        initial_state=data.initial_state,
        states_config=data.states_config,
        is_active=True
    )
    db.add(new_def)
    db.commit()
    db.refresh(new_def)
    return new_def


@router.get(
    "/definitions/{service_type}",
    response_model=WorkflowDefinitionResponse,
    status_code=status.HTTP_200_OK,
    summary="Get Workflow Definition",
    description="Retrieves active or version-pinned workflow definition schema for a given service domain."
)
def get_definition_endpoint(
    service_type: str,
    version: Optional[int] = Query(None, description="Optional version pin"),
    db: Session = Depends(get_db)
):
    """Retrieve active or version-pinned workflow definition."""
    try:
        return WorkflowEngine.get_active_definition(db, service_type=service_type, version=version)
    except ValueError as err:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(err)) from err


@router.post(
    "/instances",
    response_model=WorkflowInstanceResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create Workflow Instance",
    description="Initializes a new workflow instance for an entity."
)
def create_instance_endpoint(
    data: WorkflowInstanceCreate,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user_auth),
    x_correlation_id: Optional[str] = Header(None, alias="X-Correlation-ID")
):
    """Initialize a new version-pinned workflow instance."""
    actor_id = (current_user.get("sub") or current_user.get("email")) if isinstance(current_user, dict) else "SYSTEM"
    try:
        return WorkflowEngine.create_instance(
            db,
            service_type=data.service_type,
            entity_id=data.entity_id,
            actor_id=actor_id,
            initial_context=data.initial_context,
            version=data.version
        )
    except ValueError as err:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(err)) from err


@router.get(
    "/instances/{instance_id}",
    response_model=WorkflowInstanceResponse,
    status_code=status.HTTP_200_OK,
    summary="Get Workflow Instance",
    description="Retrieves current status, state, and context data of a workflow instance."
)
def get_instance_endpoint(
    instance_id: UUID,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user_auth)
):
    """Retrieve workflow instance by ID."""
    instance = db.query(WorkflowInstance).filter(WorkflowInstance.id == instance_id).first()
    if not instance:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Workflow instance '{instance_id}' not found.")
    return instance


@router.post(
    "/instances/{instance_id}/transition",
    response_model=WorkflowInstanceResponse,
    status_code=status.HTTP_200_OK,
    summary="Execute Workflow Transition",
    description="Executes a state transition action on a workflow instance, evaluating roles and guard rules."
)
def execute_transition_endpoint(
    instance_id: UUID,
    data: WorkflowTransitionRequest,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user_auth),
    x_correlation_id: Optional[str] = Header(None, alias="X-Correlation-ID")
):
    """Execute state transition with guard evaluation and role authorization."""
    if isinstance(current_user, dict):
        actor_id = current_user.get("sub") or current_user.get("email") or "SYSTEM"
        actor_role = current_user.get("role", "CUSTOMER")
    else:
        actor_id = "SYSTEM"
        actor_role = "CUSTOMER"

    try:
        return WorkflowEngine.execute_transition(
            db,
            instance_id=instance_id,
            action=data.action,
            actor_id=actor_id,
            actor_role=actor_role,
            payload=data.payload,
            correlation_id=x_correlation_id
        )
    except ValueError as err:
        err_str = str(err)
        if "not found" in err_str.lower():
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=err_str) from err
        elif "not authorized" in err_str.lower():
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=err_str) from err
        else:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=err_str) from err


@router.get(
    "/instances/{instance_id}/history",
    response_model=PaginatedWorkflowHistoryResponse,
    status_code=status.HTTP_200_OK,
    summary="Get Workflow History",
    description="Retrieves chronological step execution history for a workflow instance with pagination and sorting."
)
def get_history_endpoint(
    instance_id: UUID,
    limit: int = Query(50, ge=1, le=100, description="Max items per page"),
    offset: int = Query(0, ge=0, description="Pagination offset"),
    sort: str = Query("asc", regex="^(asc|desc)$", description="Sort order by timestamp"),
    db: Session = Depends(get_db)
):
    """Retrieve paginated workflow execution history."""
    instance = db.query(WorkflowInstance).filter(WorkflowInstance.id == instance_id).first()
    if not instance:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Workflow instance '{instance_id}' not found.")

    query = db.query(WorkflowHistory).filter(WorkflowHistory.instance_id == instance_id)
    total = query.count()

    order_clause = asc(WorkflowHistory.timestamp) if sort.lower() == "asc" else desc(WorkflowHistory.timestamp)
    history_records = query.order_by(order_clause).offset(offset).limit(limit).all()

    return PaginatedWorkflowHistoryResponse(
        success=True,
        total=total,
        limit=limit,
        offset=offset,
        data=history_records
    )


@router.get(
    "/instances/{instance_id}/audit",
    response_model=PaginatedWorkflowAuditResponse,
    status_code=status.HTTP_200_OK,
    summary="Get Workflow Audit Trail",
    description="Retrieves workflow audit trail logs for an instance with optional event_type filter, pagination, and sorting."
)
def get_audit_endpoint(
    instance_id: UUID,
    event_type: Optional[str] = Query(None, description="Optional filter by event_type"),
    limit: int = Query(50, ge=1, le=100, description="Max items per page"),
    offset: int = Query(0, ge=0, description="Pagination offset"),
    sort: str = Query("asc", regex="^(asc|desc)$", description="Sort order by creation timestamp"),
    db: Session = Depends(get_db)
):
    """Retrieve paginated workflow audit log records."""
    instance = db.query(WorkflowInstance).filter(WorkflowInstance.id == instance_id).first()
    if not instance:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Workflow instance '{instance_id}' not found.")

    query = db.query(WorkflowAuditLog).filter(WorkflowAuditLog.instance_id == instance_id)
    if event_type:
        query = query.filter(WorkflowAuditLog.event_type == event_type.strip().upper())

    total = query.count()

    order_clause = asc(WorkflowAuditLog.created_at) if sort.lower() == "asc" else desc(WorkflowAuditLog.created_at)
    audit_records = query.order_by(order_clause).offset(offset).limit(limit).all()

    return PaginatedWorkflowAuditResponse(
        success=True,
        total=total,
        limit=limit,
        offset=offset,
        data=audit_records
    )
