"""Persistent booking-draft / cart API.

Guest access uses the one-time token returned at creation in the `X-Draft-Token`
header. Logged-in customers use their normal application bearer token.
"""

from __future__ import annotations

from typing import Any, Dict, Optional
from uuid import UUID

from fastapi import APIRouter, Body, Depends, Header, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas.booking_draft import (
    BookingDraftClaim,
    BookingDraftClaimResponse,
    BookingDraftComplete,
    BookingDraftCreateResponse,
    BookingDraftListResponse,
    BookingDraftLookup,
    BookingDraftResponse,
    BookingDraftUpsert,
)
from app.security.dependencies import get_optional_user, get_required_user
from app.services import booking_draft_service as service

router = APIRouter(prefix="/api/booking-drafts", tags=["Booking Drafts"])


def _user_id(user: Optional[Dict[str, Any]]) -> Optional[str]:
    if not user:
        return None
    raw = user.get("user_id") or user.get("userId") or user.get("sub")
    return str(raw) if raw else None


def _token(header_token: Optional[str], body_token: Optional[str] = None) -> Optional[str]:
    return (header_token or body_token or "").strip() or None


@router.post(
    "",
    response_model=BookingDraftCreateResponse,
    summary="Create or update a booking draft",
)
def upsert_draft_endpoint(
    payload: BookingDraftUpsert,
    db: Session = Depends(get_db),
    current_user: Optional[Dict[str, Any]] = Depends(get_optional_user),
    x_draft_token: Optional[str] = Header(default=None, alias="X-Draft-Token"),
):
    draft, issued_token = service.create_or_update(
        db,
        payload,
        user_id=_user_id(current_user),
    )
    return BookingDraftCreateResponse(
        **service._to_response(draft).model_dump(),
        access_token=issued_token or "",
        guest_id=draft.guest_id,
    )


@router.get(
    "",
    response_model=BookingDraftListResponse,
    summary="List the logged-in customer's incomplete drafts",
)
def list_drafts_endpoint(
    include_completed: bool = Query(default=False),
    include_expired: bool = Query(default=True),
    db: Session = Depends(get_db),
    current_user: Dict[str, Any] = Depends(get_required_user),
):
    rows = service.list_for_user(
        db,
        _user_id(current_user) or "",
        include_completed=include_completed,
        include_expired=include_expired,
    )
    return BookingDraftListResponse(drafts=[service._to_response(row) for row in rows])


@router.post(
    "/lookup",
    response_model=BookingDraftListResponse,
    summary="Look up guest drafts by verified draft token",
)
def lookup_drafts_endpoint(
    payload: BookingDraftLookup,
    db: Session = Depends(get_db),
):
    rows = service.lookup_drafts(db, payload.drafts)
    return BookingDraftListResponse(drafts=[service._to_response(row) for row in rows])


@router.post(
    "/claim",
    response_model=BookingDraftClaimResponse,
    summary="Attach guest drafts to the logged-in account",
)
def claim_drafts_endpoint(
    payload: BookingDraftClaim,
    db: Session = Depends(get_db),
    current_user: Dict[str, Any] = Depends(get_required_user),
):
    rows = service.claim_drafts(db, _user_id(current_user) or "", payload.drafts)
    return BookingDraftClaimResponse(
        claimed=len(rows),
        drafts=[service._to_response(row) for row in rows],
    )


@router.get(
    "/{draft_id}",
    response_model=BookingDraftResponse,
    summary="Read one draft",
)
def get_draft_endpoint(
    draft_id: UUID,
    db: Session = Depends(get_db),
    current_user: Optional[Dict[str, Any]] = Depends(get_optional_user),
    x_draft_token: Optional[str] = Header(default=None, alias="X-Draft-Token"),
):
    draft = service.get_draft(
        db,
        draft_id,
        user_id=_user_id(current_user),
        token=_token(x_draft_token),
    )
    return service._to_response(draft)


@router.delete(
    "/{draft_id}",
    response_model=BookingDraftResponse,
    summary="Remove (cancel) a draft",
)
def delete_draft_endpoint(
    draft_id: UUID,
    db: Session = Depends(get_db),
    current_user: Optional[Dict[str, Any]] = Depends(get_optional_user),
    x_draft_token: Optional[str] = Header(default=None, alias="X-Draft-Token"),
):
    draft = service.cancel_draft(
        db,
        draft_id,
        user_id=_user_id(current_user),
        token=_token(x_draft_token),
    )
    return service._to_response(draft)


@router.post(
    "/{draft_id}/complete",
    response_model=BookingDraftResponse,
    summary="Mark a draft completed after a verified booking",
)
def complete_draft_endpoint(
    draft_id: UUID,
    payload: BookingDraftComplete = Body(default_factory=BookingDraftComplete),
    db: Session = Depends(get_db),
    current_user: Optional[Dict[str, Any]] = Depends(get_optional_user),
    x_draft_token: Optional[str] = Header(default=None, alias="X-Draft-Token"),
):
    draft = service.complete_draft(
        db,
        draft_id,
        user_id=_user_id(current_user),
        token=_token(x_draft_token),
        booking_ref=payload.booking_ref,
    )
    return service._to_response(draft)
