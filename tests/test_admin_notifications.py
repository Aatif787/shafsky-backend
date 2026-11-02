"""
Enterprise Admin Real-Time Notification System Tests.

Covers:
- Idempotent database persistence and deduplication on (booking_ref, event_type).
- Connection manager subscription, queue broadcasting, and disconnect handling.
- Strict authorization: 401 unauthenticated, 403 non-admin / customer, 200 authorized admin.
- Mark as read and mark all as read API operations.
- Safe integration hook with booking lifecycle (zero regression, failure isolation).
"""

from __future__ import annotations

import asyncio
import uuid
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import get_db
from app.models.admin_notification import AdminNotification
from app.routers import admin_notification_router
from app.services.admin_notification_service import (
    AdminNotificationService,
    AdminNotificationConnectionManager,
)
from app.services.auth_service import AuthService
from app.services.notification_service import NotificationService


@pytest.fixture()
def test_db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    AdminNotification.__table__.create(engine)
    factory = sessionmaker(bind=engine)
    session = factory()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture()
def client(test_db):
    app = FastAPI()
    app.include_router(admin_notification_router.router)

    def _get_test_db():
        return test_db

    app.dependency_overrides[get_db] = _get_test_db
    with TestClient(app) as test_client:
        yield test_client


def _generate_token(role: str = "ADMIN", email: str = "ops@shafskyaviation.com") -> str:
    user_id = str(uuid.uuid4())
    return AuthService.create_access_token({
        "sub": user_id,
        "user_id": user_id,
        "email": email,
        "role": role,
    })


def test_notification_deduplication(test_db):
    """Verify that multiple events for the same booking ref & event_type produce exactly 1 notification."""
    test_ref = f"BK-TEST-DEDUP-{uuid.uuid4().hex[:8]}"
    data = {
        "booking_ref": test_ref,
        "service_category": "Airport Meet & Greet",
        "service_type": "Elite VIP Arrival",
        "customer_name": "Test Passenger",
        "status": "CONFIRMED",
        "event_type": "BOOKING_CREATED",
        "title": f"New Booking: {test_ref}",
        "message": f"Test Passenger booked Elite VIP Arrival ({test_ref})",
    }

    # First call: creates record
    notif1 = AdminNotificationService.create_or_get_notification(test_db, data)
    assert notif1 is not None
    assert notif1.booking_ref == test_ref

    # Second call: returns existing record, does not create duplicate
    notif2 = AdminNotificationService.create_or_get_notification(test_db, data)
    assert notif2 is not None
    assert str(notif1.id) == str(notif2.id)

    # Verify only 1 row in database
    total = test_db.query(AdminNotification).filter(AdminNotification.booking_ref == test_ref).count()
    assert total == 1


def test_connection_manager_broadcast():
    """Verify that connection manager delivers notifications to active subscribers."""
    mgr = AdminNotificationConnectionManager()
    queue = mgr.subscribe()
    assert mgr.active_count == 1

    payload = {"booking_ref": "BK-TEST-BROADCAST", "title": "Test Broadcast"}
    mgr.broadcast_local(payload)

    # Queue should receive the item without blocking
    item = queue.get_nowait()
    assert item["booking_ref"] == "BK-TEST-BROADCAST"

    mgr.unsubscribe(queue)
    assert mgr.active_count == 0


def test_admin_api_authorization(client):
    """Verify endpoint authorization: 401 for missing token, 403 for non-admin, 200 for admin."""
    # 1. Unauthenticated -> 401
    res_no_auth = client.get("/api/admin/notifications")
    assert res_no_auth.status_code == 401

    # 2. Customer role -> 403
    customer_token = _generate_token(role="CUSTOMER", email="customer@example.com")
    res_cust = client.get(
        "/api/admin/notifications",
        headers={"Authorization": f"Bearer {customer_token}"},
    )
    assert res_cust.status_code == 403

    # 3. Admin role -> 200
    admin_token = _generate_token(role="ADMIN", email="admin@shafskyaviation.com")
    res_admin = client.get(
        "/api/admin/notifications",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert res_admin.status_code == 200
    body = res_admin.json()
    assert body["success"] is True
    assert "notifications" in body
    assert "unread_count" in body


def test_admin_mark_as_read_lifecycle(client, test_db):
    """Verify marking single notification and mark all as read."""
    test_ref_1 = f"BK-TEST-READ1-{uuid.uuid4().hex[:8]}"
    test_ref_2 = f"BK-TEST-READ2-{uuid.uuid4().hex[:8]}"
    admin_token = _generate_token(role="ADMIN")

    n1 = AdminNotificationService.create_or_get_notification(test_db, {
        "booking_ref": test_ref_1,
        "service_category": "Special Services",
        "service_type": "Passport and Visa",
        "customer_name": "Read Test 1",
        "status": "PENDING",
        "event_type": "BOOKING_CREATED",
        "title": "Title 1",
        "message": "Message 1",
    })
    n2 = AdminNotificationService.create_or_get_notification(test_db, {
        "booking_ref": test_ref_2,
        "service_category": "Lounge",
        "service_type": "Domestic Lounge",
        "customer_name": "Read Test 2",
        "status": "PENDING",
        "event_type": "BOOKING_CREATED",
        "title": "Title 2",
        "message": "Message 2",
    })

    # Mark first notification as read
    read_res = client.post(
        f"/api/admin/notifications/{n1.id}/read",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert read_res.status_code == 200
    assert read_res.json()["notification"]["is_read"] is True

    test_db.refresh(n1)
    test_db.refresh(n2)
    assert n1.is_read is True
    assert n2.is_read is False

    # Mark all as read
    read_all_res = client.post(
        "/api/admin/notifications/read-all",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert read_all_res.status_code == 200
    assert read_all_res.json()["marked_count"] >= 1

    test_db.refresh(n2)
    assert n2.is_read is True


def test_booking_hook_integration(test_db):
    """Verify that calling NotificationService.notify_booking_created produces a persistent admin notification."""
    test_ref = f"BK-INTEG-{uuid.uuid4().hex[:8]}"
    context = {
        "booking_ref": test_ref,
        "service_category": "Special Services",
        "service_type": "VIP Shopping / PSO",
        "passenger_name": "Integration User",
        "passenger_phone": "+919876543210",
        "origin_code": "DEL",
        "status": "PENDING",
    }

    result = NotificationService.notify_booking_created(test_db, context)
    assert result["booking_ref"] == test_ref

    notif = test_db.query(AdminNotification).filter(AdminNotification.booking_ref == test_ref).first()
    assert notif is not None
    assert notif.customer_name == "Integration User"
    assert notif.service_type == "VIP Shopping / PSO"
    assert notif.is_read is False
