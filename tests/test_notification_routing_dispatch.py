"""Phase 5 tests for operations WhatsApp recipient selection.

Env mode keeps the existing environment phones. Database mode uses only active
recipients on an active route for the exact service key. Customer WhatsApp
senders are not part of that selection.

Behaviour under test: resolve_operations_recipients and the operations dispatch
paths in whatsapp_department_ext, the officer helpers, and the enquiry helpers.
Existing department tests cover env dispatch only. No existing test file covers
database-mode recipient selection.

User instruction: "Implement Phase 5: Connect the existing Shafsky Aviation
WhatsApp booking notification system to database-driven Super Admin routing."
and "Test both routing modes, disabled recipients, empty routes, multiple
recipients, duplicate prevention, charter, officer-only paths and existing
customer notifications."
"""

from __future__ import annotations

import inspect
import socket
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from urllib.parse import urlparse

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.integrations.whatsapp.handlers.charter_hotel_flow import CharterHotelFlowMixin
from app.integrations.whatsapp.handlers.review_payment_flow import ReviewPaymentFlowMixin
from app.integrations.whatsapp.service_ext import trigger_booking_whatsapp_notifications
from app.models.notification_routing import (
    NOTIFICATION_SETTINGS_ROW_ID,
    NotificationRecipient,
    NotificationServiceRoute,
    NotificationSettings,
)
from app.services.notification_customer_confirmation_mixin import (
    NotificationCustomerConfirmationMixin,
)
from app.services.notification_customer_enquiry_mixin import NotificationCustomerEnquiryMixin
from app.services.notification_routing_service import (
    _dedupe_normalized_phones,
    resolve_operations_recipients,
)
from app.services.notification_service import NotificationService
from app.services.whatsapp_department_service import WhatsAppDepartmentService


class _SessionProxy:
    def __init__(self, session):
        self._session = session

    def close(self):
        return None

    def __getattr__(self, name):
        return getattr(self._session, name)


class _TextClient:
    def __init__(self, fail_phones=()):
        self.calls = []
        self.fail_phones = set(fail_phones)

    def is_configured(self):
        return True

    def send_text_message(self, to_phone, message_body=None, **kwargs):
        phone = to_phone
        self.calls.append(phone)
        if phone in self.fail_phones:
            raise RuntimeError("recipient down")
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


def _recipient(db, phone, label, when, active=True):
    row = NotificationRecipient(label=label, phone_number=phone, is_active=active)
    row.created_at = when
    db.add(row)
    db.flush()
    return row


def _route(db, service_key, recipient, active=True):
    row = NotificationServiceRoute(
        service_key=service_key,
        recipient_id=recipient.id,
        is_active=active,
    )
    db.add(row)
    db.flush()
    return row


def _mode(db, mode):
    row = db.get(NotificationSettings, NOTIFICATION_SETTINGS_ROW_ID)
    if row is None:
        row = NotificationSettings(routing_mode=mode)
        db.add(row)
    else:
        row.routing_mode = mode
    db.commit()
    return row


def _booking(service_key, stage=None):
    context = {
        "booking_ref": f"SHF-ROUTE-{uuid.uuid4().hex[:10].upper()}",
        "passenger_name": "Route Check",
        "passenger_email": "route@example.test",
        "passenger_phone": "+919876543210",
        "passenger_count": 1,
        "service_category": service_key,
        "service_type": service_key,
        "service_name": service_key,
        "origin_code": "DEL",
        "dest_code": "DEL",
        "status": "CONFIRMED" if stage == "PAYMENT_CONFIRMED" else "PENDING",
        "total_amount": 1000,
        "currency": "INR",
    }
    if stage:
        context["notification_stage"] = stage
    return context


def _arm_dispatch(monkeypatch, fail_phones=()):
    client = _TextClient(fail_phones=fail_phones)
    monkeypatch.setattr("app.services.whatsapp_department_service.whatsapp_client", client)
    monkeypatch.setattr(
        WhatsAppDepartmentService,
        "_resolve_template",
        classmethod(lambda cls: ("", "")),
    )
    return client


def test_settings_model_has_no_global_enable_flag():
    columns = set(NotificationSettings.__table__.columns.keys())
    assert columns == {"id", "routing_mode", "created_at", "updated_at"}


