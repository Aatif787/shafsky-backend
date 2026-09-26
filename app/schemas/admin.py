from typing import Optional, Dict, Any, List
from datetime import datetime
from pydantic import BaseModel, Field

class RoleUpdateRequest(BaseModel):
    role: str

class StaffAssignRequest(BaseModel):
    booking_id: str
    staff_user_id: str
    role_type: str  # OFFICER, DRIVER, AIRPORT_TEAM, LOUNGE_TEAM, CONCIERGE_TEAM
    notes: Optional[str] = None

class ShiftCreateRequest(BaseModel):
    staff_user_id: str
    shift_name: str  # MORNING, EVENING, NIGHT
    shift_date: datetime
    start_time: datetime
    end_time: datetime
    airport_code: str

class AirportCreateRequest(BaseModel):
    code: str = Field(..., min_length=3, max_length=3)
    name: str
    city: str
    country: Optional[str] = "IND"
    operating_hours: Optional[str] = "24/7"
    services_config: Optional[Dict[str, Any]] = Field(default_factory=dict)

class AirportPatchRequest(BaseModel):
    name: Optional[str] = None
    city: Optional[str] = None
    country: Optional[str] = None
    is_active: Optional[bool] = None
    operating_hours: Optional[str] = None
    services_config: Optional[Dict[str, Any]] = None


class AirportServiceUpdateRequest(BaseModel):
    price: Optional[float] = Field(None, gt=0, le=50000000)
    currency: Optional[str] = Field(None, min_length=3, max_length=3)
    is_available: Optional[bool] = None
    terminal: Optional[str] = None
    features: Optional[List[str] | str] = None
    short_description: Optional[str] = None
    min_booking_notice_hours: Optional[int] = Field(None, ge=0)


class CouponToggleRequest(BaseModel):
    is_active: Optional[bool] = None
    status: Optional[str] = None


class AdminApiResponse(BaseModel):
    success: bool
    data: Optional[Any] = None
    error: Optional[str] = None
