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

class AdminApiResponse(BaseModel):
    success: bool
    data: Optional[Any] = None
    error: Optional[str] = None