def test_env_mode_ignores_database_routes(db, monkeypatch):
    monkeypatch.setattr(
        "app.services.whatsapp_department_service.settings",
        SimpleNamespace(
            WHATSAPP_BOOKING_NOTIFY_PHONE_1="9811111111",
            WHATSAPP_BOOKING_NOTIFY_PHONE_2="9822222222",
            WHATSAPP_BOOKING_NOTIFY_PHONE_3="9833333333",
            WHATSAPP_BOOKING_NOTIFY_PHONE_4="9844444444",
            WHATSAPP_BOOKING_NOTIFY_PHONE_5="9855555555",
            WHATSAPP_OFFICER_NOTIFY_PHONE="",
        ),
    )
    when = datetime(2026, 1, 1, tzinfo=timezone.utc)
    lounge = _recipient(db, "9866666666", "Lounge desk", when)
    _route(db, "Lounge", lounge)
    _mode(db, "env")

    decision = resolve_operations_recipients("Lounge", db)

    assert decision["routing_mode"] == "env"
    assert decision["recipients"] == [
        "919811111111",
        "919822222222",
        "919833333333",
        "919844444444",
        "919855555555",
    ]
    assert "919866666666" not in decision["recipients"]
    assert db.get(NotificationSettings, NOTIFICATION_SETTINGS_ROW_ID).routing_mode == "env"


def test_env_mode_keeps_officer_fallback_when_five_phones_are_empty(db, monkeypatch):
    monkeypatch.setattr(
        "app.services.whatsapp_department_service.settings",
        SimpleNamespace(
            WHATSAPP_BOOKING_NOTIFY_PHONE_1="",
            WHATSAPP_BOOKING_NOTIFY_PHONE_2="",
            WHATSAPP_BOOKING_NOTIFY_PHONE_3="",
            WHATSAPP_BOOKING_NOTIFY_PHONE_4="",
            WHATSAPP_BOOKING_NOTIFY_PHONE_5="",
            WHATSAPP_OFFICER_NOTIFY_PHONE="9877777777",
        ),
    )

    decision = resolve_operations_recipients("Lounge", db)

    assert decision == {"routing_mode": "env", "recipients": ["919877777777"]}


def test_missing_settings_row_stays_on_env_recipients(db, monkeypatch):
    monkeypatch.setattr(
        "app.services.whatsapp_department_service.settings",
        SimpleNamespace(
            WHATSAPP_BOOKING_NOTIFY_PHONE_1="9811111111",
            WHATSAPP_BOOKING_NOTIFY_PHONE_2="",
            WHATSAPP_BOOKING_NOTIFY_PHONE_3="",
            WHATSAPP_BOOKING_NOTIFY_PHONE_4="",
            WHATSAPP_BOOKING_NOTIFY_PHONE_5="",
            WHATSAPP_OFFICER_NOTIFY_PHONE="",
        ),
    )

    decision = resolve_operations_recipients("Lounge", db)

    assert decision == {"routing_mode": "env", "recipients": ["919811111111"]}


def test_unreadable_settings_stay_on_env_recipients(monkeypatch):
    monkeypatch.setattr(
        "app.services.whatsapp_department_service.settings",
        SimpleNamespace(
            WHATSAPP_BOOKING_NOTIFY_PHONE_1="9811111111",
            WHATSAPP_BOOKING_NOTIFY_PHONE_2="",
            WHATSAPP_BOOKING_NOTIFY_PHONE_3="",
            WHATSAPP_BOOKING_NOTIFY_PHONE_4="",
            WHATSAPP_BOOKING_NOTIFY_PHONE_5="",
            WHATSAPP_OFFICER_NOTIFY_PHONE="",
        ),
    )

    class Broken:
        def get(self, *_args, **_kwargs):
            raise RuntimeError("notification_settings missing")

    decision = resolve_operations_recipients("Lounge", Broken())

    assert decision == {"routing_mode": "env", "recipients": ["919811111111"]}


