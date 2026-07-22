from typing import Optional
from pydantic import BaseModel, EmailStr

class LoginRequest(BaseModel):
    email: EmailStr
    password: str

class RefreshTokenRequest(BaseModel):
    refreshToken: str

class UserResponse(BaseModel):
    id: str
    email: str
    role: str
    fullName: Optional[str] = None

class AuthDataResponse(BaseModel):
    accessToken: str
    refreshToken: Optional[str] = None
    user: Optional[UserResponse] = None

class ApiResponse(BaseModel):
    success: bool
    data: Optional[AuthDataResponse] = None
    error: Optional[str] = None
