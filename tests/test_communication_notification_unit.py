"""Unit tests for NotificationService.create_notification and CommunicationService (mocked DB, no postgres required)."""

import uuid
from unittest.mock import MagicMock
from app.services.notification_service import NotificationService
from app.services.communication_service import CommunicationService
from app.models.schema import UserNotification
from app.providers.base import MockWhatsAppProvider, MockSMSProvider


def test_notification_service_create_notification_success():
    mock_db = MagicMock()
    user_id = str(uuid.uuid4())

    record = NotificationService.create_notification(
        mock_db,
        user_id=user_id,
        title="Welcome Alert",
        message="Welcome to Shafsky Aviation",
        notification_type="SYSTEM",
        channel="IN_APP",
        data={"key": "val"},
        link="/dashboard",
    )

    assert isinstance(record, UserNotification)
    assert record.title == "Welcome Alert"
    assert record.body == "Welcome to Shafsky Aviation"
    assert record.kind == "SYSTEM"
    assert str(record.user_id) == user_id
    assert record.link == "/dashboard"
    assert record.read_at is None
    assert record.created_at is not None
    mock_db.add.assert_called_once_with(record)
    mock_db.commit.assert_called_once()
    mock_db.refresh.assert_called_once_with(record)


def test_notification_service_create_notification_with_uuid_obj():
    mock_db = MagicMock()
    user_uuid = uuid.uuid4()

    record = NotificationService.create_notification(
        mock_db,
        user_id=user_uuid,
        title="UUID Object Alert",
        message="Message with uuid object",
    )

    assert isinstance(record, UserNotification)
    assert record.user_id == user_uuid
    assert record.kind == "GENERAL"


def test_notification_service_create_notification_empty_user_id():
    mock_db = MagicMock()

    assert NotificationService.create_notification(mock_db, user_id=None, title="T", message="M") is None
    assert NotificationService.create_notification(mock_db, user_id="", title="T", message="M") is None
    mock_db.add.assert_not_called()
    mock_db.commit.assert_not_called()


def test_notification_service_create_notification_invalid_user_id():
    mock_db = MagicMock()

    assert NotificationService.create_notification(mock_db, user_id="not-a-valid-uuid", title="T", message="M") is None
    assert NotificationService.create_notification(mock_db, user_id=12345, title="T", message="M") is None
    mock_db.add.assert_not_called()
    mock_db.commit.assert_not_called()


def test_notification_service_create_notification_rollback_on_error():
    mock_db = MagicMock()
    mock_db.commit.side_effect = RuntimeError("DB connection dropped")
    user_id = str(uuid.uuid4())

    try:
        NotificationService.create_notification(
            mock_db,
            user_id=user_id,
            title="Fail Alert",
            message="Fail Message",
        )
        assert False, "Should have re-raised exception"
    except RuntimeError:
        pass

    mock_db.rollback.assert_called_once()


def test_communication_service_dispatch_whatsapp_creates_notification():
    mock_db = MagicMock()
    user_id = str(uuid.uuid4())

    res = CommunicationService.dispatch_whatsapp(
        mock_db,
        user_id=user_id,
        phone_number="+919876543210",
        template_name="booking_confirmed",
        parameters={"ref": "SHF-123"},
        provider=MockWhatsAppProvider(),
    )

    assert res["status"] == "SENT"
    mock_db.add.assert_called_once()
    mock_db.commit.assert_called_once()


def test_communication_service_dispatch_sms_creates_notification():
    mock_db = MagicMock()
    user_id = str(uuid.uuid4())

    res = CommunicationService.dispatch_sms(
        mock_db,
        user_id=user_id,
        phone_number="+919876543210",
        message="Your flight is confirmed",
        provider=MockSMSProvider(),
    )

    assert res["status"] == "SENT"
    mock_db.add.assert_called_once()
    mock_db.commit.assert_called_once()
