"""
Pydantic Schemas for Workflow Engine API Endpoints.
"""

from typing import Dict, Any, Optional, List
from datetime import datetime
from uuid import UUID
from pydantic import BaseModel, Field


class WorkflowDefinitionCreate(BaseModel):
    service_type: str = Field(..., description="Service domain code (e.g. VISA_ASSISTANCE, AIR_CARGO, AIRPORT_MEET_AND_ASSIST)", example="VISA_ASSISTANCE")
    name: str = Field(..., description="Human readable workflow name", example="Custom Visa Workflow")
    initial_state: str = Field(..., description="Starting state name", example="DOCUMENT_COLLECTION")
    states_config: Dict[str, Any] = Field(..., description="Complete states configuration dictionary with allowed actions, roles, and guards")
    description: Optional[str] = Field(None, description="Optional workflow description")


class WorkflowDefinitionResponse(BaseModel):
    id: UUID
    service_type: str
    name: str
    version: int
    initial_state: str
    states_config: Dict[str, Any]
    is_active: bool
    created_at: datetime

    class Config:
        from_attributes = True


class WorkflowInstanceCreate(BaseModel):
    service_type: str = Field(..., description="Service domain code", example="AIRPORT_MEET_AND_ASSIST")
    entity_id: str = Field(..., description="Target domain entity reference ID", example="SHF-20260731-9090")
    initial_context: Optional[Dict[str, Any]] = Field(default_factory=dict, description="Initial context payload dictionary")
    version: Optional[int] = Field(None, description="Optional version pin for workflow definition")


class WorkflowInstanceResponse(BaseModel):
    id: UUID
    workflow_definition_id: UUID
    service_type: str
    entity_id: str
    current_state: str
    context_data: Dict[str, Any]
    is_completed: bool
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class WorkflowTransitionRequest(BaseModel):
    action: str = Field(..., description="Action name to execute from current state", example="CONFIRM")
    payload: Optional[Dict[str, Any]] = Field(default_factory=dict, description="Payload attributes for transition guard evaluation")


class WorkflowHistoryResponse(BaseModel):
    id: UUID
    instance_id: UUID
    from_state: str
    to_state: str
    action: str
    actor_id: Optional[str]
    actor_role: Optional[str]
    payload: Dict[str, Any]
    transition_metadata: Dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime

    class Config:
        from_attributes = True


class WorkflowAuditLogResponse(BaseModel):
    id: UUID
    instance_id: UUID
    event_type: str
    actor_id: Optional[str]
    details: Dict[str, Any]
    created_at: datetime

    class Config:
        from_attributes = True


class WorkflowHistoryDetailsResponse(BaseModel):
    instance: WorkflowInstanceResponse
    history: List[WorkflowHistoryResponse]
    audit_logs: List[WorkflowAuditLogResponse]


class PaginatedWorkflowHistoryResponse(BaseModel):
    success: bool = True
    total: int
    limit: int
    offset: int
    data: List[WorkflowHistoryResponse]


class PaginatedWorkflowAuditResponse(BaseModel):
    success: bool = True
    total: int
    limit: int
    offset: int
    data: List[WorkflowAuditLogResponse]