def test_database_mode_matches_exact_service_and_active_rows_only(db):
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    first = _recipient(db, "9811111111", "First", start)
    second = _recipient(db, "9822222222", "Second", start + timedelta(seconds=1))
    disabled = _recipient(db, "9833333333", "Disabled", start, active=False)
    hotel = _recipient(db, "9844444444", "Hotel only", start)
    hotels = _recipient(db, "9855555555", "Hotels only", start)
    shared = _recipient(db, "9866666666", "Shared", start + timedelta(seconds=2))
    _route(db, "Lounge", first)
    _route(db, "Lounge", second)
    _route(db, "Lounge", disabled)
    _route(db, "Lounge", shared)
    _route(db, "Hotel", hotel)
    _route(db, "Hotels", hotels)
    _route(db, "Hotel", shared)
    inactive = _recipient(db, "9877777777", "Inactive route", start)
    _route(db, "Lounge", inactive, active=False)
    _mode(db, "database")

    lounge = resolve_operations_recipients("Lounge", db)
    hotel_decision = resolve_operations_recipients("Hotel", db)
    hotels_decision = resolve_operations_recipients("Hotels", db)

    assert lounge == {
        "routing_mode": "database",
        "recipients": ["919811111111", "919822222222", "919866666666"],
    }
    assert hotel_decision["recipients"] == ["919844444444", "919866666666"]
    assert hotels_decision["recipients"] == ["919855555555"]
    assert resolve_operations_recipients("Unknown Service", db)["recipients"] == []
    assert resolve_operations_recipients("  ", db)["recipients"] == []
    assert resolve_operations_recipients(None, db)["recipients"] == []


def test_database_mode_does_not_fall_back_when_route_query_fails():
    class RoutesDown:
        def get(self, *_args, **_kwargs):
            return SimpleNamespace(routing_mode="database")

        def execute(self, *_args, **_kwargs):
            raise RuntimeError("routes unavailable")

    decision = resolve_operations_recipients("Lounge", RoutesDown())

    assert decision == {"routing_mode": "database", "recipients": []}


def test_duplicate_normalized_phones_are_sent_once():
    assert _dedupe_normalized_phones(["9876543210", "919876543210", "9988776655", ""]) == [
        "919876543210",
        "919988776655",
    ]


def test_resolve_does_not_close_the_caller_session(db):
    _mode(db, "database")
    resolve_operations_recipients("Lounge", db)
    assert db.get(NotificationSettings, NOTIFICATION_SETTINGS_ROW_ID).routing_mode == "database"


def test_database_dispatch_uses_service_recipients_for_booking_and_payment(db, monkeypatch):
    _use_session(monkeypatch, db)
    when = datetime(2026, 1, 1, tzinfo=timezone.utc)
    lounge = _recipient(db, "9811111111", "Lounge", when)
    other = _recipient(db, "9822222222", "Other", when)
    _route(db, "Lounge", lounge)
    _route(db, "Hotel", other)
    _mode(db, "database")
    client = _arm_dispatch(monkeypatch)

    created = _booking("Lounge")
    created_result = WhatsAppDepartmentService.dispatch_booking_or_enquiry_notification(created)
    paid = dict(created)
    paid["notification_stage"] = "PAYMENT_CONFIRMED"
    paid["status"] = "CONFIRMED"
    paid_result = WhatsAppDepartmentService.dispatch_booking_or_enquiry_notification(paid)
    repeat = WhatsAppDepartmentService.dispatch_booking_or_enquiry_notification(created)

    assert created_result["status"] == "ACCEPTED"
    assert paid_result["status"] == "ACCEPTED"
    assert repeat["status"] == "SKIPPED"
    assert client.calls == ["919811111111", "919811111111"]


def test_database_dispatch_sends_to_nobody_when_the_service_has_no_route(db, monkeypatch):
    _use_session(monkeypatch, db)
    _mode(db, "database")
    client = _arm_dispatch(monkeypatch)

    result = WhatsAppDepartmentService.dispatch_booking_or_enquiry_notification(_booking("Cargo"))

    assert result["status"] == "BYPASSED"
    assert result["reason"] == "no_recipients_configured"
    assert client.calls == []


def test_one_recipient_failure_does_not_stop_the_others(db, monkeypatch):
    _use_session(monkeypatch, db)
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    first = _recipient(db, "9811111111", "First", start)
    second = _recipient(db, "9822222222", "Second", start + timedelta(seconds=1))
    _route(db, "Lounge", first)
    _route(db, "Lounge", second)
    _mode(db, "database")
    client = _arm_dispatch(monkeypatch, fail_phones={"919811111111"})

    result = WhatsAppDepartmentService.dispatch_booking_or_enquiry_notification(_booking("Lounge"))

    assert client.calls == ["919811111111", "919822222222"]
    assert result["status"] == "PARTIALLY_ACCEPTED"
    assert result["recipients_accepted"] == 1


