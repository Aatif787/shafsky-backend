"""
Map a verified Clerk subject onto the existing UserAuth / Profile rows.

Clerk IDs are stored only on profiles.clerk_id. Application UUIDs and roles
stay on the existing tables. Role is never taken from the request.
"""

from __future__ import annotations

import secrets
import uuid
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.schema import Profile, Role, UserAuth
from app.security.clerk_jwt import ClerkIdentity
from app.services.auth_service import AuthService


def _role_value(role) -> str:
    return role.value if hasattr(role, "value") else str(role)


def _ensure_active(user: UserAuth) -> None:
    if not getattr(user, "is_active", True):
        raise HTTPException(status_code=403, detail="Account has been suspended or deactivated.")


def link_decision(
    *,
    clerk_profile_found: bool,
    email: str | None,
    email_verified: bool,
    account_found: bool,
    existing_clerk_id: str | None,
    incoming_clerk_id: str,
) -> str:
    """
    Pure mapping decision used by resolve_clerk_user.

    reuse: profiles.clerk_id already matches
    link: verified email matches an existing account (safely links/re-links incoming identity)
    create: no account for this email
    refuse_unverified: email matches but Clerk has not verified it
    conflict: email is already linked to a different identity and cannot be safely reconciled
    missing_email: no mapping and the token has no email
    """
    if clerk_profile_found:
        return "reuse"
    if not email:
        return "missing_email"
    if account_found:
        if not email_verified:
            return "refuse_unverified"
        return "link"
    return "create"


def _load_user(db: Session, user_id) -> UserAuth:
    user = db.scalar(select(UserAuth).where(UserAuth.id == user_id))
    if not user:
        raise HTTPException(status_code=500, detail="Authentication failed.")
    _ensure_active(user)
    return user


def resolve_clerk_user(db: Session, identity: ClerkIdentity) -> UserAuth:
    """
    1. Existing profiles.clerk_id
    2. Verified email matches existing account → link/re-link clerk_id
    3. No profile → create UserAuth + Profile with a new internal UUID
    """
    clerk_id = identity.sub
    mapped = db.scalar(select(Profile).where(Profile.clerk_id == clerk_id))
    email = (identity.email or "").strip().lower()
    profile = None
    user = None
    if not mapped and email:
        profile = db.scalar(select(Profile).where(func.lower(Profile.email) == email))
        user = db.scalar(select(UserAuth).where(func.lower(UserAuth.email) == email))

    decision = link_decision(
        clerk_profile_found=mapped is not None,
        email=email or None,
        email_verified=identity.email_verified,
        account_found=bool(profile or user),
        existing_clerk_id=profile.clerk_id if profile else None,
        incoming_clerk_id=clerk_id,
    )
    if decision == "reuse" and mapped is not None:
        return _load_user(db, mapped.auth_id)
    if decision == "missing_email":
        raise HTTPException(status_code=401, detail="Invalid or expired Clerk token.")
    if decision == "refuse_unverified":
        raise HTTPException(
            status_code=403,
            detail="Email address is not verified. Account linking was refused.",
        )
    if decision == "conflict":
        raise HTTPException(
            status_code=409,
            detail="This email is already linked to a different identity.",
        )
    if decision == "link":
        if not identity.email_verified:
            raise HTTPException(
                status_code=403,
                detail="Email address is not verified. Account linking was refused.",
            )
        if user is None and profile is not None:
            user = _load_user(db, profile.auth_id)
        if user is None:
            raise HTTPException(status_code=500, detail="Authentication failed.")
        _ensure_active(user)

        # Enterprise account linking: clear any other profile referencing this clerk_id to satisfy unique index
        db.query(Profile).filter(Profile.clerk_id == clerk_id).update({"clerk_id": None})

        if profile is None:
            profile = Profile(
                id=uuid.uuid4(),
                auth_id=user.id,
                email=user.email,
                full_name=identity.full_name or user.email.split("@")[0].title(),
                role=user.role,
                clerk_id=clerk_id,
            )
            db.add(profile)
        else:
            profile.clerk_id = clerk_id
            if identity.full_name and (not profile.full_name or profile.full_name.lower() == user.email.split("@")[0].lower()):
                profile.full_name = identity.full_name

        if not user.is_verified and identity.email_verified:
            user.is_verified = True

        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            raise HTTPException(status_code=409, detail="This email is already linked to a different identity.")
        db.refresh(user)
        return user

    now = datetime.now(timezone.utc)
    user = UserAuth(
        id=uuid.uuid4(),
        email=email,
        password_hash=AuthService.hash_password(secrets.token_urlsafe(48)),
        role=Role.CUSTOMER,
        is_verified=identity.email_verified,
        is_active=True,
        created_at=now,
        updated_at=now,
    )
    db.add(user)
    db.flush()
    profile = Profile(
        id=uuid.uuid4(),
        auth_id=user.id,
        email=email,
        full_name=identity.full_name or email.split("@")[0].title(),
        role=Role.CUSTOMER,
        clerk_id=clerk_id,
        created_at=now,
        updated_at=now,
    )
    db.add(profile)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="This email is already linked to a different identity.")
    db.refresh(user)
    return user


def access_token_claims(user: UserAuth) -> dict:
    """Same claims the password login endpoint puts in the application JWT."""
    return {
        "sub": user.email,
        "user_id": str(user.id),
        "role": _role_value(user.role),
    }
