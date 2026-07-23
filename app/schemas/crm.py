from typing import Optional, Dict, Any, List
from datetime import datetime
from pydantic import BaseModel, EmailStr, Field

class CustomerCreate(BaseModel):
    email: EmailStr
    full_name: str = Field(..., min_length=2, max_length=100)
    phone_number: Optional[str] = None
    company: Optional[str] = None
    vip_tier: Optional[str] = "REGULAR"
    passport_number: Optional[str] = None
    tags: Optional[Dict[str, Any]] = Field(default_factory=dict)
    documents_config: Optional[Dict[str, Any]] = Field(default_factory=dict)
    notes: Optional[str] = None

class CustomerUpdate(BaseModel):
    full_name: Optional[str] = None
    phone_number: Optional[str] = None
    company: Optional[str] = None
    vip_tier: Optional[str] = None
    passport_number: Optional[str] = None
    tags: Optional[Dict[str, Any]] = None
    documents_config: Optional[Dict[str, Any]] = None
    notes: Optional[str] = None

class CaseCreate(BaseModel):
    customer_id: str
    title: str = Field(..., min_length=2, max_length=200)
    description: Optional[str] = None
    category: Optional[str] = "GENERAL"
    assigned_to_id: Optional[str] = None

class CaseUpdate(BaseModel):
    status: Optional[str] = None
    assigned_to_id: Optional[str] = None
    resolution_notes: Optional[str] = None

class CrmApiResponse(BaseModel):
    success: bool
    data: Optional[Any] = None
    error: Optional[str] = None
