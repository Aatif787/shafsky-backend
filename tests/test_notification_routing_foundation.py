"""Phase 2 tests for notification routing models.

Behaviour under test: NotificationRecipient, NotificationServiceRoute, and
NotificationSettings can be stored with unique phones, unique service routes,
a foreign key, and a single env-mode settings row. No existing test file covers
these tables. WhatsAppDepartmentService.get_recipient_phone_numbers stays env-based.

User instruction: "Implement PHASE 2: DATABASE FOUNDATION" and "Add appropriate tests".
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models.notification_routing import (
    NOTIFICATION_SETTINGS_ROW_ID,
    NotificationRecipient,
    NotificationServiceRoute,
    NotificationSettings,
)
from app.services.whatsapp_department_service import WhatsAppDepartmentService


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
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def test_models_can_be_created(db):
    recipient = NotificationRecipient(label="Ops desk", phone_number="9876543210", is_active=True)
    db.add(recipient)
    db.flush()
    route = NotificationServiceRoute(service_key="Airport Assistance", recipient_id=recipient.id)
    settings = NotificationSettings()
    db.add_all([route, settings])
    db.commit()

    assert recipient.phone_number == "919876543210"
    assert route.is_active is True
    assert settings.routing_mode == "env"
    assert settings.id == NOTIFICATION_SETTINGS_ROW_ID


def test_duplicate_phone_is_rejected(db):
    db.add(NotificationRecipient(phone_number="+91 98765 43210"))
    db.commit()
    db.add(NotificationRecipient(phone_number="919876543210"))
    with pytest.raises(IntegrityError):
        db.commit()


def test_duplicate_service_recipient_mapping_is_rejected(db):
    recipient = NotificationRecipient(phone_number="9122223333")
    db.add(recipient)
    db.flush()
    db.add(NotificationServiceRoute(service_key="Lounge", recipient_id=recipient.id))
    db.commit()
    db.add(NotificationServiceRoute(service_key="Lounge", recipient_id=recipient.id))
    with pytest.raises(IntegrityError):
        db.commit()


def test_foreign_key_rejects_unknown_recipient(db):
    db.add(
        NotificationServiceRoute(
            service_key="Ticketing",
            recipient_id=uuid.uuid4(),
        )
    )
    with pytest.raises(IntegrityError):
        db.commit()


def test_settings_default_to_env_and_only_one_row_is_allowed(db):
    first = NotificationSettings()
    db.add(first)
    db.commit()
    assert first.routing_mode == "env"

    db.add(NotificationSettings(id=uuid.uuid4(), routing_mode="env"))
    with pytest.raises(IntegrityError):
        db.commit()


def test_invalid_routing_mode_is_rejected(db):
    db.add(NotificationSettings(routing_mode="live"))
    with pytest.raises(IntegrityError):
        db.commit()


def test_migration_upgrade_and_downgrade_on_empty_database():
    import importlib.util
    from pathlib import Path

    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    revision_path = (
        Path(__file__).resolve().parents[1]
        / "alembic"
        / "versions"
        / "d3e4f5a6b7c8_add_notification_routing_tables.py"
    )
    spec = importlib.util.spec_from_file_location("notification_routing_revision", revision_path)
    revision = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(revision)

    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        context = MigrationContext.configure(connection)
        with Operations.context(context):
            revision.upgrade()

        inspector = inspect(connection)
        tables = set(inspector.get_table_names())
        assert "notification_recipients" in tables
        assert "notification_service_routes" in tables
        assert "notification_settings" in tables
        mode = connection.execute(text("SELECT routing_mode FROM notification_settings")).scalar()
        assert mode == "env"

        context = MigrationContext.configure(connection)
        with Operations.context(context):
            revision.downgrade()

        remaining = set(inspect(connection).get_table_names())
        assert "notification_recipients" not in remaining
        assert "notification_service_routes" not in remaining
        assert "notification_settings" not in remaining

    engine.dispose()


def test_environment_recipient_selection_is_unchanged(monkeypatch):
    class _Settings:
        WHATSAPP_BOOKING_NOTIFY_PHONE_1 = "9876543210"
        WHATSAPP_BOOKING_NOTIFY_PHONE_2 = ""
        WHATSAPP_BOOKING_NOTIFY_PHONE_3 = "919876543210"
        WHATSAPP_BOOKING_NOTIFY_PHONE_4 = ""
        WHATSAPP_BOOKING_NOTIFY_PHONE_5 = ""
        WHATSAPP_OFFICER_NOTIFY_PHONE = "9000000000"

    monkeypatch.setattr(
        "app.services.whatsapp_department_service.settings",
        _Settings(),
    )
    phones = WhatsAppDepartmentService.get_recipient_phone_numbers()
    assert phones == ["919876543210"]
