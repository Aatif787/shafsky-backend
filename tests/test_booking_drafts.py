"""Tests for persistent booking drafts: sanitisation, ownership, claim, expiry."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.models.booking_draft import BookingDraft
from app.schemas.booking_draft import BookingDraftUpsert
from app.services import booking_draft_service as service


@pytest.fixture()
def db():
    engine = create_engine("sqlite://")
    BookingDraft.__table__.create(engine)
    factory = sessionmaker(bind=engine)
    session = factory()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def _payload(**overrides):
    data = dict(
        client_key="ticketing",
        service_key="ticketing",
        service_name="Air Ticketing",
        current_step=2,
        step_key="passenger",
        completed_steps=["journey"],
        form_data={
            "journey": {"from": "DEL", "to": "DXB"},
            "passenger": {"firstName": "Aarif"},
            "card_number": "4111111111111111",
            "cvv": "123",
            "razorpay_payment_id": "pay_123",
        },
        summary={"route": "DEL → DXB", "date": "2026-11-12"},
        resume_url="/book?service=ticketing&step=2",
    )
    data.update(overrides)
    return BookingDraftUpsert(**data)


def test_sanitize_drops_payment_credentials_and_keeps_form_data():
    clean = service.sanitize_form_data(
        {
            "passenger": {"firstName": "Aarif", "cvv": "999"},
            "card_number": "4111111111111111",
            "nested": {"razorpay_signature": "x", "email": "a@b.com"},
        }
    )
    assert clean["passenger"]["firstName"] == "Aarif"
    assert "cvv" not in clean["passenger"]
    assert "card_number" not in clean
    assert "razorpay_signature" not in clean["nested"]
    assert clean["nested"]["email"] == "a@b.com"


def test_guest_create_returns_a_token_and_requires_it_to_read(db):
    draft, token = service.create_or_update(db, _payload())
    assert token
    assert draft.status == "in_progress"
    assert draft.form_data["passenger"]["firstName"] == "Aarif"
    assert "card_number" not in draft.form_data
    assert draft.resume_url == "/book?service=ticketing&step=2"

    loaded = service.get_draft(db, draft.id, token=token)
    assert loaded.id == draft.id

    with pytest.raises(HTTPException) as wrong:
        service.get_draft(db, draft.id, token="not-the-token")
    assert wrong.value.status_code == 403

    with pytest.raises(HTTPException) as missing:
        service.get_draft(db, draft.id)
    assert missing.value.status_code == 403


def test_update_reuses_the_draft_and_does_not_issue_a_second_token(db):
    draft, token = service.create_or_update(db, _payload(current_step=1, step_key="journey"))
    updated, new_token = service.create_or_update(
        db,
        _payload(
            draft_id=draft.id,
            draft_token=token,
            current_step=3,
            step_key="payment",
            form_data={"journey": {"from": "DEL", "to": "DXB"}, "passenger": {"firstName": "Aarif"}},
        ),
    )
    assert new_token is None
    assert updated.id == draft.id
    assert updated.current_step == 3
    assert updated.status == "pending_payment"
    assert updated.last_saved_at >= draft.created_at


def test_logged_in_user_lists_and_cancels_own_draft(db):
    user_id = str(uuid.uuid4())
    draft, _ = service.create_or_update(db, _payload(), user_id=user_id)

    rows = service.list_for_user(db, user_id)
    assert [row.id for row in rows] == [draft.id]

    other_user = str(uuid.uuid4())
    assert service.list_for_user(db, other_user) == []

    cancelled = service.cancel_draft(db, draft.id, user_id=user_id)
    assert cancelled.status == "cancelled"
    assert service.list_for_user(db, user_id) == []


def test_claim_attaches_a_guest_draft_to_the_account(db):
    draft, token = service.create_or_update(db, _payload())
    user_id = str(uuid.uuid4())

    claimed = service.claim_drafts(
        db,
        user_id,
        [type("C", (), {"draft_id": draft.id, "draft_token": token})()],
    )
    assert len(claimed) == 1
    assert str(claimed[0].user_id) == user_id
    assert [row.id for row in service.list_for_user(db, user_id)] == [draft.id]


def test_expired_draft_is_reported_as_expired_not_deleted(db):
    draft, token = service.create_or_update(db, _payload())
    draft.expires_at = datetime.now(timezone.utc) - timedelta(days=1)
    db.commit()

    loaded = service.get_draft(db, draft.id, token=token)
    assert loaded.status == "expired"

    # Resume is still possible; the client must revalidate price/availability.
    resumed, _ = service.create_or_update(
        db,
        _payload(draft_id=draft.id, draft_token=token, current_step=2),
    )
    assert resumed.status in ("in_progress", "draft")


def test_complete_removes_draft_from_open_list(db):
    user_id = str(uuid.uuid4())
    draft, _ = service.create_or_update(db, _payload(), user_id=user_id)

    completed = service.complete_draft(
        db, draft.id, user_id=user_id, booking_ref="SHF-20261031-ABCD1234"
    )
    assert completed.status == "completed"
    assert completed.booking_ref == "SHF-20261031-ABCD1234"
    assert service.list_for_user(db, user_id) == []


def test_resume_url_must_be_an_internal_path():
    assert service._clean_resume_url("https://evil.example/steal") is None
    assert service._clean_resume_url("//evil.example") is None
    assert service._clean_resume_url("/book?service=hotel") == "/book?service=hotel"


def test_unknown_status_is_rejected():
    with pytest.raises(Exception):
        _payload(status="paid")
