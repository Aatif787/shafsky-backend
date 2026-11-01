"""HTTP-level tests for the booking-draft router (SQLite, no Postgres needed)."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import get_db
from app.models.booking_draft import BookingDraft
from app.routers import booking_draft_router


@pytest.fixture()
def client():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    BookingDraft.__table__.create(engine)
    factory = sessionmaker(bind=engine)
    session = factory()

    app = FastAPI()
    app.include_router(booking_draft_router.router)

    def _db():
        return session

    app.dependency_overrides[get_db] = _db
    with TestClient(app) as test_client:
        yield test_client
    session.close()
    engine.dispose()


def _body(**overrides):
    payload = {
        "guest_id": "guest-test",
        "client_key": "ticketing",
        "service_key": "ticketing",
        "service_name": "Air Ticketing",
        "current_step": 2,
        "step_key": "passenger",
        "completed_steps": ["journey"],
        "form_data": {"passenger": {"firstName": "Aarif"}, "card_number": "4111111111111111"},
        "summary": {"route": "DEL → DXB"},
        "resume_url": "/book?service=ticketing",
    }
    payload.update(overrides)
    return payload


def test_guest_create_read_update_delete_flow(client):
    created = client.post("/api/booking-drafts", json=_body())
    assert created.status_code == 200
    data = created.json()
    draft_id = data["draft_id"]
    token = data["access_token"]
    assert token
    assert "card_number" not in data["form_data"]

    read = client.get(f"/api/booking-drafts/{draft_id}", headers={"X-Draft-Token": token})
    assert read.status_code == 200
    assert read.json()["service_key"] == "ticketing"

    forbidden = client.get(f"/api/booking-drafts/{draft_id}")
    assert forbidden.status_code == 403

    wrong = client.get(
        f"/api/booking-drafts/{draft_id}", headers={"X-Draft-Token": "wrong-token"}
    )
    assert wrong.status_code == 403

    updated = client.post(
        "/api/booking-drafts",
        json=_body(draft_id=draft_id, draft_token=token, current_step=3, step_key="payment"),
    )
    assert updated.status_code == 200
    assert updated.json()["current_step"] == 3
    assert updated.json()["status"] == "pending_payment"

    deleted = client.delete(
        f"/api/booking-drafts/{draft_id}", headers={"X-Draft-Token": token}
    )
    assert deleted.status_code == 200
    assert deleted.json()["status"] == "cancelled"


def test_lookup_only_returns_verified_credentials(client):
    created = client.post("/api/booking-drafts", json=_body())
    draft_id = created.json()["draft_id"]
    token = created.json()["access_token"]

    found = client.post(
        "/api/booking-drafts/lookup",
        json={"drafts": [{"draft_id": draft_id, "draft_token": token}]},
    )
    assert found.status_code == 200
    assert len(found.json()["drafts"]) == 1

    rejected = client.post(
        "/api/booking-drafts/lookup",
        json={"drafts": [{"draft_id": draft_id, "draft_token": "wrong-token"}]},
    )
    assert rejected.status_code == 200
    assert rejected.json()["drafts"] == []


def test_listing_requires_authentication(client):
    response = client.get("/api/booking-drafts")
    assert response.status_code == 401


def test_resume_url_must_be_internal(client):
    created = client.post(
        "/api/booking-drafts",
        json=_body(resume_url="https://evil.example/steal"),
    )
    assert created.status_code == 200
    assert created.json()["resume_url"] is None
