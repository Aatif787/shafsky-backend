"""
Authentication Router with Refresh Token Rotation, HttpOnly Cookie Security, and Logout Revocation.
"""

import os
import uuid
from fastapi import APIRouter, HTTPException, Depends, Header, Request, Response
from sqlalchemy.orm import Session
from sqlalchemy import select, update
from typing import Optional, List, Dict, Any

from app.database import get_db
from app.models.schema import UserAuth, Profile, RefreshToken, Role
from app.schemas.auth import LoginRequest, RefreshTokenRequest, ApiResponse, AuthDataResponse, UserResponse
from app.services.auth_service import AuthService
from app.security.device_tracking import DeviceTracking
from app.config import settings

router = APIRouter(prefix="/api/auth", tags=["Authentication & Session Security"])


def _set_refresh_cookie(response: Response, refresh_token: str) -> None:
    """Sets HttpOnly, Secure, SameSite=Strict cookie for refresh token."""
    max_age_seconds = int(getattr(settings, "REFRESH_TOKEN_EXPIRE_DAYS", 7)) * 86400
    response.set_cookie(
        key="refreshToken",
        value=refresh_token,
        httponly=True,
        secure=True,
        samesite="strict",
        max_age=max_age_seconds,
        path="/api/auth"
    )
    response.set_cookie(
        key="refresh_token",
        value=refresh_token,
        httponly=True,
        secure=True,
        samesite="strict",
        max_age=max_age_seconds,
        path="/api/auth"
    )


def _clear_refresh_cookie(response: Response) -> None:
    """Clears HttpOnly refresh token cookies."""
    response.delete_cookie(key="refreshToken", path="/api/auth")
    response.delete_cookie(key="refresh_token", path="/api/auth")


@router.post("/login", response_model=ApiResponse)
async def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db)
):
    email = payload.email.lower().strip()
    password = payload.password

    device_info = DeviceTracking.get_client_device(request)

    # 1. Admin Hardcoded Fallback Check
    admin_email = os.getenv("ADMIN_EMAIL", "admin@shafskyaviation.com").lower()
    admin_pass = os.getenv("ADMIN_PASSWORD", "ShafskyAdmin2026!")

    user_data = None
    if email == admin_email and password == admin_pass:
        # Auto-seed admin record in DB if missing to ensure DB session tracking
        user = db.scalar(select(UserAuth).where(UserAuth.email == email))
        if not user:
            user = UserAuth(
                email=email,
                password_hash=AuthService.hash_password(password),
                role=Role.SUPER_ADMIN,
                is_verified=True
            )
            db.add(user)
            db.commit()
            db.refresh(user)

        user_data = {
            "sub": user.email,
            "user_id": str(user.id),
            "role": user.role.value if hasattr(user.role, "value") else str(user.role)
        }
    else:
        user = db.scalar(select(UserAuth).where(UserAuth.email == email))
        if user and AuthService.verify_password(password, user.password_hash):
            user_data = {
                "sub": user.email,
                "user_id": str(user.id),
                "role": user.role.value if hasattr(user.role, "value") else str(user.role)
            }

    if not user_data:
        raise HTTPException(status_code=401, detail="Invalid email or password credentials.")

    access_token = AuthService.create_access_token(user_data)
    raw_refresh = AuthService.create_refresh_token(user_data)

    # Save Hashed Refresh Token in DB with a new Token Family
    token_record = AuthService.register_refresh_token(
        db,
        user_id=user.id,
        raw_token=raw_refresh,
        device_info=device_info
    )

    # Set HttpOnly, Secure, SameSite=Strict security cookie
    _set_refresh_cookie(response, raw_refresh)

    return ApiResponse(
        success=True,
        data=AuthDataResponse(
            accessToken=access_token,
            refreshToken=raw_refresh,
            user=UserResponse(
                id=user_data["user_id"],
                email=user_data["sub"],
                role=user_data["role"],
                fullName=user_data["sub"].split("@")[0].title()
            )
        )
    )


