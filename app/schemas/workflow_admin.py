"""
Pydantic Schemas for Phase B.4 — Workflow Administration & Operations API.
"""

from typing import Dict, Any, Optional, List
from datetime import datetime
from uuid import UUID
from pydantic import BaseModel, Field
from app.schemas.workflow import WorkflowInstanceResponse


# ─────────────────────────────────────────────
# Active Dashboard Schemas
# ─────────────────────────────────────────────

class ActiveWorkflowItem(BaseModel):
    id: UUID
    workflow_definition_id: UUID
    service_type: str
    entity_id: str
    current_state: str
    context_data: Dict[str, Any] = {}
    is_completed: bool = False
    is_frozen: bool = False
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class PaginatedActiveDashboardResponse(BaseModel):
    success: bool = True
    total: int
    limit: int
    offset: int
    data: List[ActiveWorkflowItem] = []


# ─────────────────────────────────────────────
# Search Schemas
# ─────────────────────────────────────────────

class WorkflowSearchRequest(BaseModel):
    query: str = Field(..., min_length=1, description="Search term across Workflow ID, Booking ID, Entity ID, Passenger Name, Flight #, AWB, Visa Ref, Hotel Conf, PNR")
    service_type: Optional[str] = None
    state: Optional[str] = None
    limit: int = Field(default=20, ge=1, le=100)
    offset: int = Field(default=0, ge=0)


class WorkflowSearchResponse(BaseModel):
    success: bool = True
    query: str
    total: int
    limit: int
    offset: int
    results: List[ActiveWorkflowItem] = []


# ─────────────────────────────────────────────
# Workflow Metrics Schema
# ─────────────────────────────────────────────

class WorkflowMetricsResponse(BaseModel):
    total_workflows: int
    active_workflows: int
    completed_workflows: int
    cancelled_workflows: int
    frozen_workflows: int
    avg_completion_time_minutes: float
    sla_breaches_count: int
    total_transitions: int
    avg_transitions_per_workflow: float
    services_breakdown: Dict[str, int] = {}


# ─────────────────────────────────────────────
# Unified Timeline Schemas
# ─────────────────────────────────────────────

class UnifiedTimelineItem(BaseModel):
    source: str = Field(..., description="HISTORY, AUDIT, or EVENT_BUS")
    event_type: str
    title: str
    timestamp: datetime
    actor_id: Optional[str] = None
    actor_role: Optional[str] = None
    from_state: Optional[str] = None
    to_state: Optional[str] = None
    action: Optional[str] = None
    details: Dict[str, Any] = {}


class UnifiedTimelineResponse(BaseModel):
    success: bool = True
    instance_id: UUID
    total: int
    timeline: List[UnifiedTimelineItem] = []


# ─────────────────────────────────────────────
# Failed Workflow Monitoring Schemas
# ─────────────────────────────────────────────

class FailedWorkflowItem(BaseModel):
    instance_id: UUID
    service_type: str
    entity_id: str
    current_state: str
    failed_event_type: str
    error_message: str
    actor_id: Optional[str] = None
    timestamp: datetime
    details: Dict[str, Any] = {}


class PaginatedFailedWorkflowsResponse(BaseModel):
    success: bool = True
    total: int
    limit: int
    offset: int
    data: List[FailedWorkflowItem] = []


# ─────────────────────────────────────────────
# Retry & Administrative Action Schemas
# ─────────────────────────────────────────────

class WorkflowAdminActionRequest(BaseModel):
    reason: Optional[str] = Field(None, description="Reason for admin action")


class ForceTransitionRequest(BaseModel):
    target_state: str = Field(..., description="Target state to force transition into")
    reason: Optional[str] = Field(None, description="Justification for force transition")


# ─────────────────────────────────────────────
# Health Diagnostic Schemas
# ─────────────────────────────────────────────

class SystemComponentHealth(BaseModel):
    status: str = Field(..., description="healthy, degraded, or unhealthy")
    latency_ms: Optional[float] = None
    details: Dict[str, Any] = {}


class WorkflowSystemHealthResponse(BaseModel):
    status: str = Field(..., description="HEALTHY, DEGRADED, or UNHEALTHY")
    timestamp: datetime
    workflow_engine: SystemComponentHealth
    redis: SystemComponentHealth
    database: SystemComponentHealth
    event_bus: SystemComponentHealth
