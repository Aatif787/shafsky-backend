"""Business logic for persistent booking drafts.

Security model
--------------
* A logged-in customer owns drafts by `user_id`.
* A guest owns drafts through a 256-bit token returned once at creation; only its
  SHA-256 is stored. The draft id alone grants nothing.
* Claiming after login re-verifies every token before attaching the draft.

Storage model
-------------
Drafts are intentionally kept out of `bookings`. Early-step data is not a booking,
and deleting a draft must never touch a paid booking.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
import string
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Dict, Iterable, List, Optional, Tuple

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.booking_draft import OPEN_DRAFT_STATUSES, BookingDraft
from app.schemas.booking_draft import BookingDraftResponse, BookingDraftUpsert

_SAFE_KEY = re.compile(r"^[A-Za-z0-9_.:-]{1,80}$")
_SENSITIVE_KEY = re.compile(
    r"(card|cvv|cvc|pan\b|expir|password|secret|token|razorpay|upi\b|bank|ifsc|iban|account[_ -]?number)",
    re.IGNORECASE,
)
_MAX_FORM_BYTES = 64 * 1024


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _expiry_days() -> int:
    raw = (os.getenv("BOOKING_DRAFT_EXPIRY_DAYS") or "30").strip()
    try:
        days = int(raw)
    except ValueError:
        days = 30
    return max(1, min(days, 365))


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _new_token() -> str:
    return secrets.token_urlsafe(32)


def _new_draft_ref() -> str:
    date_stamp = _now().strftime("%Y%m%d")
    suffix = "".join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(6))
    return f"DRF-{date_stamp}-{suffix}"


def _json_safe(value: Any, depth: int = 0) -> Any:
    if depth > 10:
        return None
    if isinstance(value, dict):
        out: Dict[str, Any] = {}
        for key, item in value.items():
            if _SENSITIVE_KEY.search(str(key)):
                continue
            clean_key = str(key)[:100]
            out[clean_key] = _json_safe(item, depth + 1)
        return out
    if isinstance(value, (list, tuple)):
        return [_json_safe(item, depth + 1) for item in list(value)[:200]]
    if isinstance(value, str):
        return value[:5000]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)[:500]


def sanitize_form_data(value: Any) -> Dict[str, Any]:
    """Drop payment credentials and bound the payload before it is stored."""
    cleaned = _json_safe(value if isinstance(value, dict) else {})
    encoded = repr(cleaned)
    if len(encoded.encode("utf-8")) > _MAX_FORM_BYTES:
        raise HTTPException(status_code=413, detail="Draft payload is too large.")
    return cleaned


def _clean_text(value: Optional[str], limit: int) -> Optional[str]:
    if value is None:
        return None
    clean = str(value).strip()
    return clean[:limit] or None


def _clean_key(value: str, field: str) -> str:
    clean = (value or "").strip()
    if not _SAFE_KEY.match(clean):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"{field} contains unsupported characters.",
        )
    return clean


def _clean_resume_url(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    clean = str(value).strip()[:2048]
    # Only same-origin application paths may be resumed. This blocks open redirects.
    if not clean.startswith("/") or clean.startswith("//") or "\\" in clean:
        return None
    if clean.lower().startswith(("/javascript:", "/data:")):
        return None
    return clean


def _status_for(payload: BookingDraftUpsert) -> str:
    requested = (payload.status or "").strip().lower()
    if requested in OPEN_DRAFT_STATUSES:
        return requested
    step_key = (payload.step_key or "").lower()
    if "payment" in step_key or payload.current_step >= 90:
        return "pending_payment"
    if payload.current_step > 1 or payload.completed_steps:
        return "in_progress"
    return "draft"


def _is_expired(draft: BookingDraft, now: Optional[datetime] = None) -> bool:
    if draft.status not in OPEN_DRAFT_STATUSES:
        return False
    expires_at = draft.expires_at
    if not expires_at:
        return False
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    return expires_at < (now or _now())


def _expire_if_needed(db: Session, draft: BookingDraft) -> None:
    if _is_expired(draft):
        draft.status = "expired"
        draft.updated_at = _now()
        db.commit()
        db.refresh(draft)


def _authorised(draft: BookingDraft, user_id: Optional[str], token: Optional[str]) -> bool:
    if user_id and draft.user_id and str(draft.user_id) == str(user_id):
        return True
    if token and draft.access_token_hash:
        return hmac.compare_digest(draft.access_token_hash, _hash_token(token))
    return False


def _load(db: Session, draft_id: uuid.UUID) -> BookingDraft:
    draft = db.scalar(select(BookingDraft).where(BookingDraft.id == draft_id))
    if not draft:
        raise HTTPException(status_code=404, detail="Draft not found.")
    return draft


def _to_response(draft: BookingDraft) -> BookingDraftResponse:
    return BookingDraftResponse(
        draft_id=draft.id,
        draft_ref=draft.draft_ref,
        service_key=draft.service_key,
        service_name=draft.service_name,
        current_step=draft.current_step,
        step_key=draft.step_key,
        completed_steps=list(draft.completed_steps or []),
        form_data=dict(draft.form_data or {}),
        summary=dict(draft.summary or {}),
        resume_url=draft.resume_url,
        status=draft.status,
        booking_ref=draft.booking_ref,
        created_at=draft.created_at,
        updated_at=draft.updated_at,
        last_saved_at=draft.last_saved_at,
        expires_at=draft.expires_at,
    )


def create_or_update(
    db: Session,
    payload: BookingDraftUpsert,
    *,
    user_id: Optional[str] = None,
) -> Tuple[BookingDraft, Optional[str]]:
    """Create a draft, or update one the caller can prove it owns.

    Returns the draft and, on creation only, the one-time access token.
    """
    client_key = _clean_key(payload.client_key, "client_key")
    service_key = _clean_key(payload.service_key, "service_key")
    form_data = sanitize_form_data(payload.form_data)
    summary = sanitize_form_data(payload.summary)
    completed_steps = [str(step)[:80] for step in (payload.completed_steps or [])][:200]
    now = _now()
    expires_at = now + timedelta(days=_expiry_days())

    issued_token: Optional[str] = None

    if payload.draft_id:
        draft = _load(db, payload.draft_id)
        if not _authorised(draft, user_id, payload.draft_token):
            raise HTTPException(status_code=403, detail="Valid draft ownership or draft token is required.")
        if draft.status in ("completed", "cancelled"):
            raise HTTPException(status_code=409, detail="This draft is closed and cannot be updated.")
        if _is_expired(draft):
            # Resuming an expired draft is allowed, but it returns to in_progress and
            # the client must revalidate price/availability before payment.
            draft.status = "in_progress"
    else:
        draft = BookingDraft(
            draft_ref=_new_draft_ref(),
            user_id=uuid.UUID(str(user_id)) if user_id else None,
            guest_id=(payload.guest_id or uuid.uuid4().hex)[:64] if not user_id else None,
            client_key=client_key,
            service_key=service_key,
            service_name=payload.service_name.strip()[:150] or service_key,
            access_token_hash="pending",
            created_at=now,
            updated_at=now,
            last_saved_at=now,
            expires_at=expires_at,
        )
        issued_token = _new_token()
        draft.access_token_hash = _hash_token(issued_token)
        db.add(draft)

    draft.client_key = client_key
    draft.service_key = service_key
    draft.service_name = payload.service_name.strip()[:150] or service_key
    draft.current_step = payload.current_step
    draft.step_key = _clean_text(payload.step_key, 80)
    draft.completed_steps = completed_steps
    draft.form_data = form_data
    draft.summary = summary
    draft.resume_url = _clean_resume_url(payload.resume_url)
    if draft.status not in ("completed", "cancelled"):
        draft.status = _status_for(payload)
    if payload.booking_ref:
        draft.booking_ref = payload.booking_ref.strip()[:40]
    draft.last_saved_at = now
    draft.updated_at = now
    draft.expires_at = expires_at

    db.commit()
    db.refresh(draft)
    return draft, issued_token


def get_draft(
    db: Session,
    draft_id: uuid.UUID,
    *,
    user_id: Optional[str] = None,
    token: Optional[str] = None,
) -> BookingDraft:
    draft = _load(db, draft_id)
    if not _authorised(draft, user_id, token):
        raise HTTPException(status_code=403, detail="Valid draft ownership or draft token is required.")
    _expire_if_needed(db, draft)
    return draft


def list_for_user(
    db: Session,
    user_id: str,
    *,
    include_completed: bool = False,
    include_expired: bool = True,
) -> List[BookingDraft]:
    if not user_id:
        raise HTTPException(status_code=401, detail="Authentication required to list drafts.")
    statuses = list(OPEN_DRAFT_STATUSES)
    if include_expired:
        statuses.append("expired")
    if include_completed:
        statuses.append("completed")
    rows = list(
        db.scalars(
            select(BookingDraft)
            .where(BookingDraft.user_id == uuid.UUID(str(user_id)))
            .where(BookingDraft.status.in_(statuses))
            .order_by(BookingDraft.updated_at.desc())
            .limit(100)
        ).all()
    )
    for row in rows:
        _expire_if_needed(db, row)
    return rows


def lookup_drafts(db: Session, credentials: Iterable[Any]) -> List[BookingDraft]:
    """Return only drafts whose guest token verifies."""
    found: List[BookingDraft] = []
    seen: set[str] = set()
    for credential in list(credentials)[:100]:
        draft_id = getattr(credential, "draft_id", None)
        token = getattr(credential, "draft_token", None)
        if not draft_id or not token:
            continue
        draft = db.scalar(select(BookingDraft).where(BookingDraft.id == draft_id))
        if not draft or not hmac.compare_digest(draft.access_token_hash, _hash_token(token)):
            continue
        if str(draft.id) in seen:
            continue
        seen.add(str(draft.id))
        _expire_if_needed(db, draft)
        found.append(draft)
    return found


def claim_drafts(db: Session, user_id: str, credentials: Iterable[Any]) -> List[BookingDraft]:
    """Attach verified guest drafts to the logged-in account."""
    claimed: List[BookingDraft] = []
    target_user = uuid.UUID(str(user_id))
    for credential in list(credentials)[:100]:
        draft_id = getattr(credential, "draft_id", None)
        token = getattr(credential, "draft_token", None)
        if not draft_id or not token:
            continue
        draft = db.scalar(select(BookingDraft).where(BookingDraft.id == draft_id))
        if not draft or not hmac.compare_digest(draft.access_token_hash, _hash_token(token)):
            continue
        if draft.user_id and draft.user_id != target_user:
            continue
        if draft.status in ("completed", "cancelled"):
            continue
        draft.user_id = target_user
        draft.updated_at = _now()
        claimed.append(draft)
    if claimed:
        db.commit()
        for draft in claimed:
            db.refresh(draft)
    return claimed


def cancel_draft(
    db: Session,
    draft_id: uuid.UUID,
    *,
    user_id: Optional[str] = None,
    token: Optional[str] = None,
) -> BookingDraft:
    draft = _load(db, draft_id)
    if not _authorised(draft, user_id, token):
        raise HTTPException(status_code=403, detail="Valid draft ownership or draft token is required.")
    if draft.status == "completed":
        raise HTTPException(status_code=409, detail="Completed bookings cannot be removed as drafts.")
    draft.status = "cancelled"
    draft.updated_at = _now()
    db.commit()
    db.refresh(draft)
    return draft


def complete_draft(
    db: Session,
    draft_id: uuid.UUID,
    *,
    user_id: Optional[str] = None,
    token: Optional[str] = None,
    booking_ref: Optional[str] = None,
) -> BookingDraft:
    draft = _load(db, draft_id)
    if not _authorised(draft, user_id, token):
        raise HTTPException(status_code=403, detail="Valid draft ownership or draft token is required.")
    draft.status = "completed"
    if booking_ref:
        draft.booking_ref = booking_ref.strip()[:40]
    draft.updated_at = _now()
    db.commit()
    db.refresh(draft)
    return draft