@router.post("/refresh", response_model=ApiResponse)
async def refresh_token(
    request: Request,
    response: Response,
    payload: Optional[RefreshTokenRequest] = None,
    db: Session = Depends(get_db)
):
    device_info = DeviceTracking.get_client_device(request)

    # Extract raw refresh token from payload or HttpOnly cookie
    raw_refresh_token = None
    if payload and payload.refreshToken:
        raw_refresh_token = payload.refreshToken
    elif request.cookies.get("refreshToken"):
        raw_refresh_token = request.cookies.get("refreshToken")
    elif request.cookies.get("refresh_token"):
        raw_refresh_token = request.cookies.get("refresh_token")

    if not raw_refresh_token:
        raise HTTPException(status_code=401, detail="Missing or invalid refresh token.")

    try:
        token_data = AuthService.rotate_refresh_token(db, raw_refresh_token, device_info)
        _set_refresh_cookie(response, token_data["refreshToken"])
        return ApiResponse(
            success=True,
            data=AuthDataResponse(
                accessToken=token_data["accessToken"],
                refreshToken=token_data["refreshToken"]
            )
        )
    except ValueError as ve:
        err_code = str(ve)
        _clear_refresh_cookie(response)
        if err_code == "REPLAY_ATTACK_DETECTED":
            raise HTTPException(status_code=401, detail="Security violation: Token replay attack detected. All sessions revoked.")
        elif err_code == "REFRESH_TOKEN_EXPIRED":
            raise HTTPException(status_code=401, detail="Refresh token has expired. Please log in again.")
        else:
            raise HTTPException(status_code=401, detail="Invalid or revoked refresh token.")
    except Exception:
        _clear_refresh_cookie(response)
        raise HTTPException(status_code=401, detail="Invalid refresh token signature.")


@router.post("/logout", response_model=ApiResponse)
async def logout(
    request: Request,
    response: Response,
    payload: Optional[RefreshTokenRequest] = None,
    db: Session = Depends(get_db)
):
    raw_refresh = None
    if payload and payload.refreshToken:
        raw_refresh = payload.refreshToken
    elif request.cookies.get("refreshToken"):
        raw_refresh = request.cookies.get("refreshToken")
    elif request.cookies.get("refresh_token"):
        raw_refresh = request.cookies.get("refresh_token")

    if raw_refresh:
        AuthService.revoke_refresh_token(db, raw_refresh)

    _clear_refresh_cookie(response)
    return ApiResponse(success=True, data={"message": "Successfully logged out and session revoked."})


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
                    id=decoded.get("user_id", decoded.get("userId", "")),
                    email=decoded.get("sub", decoded.get("email", "")),
                    role=decoded.get("role", "")
                )
            )
        )
    except Exception:
        raise HTTPException(status_code=401, detail="Token expired or invalid.")


@router.get("/device-sessions", response_model=ApiResponse)
async def get_active_device_sessions(
    authorization: Optional[str] = Header(None),
    db: Session = Depends(get_db)
):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing authorization header.")
    token = authorization.split(" ")[1]
    decoded = AuthService.decode_access_token(token)
    user_id_str = decoded.get("user_id")

    try:
        u_uuid = uuid.UUID(user_id_str) if user_id_str else None
    except Exception:
        u_uuid = None

    if not u_uuid:
        return ApiResponse(success=True, data=[])

    records = list(db.scalars(
        select(RefreshToken).where(RefreshToken.user_id == u_uuid, RefreshToken.revoked.is_(False))
    ).all())

    sessions = [
        {
            "deviceId": r.device_id,
            "browser": r.browser,
            "platform": r.platform,
            "ipAddress": r.ip_address,
            "lastActivity": r.last_activity.isoformat() if r.last_activity else r.created_at.isoformat(),
            "createdAt": r.created_at.isoformat()
        }
        for r in records
    ]

    return ApiResponse(success=True, data=sessions)


@router.post("/logout-device/{device_id}", response_model=ApiResponse)
async def logout_device(
    device_id: str,
    authorization: Optional[str] = Header(None),
    db: Session = Depends(get_db)
):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing authorization header.")
    token = authorization.split(" ")[1]
    decoded = AuthService.decode_access_token(token)
    user_id_str = decoded.get("user_id")

    if user_id_str:
        DeviceTracking.revoke_device_session(db, user_id_str, device_id)

    return ApiResponse(success=True, data={"message": f"Device session '{device_id}' revoked."})


@router.post("/logout-all-devices", response_model=ApiResponse)
async def logout_all_devices(
    authorization: Optional[str] = Header(None),
    db: Session = Depends(get_db)
):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing authorization header.")
    token = authorization.split(" ")[1]
    decoded = AuthService.decode_access_token(token)
    user_id_str = decoded.get("user_id")

    if user_id_str:
        DeviceTracking.revoke_all_user_sessions(db, user_id_str)

    return ApiResponse(success=True, data={"message": "All device sessions successfully revoked."})
