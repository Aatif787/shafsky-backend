from fastapi import APIRouter, HTTPException, Depends, Header
from typing import Optional
from app.schemas.auth import LoginRequest, RefreshTokenRequest, ApiResponse, AuthDataResponse, UserResponse
from app.services.auth_service import AuthService

router = APIRouter(prefix="/api/auth", tags=["Authentication"])

@router.post("/login", response_model=ApiResponse)
async def login(payload: LoginRequest):
    email = payload.email
    password = payload.password

    # Default admin fallback matching system seed
    if email == "admin@shafskyaviation.com" and password == "ShafskyAdmin2026!":
        user_data = {"userId": "00000000-0000-0000-0000-000000000001", "email": email, "role": "SUPER_ADMIN"}
        access_token = AuthService.create_access_token(user_data)
        refresh_token = AuthService.create_refresh_token(user_data)

        return ApiResponse(
            success=True,
            data=AuthDataResponse(
                accessToken=access_token,
                refreshToken=refresh_token,
                user=UserResponse(
                    id=user_data["userId"],
                    email=email,
                    role=user_data["role"],
                    fullName="Shafsky System Admin"
                )
            )
        )

    raise HTTPException(status_code=401, detail="Invalid email or password credentials.")

@router.post("/refresh", response_model=ApiResponse)
async def refresh_token(payload: RefreshTokenRequest):
    try:
        decoded = AuthService.decode_refresh_token(payload.refreshToken)
        new_access_token = AuthService.create_access_token({
            "userId": decoded.get("userId"),
            "email": decoded.get("email"),
            "role": decoded.get("role")
        })
        return ApiResponse(
            success=True,
            data=AuthDataResponse(accessToken=new_access_token)
        )
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid or expired refresh token.")

@router.get("/me", response_model=ApiResponse)
async def get_me(authorization: Optional[str] = Header(None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid authorization header.")

    token = authorization.split(" ")[1]
    try:
        decoded = AuthService.decode_access_token(token)
        return ApiResponse(
            success=True,
            data=AuthDataResponse(
                user=UserResponse(
                    id=decoded.get("userId", ""),
                    email=decoded.get("email", ""),
                    role=decoded.get("role", "")
                )
            )
        )
    except Exception:
        raise HTTPException(status_code=401, detail="Token expired or invalid.")
