"""
Authentication Router with Refresh Token Rotation, HttpOnly Cookie Security, and Logout Revocation.
"""

import os
import uuid
from datetime import datetime, timezone
from typing import Dict, Any

from fastapi import APIRouter, HTTPException, Depends, Request, Response
from sqlalchemy.orm import Session
from sqlalchemy import select

from app.database import get_db
from app.models.schema import UserAuth, Profile, RefreshToken, Role
from app.schemas.auth import (
    LoginRequest,
    ApiResponse,
    AuthDataResponse,
    UserResponse,
    ProfileUpdateRequest,
    ChangePasswordRequest,
)
from app.services.auth_service import AuthService
from app.security.device_tracking import DeviceTracking
from app.security.dependencies import get_required_user
from app.config import settings

router = APIRouter(prefix="/api/auth", tags=["Authentication & Session Security"])


def _refresh_cookie_kwargs(max_age_seconds: int | None = None) -> dict:
    """Shared cookie attributes so set/clear stay in sync for browsers."""
    samesite = getattr(settings, "COOKIE_SAMESITE", None) or ("strict" if settings.is_production else "lax")
    if samesite not in ("lax", "strict", "none"):
        samesite = "lax"
    # SameSite=None requires Secure; force Secure whenever none is used.
    secure = settings.is_production or (samesite == "none")
    kwargs = dict(
        httponly=True,
        secure=secure,
        samesite=samesite,
        path="/api/auth",
    )
    if max_age_seconds is not None:
        kwargs["max_age"] = max_age_seconds
    return kwargs


def _set_refresh_cookie(response: Response, raw_token: str) -> None:
    """Sets HttpOnly refresh token cookies. Secure flag is required in production."""
    max_age_seconds = int(getattr(settings, "REFRESH_TOKEN_EXPIRE_DAYS", 7)) * 86400
    cookie_kwargs = _refresh_cookie_kwargs(max_age_seconds)
    response.set_cookie(key="refreshToken", value=raw_token, **cookie_kwargs)
    response.set_cookie(key="refresh_token", value=raw_token, **cookie_kwargs)


def _clear_refresh_cookie(response: Response) -> None:
    """Clears HttpOnly refresh token cookies with matching attributes."""
    cookie_kwargs = _refresh_cookie_kwargs()
    response.delete_cookie(key="refreshToken", **cookie_kwargs)
    response.delete_cookie(key="refresh_token", **cookie_kwargs)


def _parse_user_uuid(user_id_str: str | None) -> uuid.UUID | None:
    if not user_id_str:
        return None
    try:
        return uuid.UUID(user_id_str)
    except Exception:
        return None


def _require_user_id(decoded: Dict[str, Any]) -> str:
    user_id_str = decoded.get("user_id") or decoded.get("userId")
    if not user_id_str:
        raise HTTPException(status_code=401, detail="Token missing user identity.")
    return str(user_id_str)


