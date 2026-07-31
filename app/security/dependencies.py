from fastapi import Header, HTTPException
from typing import Optional, Dict, Any
from app.services.auth_service import AuthService

ADMIN_ROLES = [
    "SUPER_ADMIN", "ADMIN", "OPERATIONS_MANAGER",
    "DUTY_OFFICER", "CONCIERGE_TEAM", "CUSTOMER_SUPPORT", "DISPATCHER"
]

STAFF_OR_ADMIN_ROLES = [
    "SUPER_ADMIN", "ADMIN", "OPERATIONS_MANAGER", "DUTY_OFFICER",
    "MEET_AND_ASSIST_STAFF", "CONCIERGE_TEAM", "CUSTOMER_SUPPORT"
]

def get_optional_user(authorization: Optional[str] = Header(None)) -> Optional[Dict[str, Any]]:
    if not authorization or not authorization.startswith("Bearer "):
        return None
    token = authorization.split(" ")[1]
    try:
        return AuthService.decode_access_token(token)
    except Exception:
        return None

def get_required_user(authorization: Optional[str] = Header(None)) -> Dict[str, Any]:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid authorization header.")
    token = authorization.split(" ")[1]
    try:
        return AuthService.decode_access_token(token)
    except Exception as exc:
        raise HTTPException(status_code=401, detail="Token expired or invalid.") from exc

def get_required_admin(authorization: Optional[str] = Header(None)) -> Dict[str, Any]:
    user = get_required_user(authorization)
    role = user.get("role")
    if role not in ADMIN_ROLES:
        raise HTTPException(status_code=403, detail="Access denied. Insufficient administrative permissions.")
    return user

def get_required_super_admin(authorization: Optional[str] = Header(None)) -> Dict[str, Any]:
    user = get_required_admin(authorization)
    if user.get("role") != "SUPER_ADMIN":
        raise HTTPException(status_code=403, detail="Access denied. Super Admin privileges required.")
    return user

def get_required_staff_or_admin(authorization: Optional[str] = Header(None)) -> Dict[str, Any]:
    user = get_required_user(authorization)
    if user.get("role") not in STAFF_OR_ADMIN_ROLES:
        raise HTTPException(status_code=403, detail="Access denied. Staff or administrative privileges required.")
    return user