def test_charter_dispatch_uses_private_charter_or_the_stored_category(db, monkeypatch):
    _use_session(monkeypatch, db)
    when = datetime(2026, 1, 1, tzinfo=timezone.utc)
    private = _recipient(db, "9811111111", "Private", when)
    charter = _recipient(db, "9822222222", "Charter", when)
    _route(db, "Private Charter", private)
    _route(db, "Charter", charter)
    _mode(db, "database")
    client = _arm_dispatch(monkeypatch)

    implied = SimpleNamespace(
        request_reference=f"CHR-{uuid.uuid4().hex[:8]}",
        customer_name="Charter Guest",
        email="charter@example.test",
        phone="9876543210",
        country_code="91",
        trip_type="ONE_WAY",
        origin="DEL",
        destination="BOM",
        departure_date="2026-12-01",
        passengers={"total": 2},
    )
    explicit = SimpleNamespace(**implied.__dict__)
    explicit.request_reference = f"CHR-{uuid.uuid4().hex[:8]}"
    explicit.service_category = "Charter"

    implied_result = WhatsAppDepartmentService.dispatch_charter_notification(implied)
    explicit_result = WhatsAppDepartmentService.dispatch_charter_notification(explicit)

    assert implied_result["status"] == "ACCEPTED"
    assert explicit_result["status"] == "ACCEPTED"
    assert client.calls == ["919811111111", "919822222222"]


def test_env_dispatch_still_uses_environment_recipients(db, monkeypatch):
    _use_session(monkeypatch, db)
    monkeypatch.setattr(
        "app.services.whatsapp_department_service.settings",
        SimpleNamespace(
            WHATSAPP_BOOKING_NOTIFY_PHONE_1="9811111111",
            WHATSAPP_BOOKING_NOTIFY_PHONE_2="9822222222",
            WHATSAPP_BOOKING_NOTIFY_PHONE_3="",
            WHATSAPP_BOOKING_NOTIFY_PHONE_4="",
            WHATSAPP_BOOKING_NOTIFY_PHONE_5="",
            WHATSAPP_OFFICER_NOTIFY_PHONE="",
            WHATSAPP_DEPARTMENT_NOTIFICATION_TEMPLATE="",
            WHATSAPP_DEPARTMENT_TEMPLATE_LANG="en",
        ),
    )
    lounge = _recipient(db, "9866666666", "Stored", datetime(2026, 1, 1, tzinfo=timezone.utc))
    _route(db, "Lounge", lounge)
    _mode(db, "env")
    client = _arm_dispatch(monkeypatch)

    result = WhatsAppDepartmentService.dispatch_booking_or_enquiry_notification(_booking("Lounge"))

    assert result["status"] == "ACCEPTED"
    assert client.calls == ["919811111111", "919822222222"]


def test_officer_and_charter_fallbacks_do_not_send_in_database_mode(db, monkeypatch):
    _use_session(monkeypatch, db)
    _mode(db, "database")
    officer = _TextClient()
    charter = _TextClient()
    hook = _TextClient()
    monkeypatch.setattr("app.integrations.whatsapp.handlers.review_payment_flow.whatsapp_client", officer)
    monkeypatch.setattr("app.integrations.whatsapp.handlers.charter_hotel_flow.whatsapp_client", charter)
    monkeypatch.setattr("app.integrations.whatsapp.service.whatsapp_client", hook)

    ReviewPaymentFlowMixin._notify_officer_booking(None, "SHF-OFFICER", 0, False)
    CharterHotelFlowMixin._notify_charter_desk(None, "CHR-DESK")
    trigger_booking_whatsapp_notifications(SimpleNamespace())

    assert officer.calls == []
    assert charter.calls == []
    assert hook.calls == []


def test_officer_fallback_still_sends_in_env_mode(db, monkeypatch):
    _use_session(monkeypatch, db)
    _mode(db, "env")
    officer = _TextClient()
    monkeypatch.setattr("app.integrations.whatsapp.handlers.review_payment_flow.whatsapp_client", officer)
    conv = SimpleNamespace(
        customer_name="Guest",
        customer_phone="919876543210",
        customer_email="guest@example.test",
        selected_service_name="Lounge",
        selected_airport_iata="DEL",
        flight_num="6E1",
        booking_date="2026-12-01",
        passenger_count=1,
        flight_details_json={},
    )

    ReviewPaymentFlowMixin._notify_officer_booking(conv, "SHF-ENV-OFFICER", 1500, True)

    assert len(officer.calls) == 1