def _profile_payload(profile: Profile) -> dict:
    return {
        "id": str(profile.id),
        "auth_id": str(profile.auth_id),
        "email": profile.email,
        "full_name": profile.full_name or (profile.email.split("@")[0].title() if profile.email else "User"),
        "phone_number": profile.phone_number,
        "avatar_url": profile.avatar_url,
        "role": profile.role.value if hasattr(profile.role, "value") else str(profile.role),
        "company": profile.company,
        "vip_status": profile.vip_status,
        "vip_tier": profile.vip_tier.value if hasattr(profile.vip_tier, "value") else str(profile.vip_tier),
        "passport_number": profile.passport_number,
    }


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

    # C1: Never accept plaintext env ADMIN_PASSWORD as a login backdoor.
    # Admins must authenticate against a hashed UserAuth row in the database.
    # Optional one-time bootstrap: ALLOW_ADMIN_BOOTSTRAP=true (non-production only)
    # when ADMIN_EMAIL + ADMIN_PASSWORD are set and no SUPER_ADMIN exists yet.
    allow_bootstrap = (
        not settings.is_production
        and (os.getenv("ALLOW_ADMIN_BOOTSTRAP") or "").strip().lower() in ("1", "true", "yes")
    )
    if allow_bootstrap:
        admin_email = (os.getenv("ADMIN_EMAIL") or "").lower().strip()
        admin_pass = os.getenv("ADMIN_PASSWORD") or ""
        if admin_email and admin_pass and email == admin_email and password == admin_pass:
            existing_super = db.scalar(
                select(UserAuth).where(UserAuth.role == Role.SUPER_ADMIN).limit(1)
            )
            user = db.scalar(select(UserAuth).where(UserAuth.email == email))
            if not existing_super and not user:
                user = UserAuth(
                    email=email,
                    password_hash=AuthService.hash_password(password),
                    role=Role.SUPER_ADMIN,
                    is_verified=True,
                )
                db.add(user)
                db.commit()
                db.refresh(user)

    user = db.scalar(select(UserAuth).where(UserAuth.email == email))
    user_data = None
    if user and AuthService.verify_password(password, user.password_hash):
        if not getattr(user, "is_active", True):
            raise HTTPException(status_code=403, detail="Account has been suspended or deactivated.")
        user_data = {
            "sub": user.email,
            "user_id": str(user.id),
            "role": user.role.value if hasattr(user.role, "value") else str(user.role),
        }

    if not user_data:
        raise HTTPException(status_code=401, detail="Invalid email or password credentials.")

    access_token = AuthService.create_access_token(user_data)
    raw_refresh = AuthService.create_refresh_token(user_data)

    # Save Hashed Refresh Token in DB with a new Token Family
    AuthService.register_refresh_token(
        db,
        user_id=user.id,
        raw_token=raw_refresh,
        device_info=device_info
    )

    # C2: Refresh token only in HttpOnly cookie — never in JSON body.
    _set_refresh_cookie(response, raw_refresh)

    return ApiResponse(
        success=True,
        data=AuthDataResponse(
            accessToken=access_token,
            refreshToken=None,
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
        elif err_code == "ACCOUNT_INACTIVE":
            raise HTTPException(status_code=403, detail="Account has been suspended or deactivated.")
        else:
            raise HTTPException(status_code=401, detail="Invalid or revoked refresh token.")
    except Exception:
        _clear_refresh_cookie(response)
        raise HTTPException(status_code=401, detail="Invalid refresh token signature.")


@router.post("/logout", response_model=ApiResponse)
async def logout(
    request: Request,
    response: Response,
    db: Session = Depends(get_db)
):
    raw_refresh = request.cookies.get("refreshToken") or request.cookies.get("refresh_token")

    if raw_refresh:
        AuthService.revoke_refresh_token(db, raw_refresh)

    _clear_refresh_cookie(response)
    return ApiResponse(success=True, data={"message": "Successfully logged out and session revoked."})


@router.get("/me", response_model=ApiResponse)
async def get_me(decoded: Dict[str, Any] = Depends(get_required_user)):
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


@router.get("/device-sessions", response_model=ApiResponse)
async def get_active_device_sessions(
    decoded: Dict[str, Any] = Depends(get_required_user),
    db: Session = Depends(get_db)
):
    user_id_str = _require_user_id(decoded)
    u_uuid = _parse_user_uuid(user_id_str)
    if not u_uuid:
        raise HTTPException(status_code=401, detail="Token missing user identity.")

    now = datetime.now(timezone.utc)
    records = list(db.scalars(
        select(RefreshToken).where(
            RefreshToken.user_id == u_uuid,
            RefreshToken.revoked.is_(False),
            RefreshToken.expires_at > now,
        )
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
    decoded: Dict[str, Any] = Depends(get_required_user),
    db: Session = Depends(get_db)
):
    user_id_str = _require_user_id(decoded)
    DeviceTracking.revoke_device_session(db, user_id_str, device_id)
    return ApiResponse(success=True, data={"message": f"Device session '{device_id}' revoked."})


@router.post("/logout-all-devices", response_model=ApiResponse)
async def logout_all_devices(
    decoded: Dict[str, Any] = Depends(get_required_user),
    db: Session = Depends(get_db)
):
    user_id_str = _require_user_id(decoded)
    DeviceTracking.revoke_all_user_sessions(db, user_id_str)
    return ApiResponse(success=True, data={"message": "All device sessions successfully revoked."})


@router.get("/profile", response_model=ApiResponse)
async def get_user_profile(
    decoded: Dict[str, Any] = Depends(get_required_user),
    db: Session = Depends(get_db)
):
    user_id_str = decoded.get("user_id") or decoded.get("userId") or ""
    email = decoded.get("sub", "")
    u_uuid = _parse_user_uuid(user_id_str)

    profile = None
    if u_uuid:
        profile = db.scalar(select(Profile).where(Profile.auth_id == u_uuid))
    if not profile and email:
        profile = db.scalar(select(Profile).where(Profile.email == email.lower()))

    if not profile:
        # Fallback from JWT claims — stable id (auth user id), never a random UUID
        return ApiResponse(
            success=True,
            data={
                "id": user_id_str,
                "auth_id": user_id_str,
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

    return ApiResponse(success=True, data=_profile_payload(profile))


@router.patch("/profile", response_model=ApiResponse)
async def update_user_profile(
    payload: ProfileUpdateRequest,
    decoded: Dict[str, Any] = Depends(get_required_user),
    db: Session = Depends(get_db)
):
    user_id_str = decoded.get("user_id") or decoded.get("userId")
    email = (decoded.get("sub") or "").lower()
    u_uuid = _parse_user_uuid(user_id_str)

    user = None
    if u_uuid:
        user = db.scalar(select(UserAuth).where(UserAuth.id == u_uuid))
    if not user and email:
        user = db.scalar(select(UserAuth).where(UserAuth.email == email))

    if not user:
        raise HTTPException(status_code=404, detail="User account not found.")

    profile = None
    if u_uuid:
        profile = db.scalar(select(Profile).where(Profile.auth_id == u_uuid))
    if not profile and email:
        profile = db.scalar(select(Profile).where(Profile.email == email))

    if not profile:
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

    return ApiResponse(success=True, data=_profile_payload(profile))


@router.post("/change-password", response_model=ApiResponse)
async def change_password(
    payload: ChangePasswordRequest,
    decoded: Dict[str, Any] = Depends(get_required_user),
    db: Session = Depends(get_db)
):
    user_id_str = decoded.get("user_id") or decoded.get("userId")
    email = (decoded.get("sub") or "").lower()
    u_uuid = _parse_user_uuid(user_id_str)

    user = None
    if u_uuid:
        user = db.scalar(select(UserAuth).where(UserAuth.id == u_uuid))
    if not user and email:
        user = db.scalar(select(UserAuth).where(UserAuth.email == email))

    if not user:
        raise HTTPException(status_code=404, detail="User account not found.")

    if not getattr(user, "is_active", True):
        raise HTTPException(status_code=403, detail="Account has been suspended or deactivated.")

    # Strictly verify current password
    if not payload.current_password or not AuthService.verify_password(payload.current_password, user.password_hash):
        raise HTTPException(status_code=400, detail="Current password is incorrect.")

    # Prevent password reuse
    if payload.current_password == payload.new_password or AuthService.verify_password(payload.new_password, user.password_hash):
        raise HTTPException(status_code=400, detail="New password cannot be the same as the current password.")

    user.password_hash = AuthService.hash_password(payload.new_password)
    user.updated_at = datetime.now(timezone.utc)
    db.commit()

    # Revoke all device and refresh token sessions upon password change
    DeviceTracking.revoke_all_user_sessions(db, str(user.id))

    return ApiResponse(success=True, data={"message": "Password updated successfully. All other active sessions have been revoked."})
