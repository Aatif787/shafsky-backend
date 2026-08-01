from typing import Optional, Any
from pydantic import BaseModel, EmailStr, Field

class LoginRequest(BaseModel):
    email: EmailStr
    password: str

class RefreshTokenRequest(BaseModel):
    refreshToken: Optional[str] = None

class UserResponse(BaseModel):
    id: str
    email: str
    role: str
    fullName: Optional[str] = None

class AuthDataResponse(BaseModel):
    accessToken: Optional[str] = None
    refreshToken: Optional[str] = None
    user: Optional[UserResponse] = None

class ProfileUpdateRequest(BaseModel):
    full_name: Optional[str] = None
    phone_number: Optional[str] = None
    avatar_url: Optional[str] = None
    company: Optional[str] = None
    passport_number: Optional[str] = None

class ProfileResponse(BaseModel):
    id: str
    auth_id: str
    email: str
    full_name: Optional[str] = None
    phone_number: Optional[str] = None
    avatar_url: Optional[str] = None
    role: str
    company: Optional[str] = None
    vip_status: bool = False
    vip_tier: str = "REGULAR"
    passport_number: Optional[str] = None

class ChangePasswordRequest(BaseModel):
    current_password: Optional[str] = None
    new_password: str = Field(..., min_length=8)


class ApiResponse(BaseModel):
    success: bool
    data: Optional[Any] = None
    error: Optional[str] = None
