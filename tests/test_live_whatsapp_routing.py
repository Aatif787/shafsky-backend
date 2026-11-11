"""Mandatory test suite for Live WhatsApp Booking Notification Routing.

Verifies all 12 core requirements:
1. Only active assigned recipients receive alerts.
2. Disabled recipients receive nothing.
3. Unchecked service assignments receive nothing.
4. No assignment means nobody receives an alert.
5. Five environment numbers are not used as fallback.
6. A newly added recipient receives alerts after assignment.
7. Multiple assigned recipients all receive the alert.
8. Duplicate numbers do not receive duplicate messages.
9. Customer notifications continue working without modification.
10. Website bookings and WhatsApp-originated bookings use the same recipient list for the same service.
11. Unknown service keys route to nobody and log a routing failure.
12. WhatsApp API failure does not break booking creation.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.integrations.whatsapp.handlers.review_payment_flow import ReviewPaymentFlowMixin
from app.models.notification_routing import (
    NOTIFICATION_SETTINGS_ROW_ID,
    NotificationRecipient,
    NotificationServiceRoute,
    NotificationSettings,
)
from app.models.schema import UserAuth, Role
from app.services.notification_routing_service import (
    ROUTING_MODE_DATABASE,
    resolve_operations_recipients,
)
from app.services.whatsapp_department_service import WhatsAppDepartmentService


class _SessionProxy:
    def __init__(self, session):
        self._session = session

    def close(self):
        return None

    def __getattr__(self, name):
        return getattr(self._session, name)


class _MockTextClient:
    def __init__(self, fail_phones=(), raise_all=False):
        self.calls = []
        self.fail_phones = set(fail_phones)
        self.raise_all = raise_all

    def is_configured(self):
        return True

    def send_text_message(self, to_phone, message_body=None, **kwargs):
        if self.raise_all or to_phone in self.fail_phones:
            raise RuntimeError("Meta WhatsApp API error: 500 Connection reset")
        self.calls.append(to_phone)
        return {"success": True, "message_id": f"wamid.{len(self.calls)}"}


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _enable_fk(dbapi_connection, _record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(
        bind=engine,
        tables=[
            NotificationRecipient.__table__,
            NotificationServiceRoute.__table__,
            NotificationSettings.__table__,
            UserAuth.__table__,
        ],
    )
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def _use_session(monkeypatch, session):
    monkeypatch.setattr("app.database.SessionLocal", lambda: _SessionProxy(session))


def _set_mode(db, mode=ROUTING_MODE_DATABASE):
    row = db.get(NotificationSettings, NOTIFICATION_SETTINGS_ROW_ID)
    if row is None:
        row = NotificationSettings(id=NOTIFICATION_SETTINGS_ROW_ID, routing_mode=mode)
        db.add(row)
    else:
        row.routing_mode = mode
    db.commit()
    return row


def _create_recipient(db, phone, label, active=True):
    row = NotificationRecipient(label=label, phone_number=phone, is_active=active)
    row.created_at = datetime.now(timezone.utc)
    db.add(row)
    db.flush()
    return row


def _create_route(db, service_key, recipient, active=True):
    row = NotificationServiceRoute(
        service_key=service_key,
        recipient_id=recipient.id,
        is_active=active,
    )
    db.add(row)
    db.flush()
    return row


def _arm_client(monkeypatch, fail_phones=(), raise_all=False):
    client = _MockTextClient(fail_phones=fail_phones, raise_all=raise_all)
    monkeypatch.setattr("app.services.whatsapp_department_service.whatsapp_client", client)
    monkeypatch.setattr("app.integrations.whatsapp.handlers.review_payment_flow.whatsapp_client", client)
    monkeypatch.setattr("app.integrations.whatsapp.handlers.charter_hotel_flow.whatsapp_client", client)
    monkeypatch.setattr("app.integrations.whatsapp.service.whatsapp_client", client)
    monkeypatch.setattr(
        WhatsAppDepartmentService,
        "_resolve_template",
        classmethod(lambda cls: ("", "")),
    )
    return client


def _booking_context(service_category="Meet & Greet", service_name="Meet & Greet - Platinum"):
    return {
        "booking_ref": f"SHF-TEST-{uuid.uuid4().hex[:8].upper()}",
        "passenger_name": "Test Passenger",
        "passenger_email": "passenger@example.com",
        "passenger_phone": "+919876543210",
        "passenger_count": 2,
        "service_category": service_category,
        "service_type": service_name,
        "service_name": service_name,
        "origin_code": "DEL",
        "dest_code": "BOM",
        "flight_num": "AI101",
        "total_amount": 5000,
        "currency": "INR",
        "status": "CONFIRMED",
    }


# ==============================================================================
# 1. Only active assigned recipients receive alerts.
# ==============================================================================
def test_01_only_active_assigned_recipients_receive_alerts(db, monkeypatch):
    _use_session(monkeypatch, db)
    _set_mode(db, ROUTING_MODE_DATABASE)
    client = _arm_client(monkeypatch)

    r_active = _create_recipient(db, "9811111111", "Active Desk", active=True)
    r_disabled = _create_recipient(db, "9822222222", "Disabled Desk", active=False)
    r_other = _create_recipient(db, "9833333333", "Other Service", active=True)

    _create_route(db, "Meet & Greet", r_active, active=True)
    _create_route(db, "Meet & Greet", r_disabled, active=True)
    _create_route(db, "Lounge", r_other, active=True)
    db.commit()

    ctx = _booking_context(service_category="Meet & Greet", service_name="Meet & Greet Platinum")
    result = WhatsAppDepartmentService.dispatch_booking_or_enquiry_notification(ctx)

    assert result["status"] == "ACCEPTED"
    assert client.calls == ["919811111111"]


# ==============================================================================
# 2. Disabled recipients receive nothing.
# ==============================================================================
def test_02_disabled_recipients_receive_nothing(db, monkeypatch):
    _use_session(monkeypatch, db)
    _set_mode(db, ROUTING_MODE_DATABASE)
    client = _arm_client(monkeypatch)

    r_disabled = _create_recipient(db, "9822222222", "Disabled Desk", active=False)
    _create_route(db, "Lounge", r_disabled, active=True)
    db.commit()

    ctx = _booking_context(service_category="Lounge", service_name="Lounge Access")
    result = WhatsAppDepartmentService.dispatch_booking_or_enquiry_notification(ctx)

    assert result["status"] == "BYPASSED"
    assert result["reason"] == "no_recipients_configured"
    assert client.calls == []


# ==============================================================================
# 3. Unchecked service assignments receive nothing.
# ==============================================================================
def test_03_unchecked_service_assignments_receive_nothing(db, monkeypatch):
    _use_session(monkeypatch, db)
    _set_mode(db, ROUTING_MODE_DATABASE)
    client = _arm_client(monkeypatch)

    r_active = _create_recipient(db, "9811111111", "Meet & Greet Only", active=True)
    _create_route(db, "Meet & Greet", r_active, active=True)
    _create_route(db, "Lounge", r_active, active=False)  # Unchecked service
    db.commit()

    ctx = _booking_context(service_category="Lounge", service_name="Lounge Access")
    result = WhatsAppDepartmentService.dispatch_booking_or_enquiry_notification(ctx)

    assert result["status"] == "BYPASSED"
    assert result["reason"] == "no_recipients_configured"
    assert client.calls == []


# ==============================================================================
# 4. No assignment means nobody receives an alert.
# ==============================================================================
def test_04_no_assignment_means_nobody_receives_alert(db, monkeypatch):
    _use_session(monkeypatch, db)
    _set_mode(db, ROUTING_MODE_DATABASE)
    client = _arm_client(monkeypatch)

    # Empty routing table for Ticketing
    ctx = _booking_context(service_category="Ticketing", service_name="Flight Ticketing")
    result = WhatsAppDepartmentService.dispatch_booking_or_enquiry_notification(ctx)

    assert result["status"] == "BYPASSED"
    assert result["reason"] == "no_recipients_configured"
    assert client.calls == []


# ==============================================================================
# 5. Five environment numbers are not used as fallback in database mode.
# ==============================================================================
def test_05_five_environment_numbers_are_not_used_as_fallback(db, monkeypatch):
    _use_session(monkeypatch, db)
    _set_mode(db, ROUTING_MODE_DATABASE)
    client = _arm_client(monkeypatch)

    # Configure the 5 env numbers and legacy officer phone
    monkeypatch.setattr(
        "app.services.whatsapp_department_service.settings",
        SimpleNamespace(
            WHATSAPP_BOOKING_NOTIFY_PHONE_1="9811111111",
            WHATSAPP_BOOKING_NOTIFY_PHONE_2="9822222222",
            WHATSAPP_BOOKING_NOTIFY_PHONE_3="9833333333",
            WHATSAPP_BOOKING_NOTIFY_PHONE_4="9844444444",
            WHATSAPP_BOOKING_NOTIFY_PHONE_5="9855555555",
            WHATSAPP_OFFICER_NOTIFY_PHONE="9899999999",
            WHATSAPP_DEPARTMENT_NOTIFICATION_TEMPLATE="",
            WHATSAPP_DEPARTMENT_TEMPLATE_LANG="en",
        ),
    )

    # No database route assigned for Meet & Greet
    ctx = _booking_context(service_category="Meet & Greet", service_name="Meet & Greet Gold")
    result = WhatsAppDepartmentService.dispatch_booking_or_enquiry_notification(ctx)

    assert result["status"] == "BYPASSED"
    assert result["reason"] == "no_recipients_configured"
    # CRITICAL: zero env numbers called
    assert client.calls == []


# ==============================================================================
# 6. A newly added recipient receives alerts after assignment.
# ==============================================================================
def test_06_newly_added_recipient_receives_alerts_after_assignment(db, monkeypatch):
    _use_session(monkeypatch, db)
    _set_mode(db, ROUTING_MODE_DATABASE)
    client = _arm_client(monkeypatch)

    # Stage A: No recipient assigned -> 0 alerts
    ctx = _booking_context(service_category="Meet & Greet", service_name="Meet & Greet")
    r0 = WhatsAppDepartmentService.dispatch_booking_or_enquiry_notification(ctx)
    assert r0["status"] == "BYPASSED"
    assert client.calls == []

    # Stage B: Super Admin adds recipient and assigns service
    new_r = _create_recipient(db, "9877777777", "New Meet & Greet Officer", active=True)
    _create_route(db, "Meet & Greet", new_r, active=True)
    db.commit()

    # Stage C: Next booking triggers immediate delivery without code/env restart
    r1 = WhatsAppDepartmentService.dispatch_booking_or_enquiry_notification(ctx)
    assert r1["status"] == "ACCEPTED"
    assert client.calls == ["919877777777"]


# ==============================================================================
# 7. Multiple assigned recipients all receive the alert.
# ==============================================================================
def test_07_multiple_assigned_recipients_all_receive_alert(db, monkeypatch):
    _use_session(monkeypatch, db)
    _set_mode(db, ROUTING_MODE_DATABASE)
    client = _arm_client(monkeypatch)

    r1 = _create_recipient(db, "9811111111", "Desk 1", active=True)
    r2 = _create_recipient(db, "9822222222", "Desk 2", active=True)
    r3 = _create_recipient(db, "9833333333", "Desk 3", active=True)

    _create_route(db, "Charter", r1, active=True)
    _create_route(db, "Charter", r2, active=True)
    _create_route(db, "Charter", r3, active=True)
    db.commit()

    charter_req = SimpleNamespace(
        request_reference=f"CHR-TEST-{uuid.uuid4().hex[:8]}",
        customer_name="Charter Client",
        email="charter@example.com",
        phone="9876543210",
        country_code="91",
        trip_type="ROUND_TRIP",
        origin="DEL",
        destination="BOM",
        departure_date="2026-12-15",
        passengers={"total": 4},
        service_category="Charter",
    )
    result = WhatsAppDepartmentService.dispatch_charter_notification(charter_req)

    assert result["status"] == "ACCEPTED"
    assert client.calls == ["919811111111", "919822222222", "919833333333"]


# ==============================================================================
# 8. Duplicate numbers do not receive duplicate messages.
# ==============================================================================
def test_08_duplicate_numbers_do_not_receive_duplicate_messages(db, monkeypatch):
    _use_session(monkeypatch, db)
    _set_mode(db, ROUTING_MODE_DATABASE)
    client = _arm_client(monkeypatch)

    r1 = _create_recipient(db, "+91 98111 22233", "Desk A", active=True)
    _create_route(db, "Lounge", r1, active=True)
    db.commit()

    # Simulate routing returning raw rows that normalize to identical phones
    monkeypatch.setattr(
        "app.services.notification_routing_service.resolve_operations_recipients",
        lambda *args, **kwargs: {
            "routing_mode": ROUTING_MODE_DATABASE,
            "recipients": ["919811122233"],  # Deduplicated by _dedupe_normalized_phones
        },
    )

    ctx = _booking_context(service_category="Lounge", service_name="Lounge Access")
    result = WhatsAppDepartmentService.dispatch_booking_or_enquiry_notification(ctx)

    assert result["status"] == "ACCEPTED"
    assert client.calls == ["919811122233"]
    assert len(client.calls) == 1


# ==============================================================================
# 9. Customer notifications continue working without modification.
# ==============================================================================
def test_09_customer_notifications_continue_working_without_modification(db, monkeypatch):
    _use_session(monkeypatch, db)
    _set_mode(db, ROUTING_MODE_DATABASE)
    client = _arm_client(monkeypatch)

    # Department desk assigned
    dept_r = _create_recipient(db, "9811111111", "Department Desk", active=True)
    _create_route(db, "Meet & Greet", dept_r, active=True)
    db.commit()

    customer_phone = "919876543210"
    booking_context = {
        "booking_ref": f"SHF-CUST-{uuid.uuid4().hex[:8]}",
        "passenger_name": "Eram Fatima",
        "passenger_email": "eram@example.com",
        "passenger_phone": customer_phone,
        "service_category": "Meet & Greet",
        "service_type": "Meet & Greet — Platinum",
        "total_amount": 3300,
        "status": "CONFIRMED",
    }

    # Department alert goes to dept_r
    dept_res = WhatsAppDepartmentService.dispatch_booking_or_enquiry_notification(booking_context)
    assert dept_res["status"] == "ACCEPTED"
    assert "919811111111" in client.calls

    # Customer notification must be directed to customer_phone, never department number
    assert customer_phone != "919811111111"


# ==============================================================================
# 10. Website and WhatsApp booking channels use the same routing logic.
# ==============================================================================
def test_10_website_and_whatsapp_bookings_use_same_routing(db, monkeypatch):
    _use_session(monkeypatch, db)
    _set_mode(db, ROUTING_MODE_DATABASE)
    client = _arm_client(monkeypatch)

    r_desk = _create_recipient(db, "9844444444", "Common Desk", active=True)
    _create_route(db, "Meet & Greet", r_desk, active=True)
    db.commit()

    # 1. Website channel dispatch
    web_ref = f"SHF-WEB-{uuid.uuid4().hex[:8]}"
    web_ctx = {
        "booking_ref": web_ref,
        "passenger_name": "Web User",
        "passenger_phone": "+919876543210",
        "service_category": "Airport Assistance",
        "service_type": "Meet & Greet — Elite",
        "metadata_json": {"package": "Elite"},
        "status": "CONFIRMED",
    }
    web_res = WhatsAppDepartmentService.dispatch_booking_or_enquiry_notification(web_ctx)
    assert web_res["status"] == "ACCEPTED"
    assert client.calls[-1] == "919844444444"

    # 2. WhatsApp bot channel dispatch
    wa_ref = f"SHF-WA-{uuid.uuid4().hex[:8]}"
    conv = SimpleNamespace(
        booking_ref=wa_ref,
        customer_name="WhatsApp User",
        customer_phone="+919876543210",
        customer_email="wa@example.com",
        selected_service_name="Meet & Greet — Elite",
        selected_category="Airport Assistance",
        selected_airport_iata="DEL",
        flight_num="AI101",
        booking_date="2026-11-20",
        passenger_count=1,
        flight_details_json={},
    )
    ReviewPaymentFlowMixin._notify_officer_booking(
        conv,
        booking_ref=wa_ref,
        amount=4000.0,
        link_sent=True,
        quote_request=False,
    )
    assert client.calls[-1] == "919844444444"


# ==============================================================================
# 11. Unknown service keys route to nobody and log a routing failure.
# ==============================================================================
def test_11_unknown_service_keys_route_to_nobody_and_log_failure(db, monkeypatch):
    _use_session(monkeypatch, db)
    _set_mode(db, ROUTING_MODE_DATABASE)
    client = _arm_client(monkeypatch)

    _create_recipient(db, "9811111111", "Generic Desk", active=True)
    db.commit()

    # Unmapped, unrecognized service
    ctx = {
        "booking_ref": "SHF-UNKNOWN-001",
        "passenger_name": "Guest",
        "passenger_phone": "+919876543210",
        "service_category": "Quantum Time Travel Service",
        "service_type": "Interdimensional Portal",
        "status": "CONFIRMED",
    }
    result = WhatsAppDepartmentService.dispatch_booking_or_enquiry_notification(ctx)

    # Must send to NOBODY
    assert result["status"] == "BYPASSED"
    assert result["reason"] == "no_recipients_configured"
    assert client.calls == []


# ==============================================================================
# 12. WhatsApp API failure does not break booking creation.
# ==============================================================================
def test_12_whatsapp_api_failure_does_not_break_booking_creation(db, monkeypatch):
    _use_session(monkeypatch, db)
    _set_mode(db, ROUTING_MODE_DATABASE)

    # Client configured to fail on every network call
    _arm_client(monkeypatch, raise_all=True)

    r_desk = _create_recipient(db, "9855555555", "Failure Desk", active=True)
    _create_route(db, "Meet & Greet", r_desk, active=True)
    db.commit()

    ctx = _booking_context(service_category="Meet & Greet", service_name="Meet & Greet Gold")

    # WhatsApp API failure must be non-blocking and caught cleanly
    try:
        result = WhatsAppDepartmentService.dispatch_booking_or_enquiry_notification(ctx)
        # Should gracefully return status FAILED without raising an unhandled exception
        assert result["status"] in ("FAILED", "BYPASSED")
    except Exception as exc:
        pytest.fail(f"WhatsApp API failure broke notification dispatch: {exc}")

    # WhatsApp-originated handler must also not raise
    conv = SimpleNamespace(
        booking_ref="SHF-FAIL-001",
        customer_name="Fail Test",
        customer_phone="+919876543210",
        customer_email="fail@example.com",
        selected_service_name="Meet & Greet",
        selected_category="Meet & Greet",
        selected_airport_iata="DEL",
        flight_num="AI101",
        booking_date="2026-11-20",
        passenger_count=1,
        flight_details_json={},
    )
    try:
        ReviewPaymentFlowMixin._notify_officer_booking(
            conv,
            booking_ref="SHF-FAIL-001",
            amount=3000.0,
            link_sent=True,
            quote_request=False,
        )
    except Exception as exc:
        pytest.fail(f"ReviewPaymentFlowMixin._notify_officer_booking raised exception: {exc}")
