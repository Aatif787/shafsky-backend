from fastapi import Header, HTTPException
from typing import Optional, Dict, Any
from app.services.auth_service import AuthService

ADMIN_ROLES = [
    "SUPER_ADMIN", "ADMIN", "OPERATIONS_MANAGER",
]

FINANCE_REFUND_ROLES = [
    "SUPER_ADMIN", "ADMIN",
]

STAFF_OR_ADMIN_ROLES = [
    "SUPER_ADMIN", "ADMIN", "OPERATIONS_MANAGER", "DUTY_OFFICER",
    "DISPATCHER", "MEET_AND_ASSIST_STAFF", "CONCIERGE_TEAM", "CUSTOMER_SUPPORT"
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

RECYCLE_ADMIN_ROLES = ["SUPER_ADMIN", "ADMIN"]

def get_required_recycle_admin(authorization: Optional[str] = Header(None)) -> Dict[str, Any]:
    """Admin or Super Admin may move bookings to the bin and restore them."""
    user = get_required_admin(authorization)
    if user.get("role") not in RECYCLE_ADMIN_ROLES:
        raise HTTPException(
            status_code=403,
            detail="Access denied. Only Admin or Super Admin can manage the booking recycle bin.",
        )
    return user

def get_required_staff_or_admin(authorization: Optional[str] = Header(None)) -> Dict[str, Any]:
    user = get_required_user(authorization)
    if user.get("role") not in STAFF_OR_ADMIN_ROLES:
        raise HTTPException(status_code=403, detail="Access denied. Staff or administrative privileges required.")
    return user


def get_required_finance_admin(authorization: Optional[str] = Header(None)) -> Dict[str, Any]:
    """Refunds: ADMIN / SUPER_ADMIN only."""
    user = get_required_user(authorization)
    if user.get("role") not in FINANCE_REFUND_ROLES:
        raise HTTPException(
            status_code=403,
            detail="Access denied. Only Admin or Super Admin may issue refunds.",
        )
    return user


def user_owns_booking(user: Optional[Dict[str, Any]], booking) -> bool:
    """True if authenticated user is staff or matches booking owner/email."""
    if not user or not booking:
        return False
    role = user.get("role")
    if role in STAFF_OR_ADMIN_ROLES:
        return True
    user_id = str(user.get("user_id") or user.get("sub") or "").strip()
    email = (user.get("email") or user.get("sub") or "").strip().lower()
    booking_user_id = str(getattr(booking, "user_id", "") or "").strip()
    booking_email = (getattr(booking, "passenger_email", None) or "").strip().lower()
    if user_id and booking_user_id and user_id == booking_user_id:
        return True
    if email and booking_email and email == booking_email:
        return True
    return False


def assert_payment_access(
    *,
    booking,
    payment_token: Optional[str] = None,
    current_user: Optional[Dict[str, Any]] = None,
) -> None:
    """
    Allow payment initiate/retry/create-order when:
    - caller owns the booking (or is staff), OR
    - a valid signed payment_token for this booking_ref is presented.
    """
    from app.security.payment_token import verify_payment_token

    if user_owns_booking(current_user, booking):
        return
    ref = getattr(booking, "booking_ref", None) or ""
    if verify_payment_token(payment_token, booking_ref=ref):
        return
    raise HTTPException(
        status_code=401,
        detail="Payment session required. Provide a valid payment_token or sign in as the booking owner.",
    )

# Aliases and Helpers for Workflow and Endpoint Authorization
get_current_user_auth = get_optional_user

def require_role(allowed_roles: list):
    def role_checker(authorization: Optional[str] = Header(None)) -> Dict[str, Any]:
        user = get_required_user(authorization)
        user_role = user.get("role")
        roles_str = [r.value if hasattr(r, "value") else str(r) for r in allowed_roles]
        if user_role not in roles_str and user_role not in ["ADMIN", "SUPER_ADMIN"]:
            raise HTTPException(status_code=403, detail=f"Access denied. Required roles: {roles_str}")
        return user
    return role_checker