def test_enquiry_helpers_follow_database_routing_and_keep_env_fallback(db, monkeypatch):
    _use_session(monkeypatch, db)
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    lounge = _recipient(db, "9811111111", "Lounge", start)
    second = _recipient(db, "9822222222", "Second", start + timedelta(seconds=1))
    transport = _recipient(db, "9833333333", "Transport", start)
    _route(db, "Lounge", lounge)
    _route(db, "Lounge", second)
    _route(db, "Ground Transport", transport)
    _mode(db, "database")
    client = _TextClient(fail_phones={"919811111111"})
    monkeypatch.setattr("app.services.notification_service.whatsapp_client", client)
    context = {
        "booking_ref": "ENQ-LOUNGE",
        "passenger_name": "Guest",
        "passenger_phone": "9876543210",
        "passenger_email": "guest@example.test",
        "details": {},
    }

    lounge_result = NotificationService._dispatch_lounge_enquiry_whatsapp(context)
    transport_result = NotificationService._dispatch_ground_transport_enquiry_whatsapp(context)

    assert client.calls == ["919811111111", "919822222222", "919833333333"]
    assert lounge_result["status"] == "DELIVERED"
    assert lounge_result["failures"] == 1
    assert transport_result["status"] == "DELIVERED"
    assert "919599087957" not in client.calls

    _mode(db, "env")
    monkeypatch.setenv("WHATSAPP_LOUNGE_STAFF_PHONE", "")
    monkeypatch.setenv("WHATSAPP_OFFICER_NOTIFY_PHONE", "")
    monkeypatch.setattr(
        "app.services.notification_service.settings",
        SimpleNamespace(
            WHATSAPP_LOUNGE_STAFF_PHONE="",
            WHATSAPP_OFFICER_NOTIFY_PHONE="",
        ),
        raising=False,
    )
    env_client = _TextClient()
    monkeypatch.setattr("app.services.notification_service.whatsapp_client", env_client)

    env_result = NotificationService._dispatch_lounge_enquiry_whatsapp(context)

    assert env_result["status"] == "DELIVERED"
    assert env_client.calls == ["919599087957"]


def test_customer_notification_sources_do_not_select_operations_routes():
    confirmation = inspect.getsource(NotificationCustomerConfirmationMixin.send_customer_whatsapp_confirmation)
    acknowledgement = inspect.getsource(
        NotificationCustomerEnquiryMixin.send_customer_whatsapp_enquiry_acknowledgement
    )
    operations = inspect.getsource(NotificationCustomerConfirmationMixin.notify_operations_payment_confirmed)

    assert "resolve_operations_recipients" not in confirmation
    assert "resolve_operations_recipients" not in acknowledgement
    assert "resolve_operations_recipients" not in operations
    assert "dispatch_booking_or_enquiry_notification" in operations


def _local_postgres_test_url():
    url = "postgresql://shafsky:shafsky@127.0.0.1:55432/shafsky_test"
    parsed = urlparse(url)
    host = parsed.hostname or ""
    if host not in {"127.0.0.1", "localhost"} or "test" not in (parsed.path or ""):
        return None
    try:
        with socket.create_connection((host, parsed.port or 5432), timeout=0.3):
            return url
    except OSError:
        return None


def test_postgres_database_mode_resolves_one_active_recipient():
    url = _local_postgres_test_url()
    if url is None:
        pytest.skip("Local Postgres test database is not running. Production was not migrated.")

    engine = create_engine(url)
    NotificationRecipient.__table__.create(engine, checkfirst=True)
    NotificationServiceRoute.__table__.create(engine, checkfirst=True)
    NotificationSettings.__table__.create(engine, checkfirst=True)
    session = sessionmaker(bind=engine)()
    recipient_id = None
    try:
        _mode(session, "database")
        recipient = _recipient(
            session,
            "9898989898",
            "Postgres desk",
            datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        recipient_id = recipient.id
        _route(session, "Lounge", recipient)
        session.commit()

        decision = resolve_operations_recipients("Lounge", session)

        assert decision == {"routing_mode": "database", "recipients": ["919898989898"]}
    finally:
        if recipient_id is not None:
            session.query(NotificationServiceRoute).filter(
                NotificationServiceRoute.recipient_id == recipient_id
            ).delete(synchronize_session=False)
            session.query(NotificationRecipient).filter(
                NotificationRecipient.id == recipient_id
            ).delete(synchronize_session=False)
            session.commit()
        _mode(session, "env")
        session.close()
        engine.dispose()
