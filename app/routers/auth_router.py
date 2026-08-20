"""
Authentication Router with Refresh Token Rotation, HttpOnly Cookie Security, and Logout Revocation.
"""

import os
import uuid
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException, Depends, Header, Request, Response
from sqlalchemy.orm import Session
from sqlalchemy import select, update
from typing import Optional, List, Dict, Any

from app.database import get_db
from app.models.schema import UserAuth, Profile, RefreshToken, Role
from app.schemas.auth import (
    LoginRequest,
    RefreshTokenRequest,
    ApiResponse,
    AuthDataResponse,
    UserResponse,
    ProfileUpdateRequest,
    ProfileResponse,
)
from app.services.auth_service import AuthService
from app.security.device_tracking import DeviceTracking
from app.config import settings

router = APIRouter(prefix="/api/auth", tags=["Authentication & Session Security"])


def _set_refresh_cookie(response: Response, refresh_token: str) -> None:
    """Sets HttpOnly refresh token cookies. Secure flag is required in production."""
    max_age_seconds = int(getattr(settings, "REFRESH_TOKEN_EXPIRE_DAYS", 7)) * 86400
    cookie_kwargs = dict(
        httponly=True,
        secure=settings.is_production,
        samesite="strict" if settings.is_production else "lax",
        max_age=max_age_seconds,
        path="/api/auth",
    )
    response.set_cookie(key="refreshToken", value=refresh_token, **cookie_kwargs)
    response.set_cookie(key="refresh_token", value=refresh_token, **cookie_kwargs)


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

    admin_email = (os.getenv("ADMIN_EMAIL") or "").lower().strip()
    admin_pass = os.getenv("ADMIN_PASSWORD") or ""

    user_data = None
    if admin_email and admin_pass and email == admin_email and password == admin_pass:
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

    # Extract refresh token from HttpOnly cookie only (never from JSON body).
    raw_refresh_token = request.cookies.get("refreshToken") or request.cookies.get("refresh_token")

    if not raw_refresh_token:
        raise HTTPException(status_code=401, detail="Missing or invalid refresh token.")

    try:
        token_data = AuthService.rotate_refresh_token(db, raw_refresh_token, device_info)
        _set_refresh_cookie(response, token_data["refreshToken"])
        return ApiResponse(
            success=True,
            data=AuthDataResponse(
                accessToken=token_data["accessToken"],
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
    raw_refresh = request.cookies.get("refreshToken") or request.cookies.get("refresh_token")

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


@router.get("/profile", response_model=ApiResponse)
async def get_user_profile(
    authorization: Optional[str] = Header(None),
    db: Session = Depends(get_db)
):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing authorization header.")

    token = authorization.split(" ")[1]
    decoded = AuthService.decode_access_token(token)
    user_id_str = decoded.get("user_id")
    email = decoded.get("sub", "")

    try:
        u_uuid = uuid.UUID(user_id_str) if user_id_str else None
    except Exception:
        u_uuid = None

    profile = None
    if u_uuid:
        profile = db.scalar(select(Profile).where(Profile.auth_id == u_uuid))
    if not profile and email:
        profile = db.scalar(select(Profile).where(Profile.email == email.lower()))

    if not profile:
        # Fallback profile response constructed from user claims
        return ApiResponse(
            success=True,
            data={
                "id": user_id_str or str(uuid.uuid4()),
                "auth_id": user_id_str or "",
                "email": email,
                "full_name": email.split("@")[0].title() if email else "User",
                "role": decoded.get("role", "CUSTOMER"),
                "phone_number": None,
                "avatar_url": None,
                "company": None,
                "vip_status": False,
                "vip_tier": "REGULAR",
                "passport_number": None,
            }
        )

    return ApiResponse(
        success=True,
        data={
            "id": str(profile.id),
            "auth_id": str(profile.auth_id),
            "email": profile.email,
            "full_name": profile.full_name or profile.email.split("@")[0].title(),
            "phone_number": profile.phone_number,
            "avatar_url": profile.avatar_url,
            "role": profile.role.value if hasattr(profile.role, "value") else str(profile.role),
            "company": profile.company,
            "vip_status": profile.vip_status,
            "vip_tier": profile.vip_tier.value if hasattr(profile.vip_tier, "value") else str(profile.vip_tier),
            "passport_number": profile.passport_number,
        }
    )


@router.patch("/profile", response_model=ApiResponse)
async def update_user_profile(
    payload: ProfileUpdateRequest,
    authorization: Optional[str] = Header(None),
    db: Session = Depends(get_db)
):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing authorization header.")

    token = authorization.split(" ")[1]
    decoded = AuthService.decode_access_token(token)
    user_id_str = decoded.get("user_id")
    email = decoded.get("sub", "").lower()

    try:
        u_uuid = uuid.UUID(user_id_str) if user_id_str else None
    except Exception:
        u_uuid = None

    user = None
    if u_uuid:
        user = db.scalar(select(UserAuth).where(UserAuth.id == u_uuid))
    if not user and email:
        user = db.scalar(select(UserAuth).where(UserAuth.email == email))

        if not user and email:
            raise HTTPException(status_code=404, detail="User account not found.")

    profile = None
    if u_uuid:
        profile = db.scalar(select(Profile).where(Profile.auth_id == u_uuid))
    if not profile and email:
        profile = db.scalar(select(Profile).where(Profile.email == email))

    if not profile and user:
        # Create profile record if missing
        profile = Profile(
            auth_id=user.id,
            email=user.email,
            full_name=payload.full_name or user.email.split("@")[0].title(),
            phone_number=payload.phone_number,
            avatar_url=payload.avatar_url,
            company=payload.company,
            passport_number=payload.passport_number,
            role=user.role,
        )
        db.add(profile)
    else:
        if payload.full_name is not None:
            profile.full_name = payload.full_name
        if payload.phone_number is not None:
            profile.phone_number = payload.phone_number
        if payload.avatar_url is not None:
            profile.avatar_url = payload.avatar_url
        if payload.company is not None:
            profile.company = payload.company
        if payload.passport_number is not None:
            profile.passport_number = payload.passport_number

    db.commit()
    db.refresh(profile)

    return ApiResponse(
        success=True,
        data={
            "id": str(profile.id),
            "auth_id": str(profile.auth_id),
            "email": profile.email,
            "full_name": profile.full_name,
            "phone_number": profile.phone_number,
            "avatar_url": profile.avatar_url,
            "role": profile.role.value if hasattr(profile.role, "value") else str(profile.role),
            "company": profile.company,
            "vip_status": profile.vip_status,
            "vip_tier": profile.vip_tier.value if hasattr(profile.vip_tier, "value") else str(profile.vip_tier),
            "passport_number": profile.passport_number,
        }
    )


from app.schemas.auth import ChangePasswordRequest

@router.post("/change-password", response_model=ApiResponse)
async def change_password(
    payload: ChangePasswordRequest,
    authorization: Optional[str] = Header(None),
    db: Session = Depends(get_db)
):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing authorization header.")

    token = authorization.split(" ")[1]
    decoded = AuthService.decode_access_token(token)
    user_id_str = decoded.get("user_id")
    email = decoded.get("sub", "").lower()

    try:
        u_uuid = uuid.UUID(user_id_str) if user_id_str else None
    except Exception:
        u_uuid = None

    user = None
    if u_uuid:
        user = db.scalar(select(UserAuth).where(UserAuth.id == u_uuid))
    if not user and email:
        user = db.scalar(select(UserAuth).where(UserAuth.email == email))

    if not user:
        raise HTTPException(status_code=404, detail="User account not found.")

    user.password_hash = AuthService.hash_password(payload.new_password)
    user.updated_at = datetime.now(timezone.utc)
    db.commit()

    return ApiResponse(success=True, data={"message": "Password updated successfully."})
