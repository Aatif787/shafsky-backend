"""Regression tests for the WhatsApp operations-notification delivery defects.

Covers the failures that made real bookings produce no WhatsApp alert while the
mocked suites stayed green:

1. Meta error 131047 (recipient outside the 24-hour customer service window) used
   to be flattened into a generic FAILED entry, so a template-less deployment
   looked like a transient API problem instead of a configuration gap.
2. Per-recipient Meta diagnostics (HTTP status, error code, subcode) were thrown
   away before being logged or persisted.
3. The durable duplicate guard raised AttributeError on the generic JSON payload
   column and was swallowed at DEBUG level, so restarting the process re-sent
   every notification.
4. A missing template produced only a WARNING, even in production.
5. The resolved service category was not carried into the notification context,
   so multi-service bookings were routed as Meet & Greet.
"""

import logging
from types import SimpleNamespace

import pytest
from sqlalchemy import delete, select

from app.models.schema import NotificationRecord, NotificationStatus
from app.services.whatsapp_department_service import Department, WhatsAppDepartmentService

OPS_TEMPLATE_TYPE = "WHATSAPP_OPS_NOTIFICATION"

RECIPIENTS = {
    "WHATSAPP_BOOKING_NOTIFY_PHONE_1": "9599087957",
    "WHATSAPP_BOOKING_NOTIFY_PHONE_2": "9599225778",
    "WHATSAPP_BOOKING_NOTIFY_PHONE_3": "9217522660",
    "WHATSAPP_BOOKING_NOTIFY_PHONE_4": "8864931247",
    "WHATSAPP_BOOKING_NOTIFY_PHONE_5": "9599087959",
    "WHATSAPP_OFFICER_NOTIFY_PHONE": "",
}

NORMALIZED_RECIPIENTS = {
    "919599087957",
    "919599225778",
    "919217522660",
    "918864931247",
    "919599087959",
}


def _settings(template="", language="en_US", production=False):
    return SimpleNamespace(
        **RECIPIENTS,
        is_production=production,
        WHATSAPP_DEPARTMENT_NOTIFICATION_TEMPLATE=template,
        WHATSAPP_DEPARTMENT_TEMPLATE_LANG=language,
    )


def _purge_ops_records():
    from app.database import SessionLocal

    with SessionLocal() as db:
        db.execute(delete(NotificationRecord).where(NotificationRecord.template_type == OPS_TEMPLATE_TYPE))
        db.commit()


@pytest.fixture(autouse=True)
def isolate_notification_state(monkeypatch):
    """Reset in-memory and durable idempotency state around every test."""
    monkeypatch.setattr("app.services.whatsapp_department_service.settings", _settings())
    monkeypatch.setattr("app.core.redis.get_redis_client", lambda *a, **k: None)
    WhatsAppDepartmentService.clear_idempotency_cache()
    _purge_ops_records()
    yield
    WhatsAppDepartmentService.clear_idempotency_cache()
    _purge_ops_records()


def _accept_all(monkeypatch):
    monkeypatch.setattr(
        "app.integrations.whatsapp.client.WhatsAppClient.is_configured", lambda self: True
    )

    def _send_text(self, phone, message):
        return {"success": True, "message_id": f"wamid.{phone}"}

    monkeypatch.setattr(
        "app.integrations.whatsapp.client.WhatsAppClient.send_text_message", _send_text
    )


# ---------------------------------------------------------------------------
# 1. Window-closed recipients are reported precisely
# ---------------------------------------------------------------------------

def test_window_closed_is_reported_and_remaining_recipients_still_attempted(monkeypatch):
    """A 131047 rejection must be actionable and must not stop the other four sends."""
    attempted = []

    def _send_text(self, phone, message):
        attempted.append(phone)
        if phone == "919599087957":
            return {
                "success": False,
                "status": "failed",
                "status_code": 400,
                "error_code": 131047,
                "error_subcode": 0,
                "error": "Meta API Error (400): Re-engagement message",
            }
        return {"success": True, "message_id": f"wamid.{phone}"}

    monkeypatch.setattr(
        "app.integrations.whatsapp.client.WhatsAppClient.is_configured", lambda self: True
    )
    monkeypatch.setattr(
        "app.integrations.whatsapp.client.WhatsAppClient.send_text_message", _send_text
    )

    result = WhatsAppDepartmentService.dispatch_booking_or_enquiry_notification(
        {
            "booking_ref": "SHF-WINDOW-001",
            "service_category": "Airport Assistance",
            "service_type": "Meet & Greet",
        }
    )

    assert len(attempted) == 5, "every recipient must still be attempted"
    assert result["recipients_attempted"] == 5
    assert result["recipients_accepted"] == 4
    assert result["status"] == "PARTIALLY_ACCEPTED"
    assert result["template_configured"] is False
    assert result["status_counts"] == {"WINDOW_CLOSED": 1, "ACCEPTED": 4}

    blocked = next(r for r in result["recipient_results"] if r["status"] == "WINDOW_CLOSED")
    assert blocked["http_status"] == 400
    assert blocked["error_code"] == 131047
    assert "24-hour" in blocked["remedy"]


def test_all_recipients_window_closed_fails_without_suppressing_a_retry(monkeypatch):
    """A fully rejected dispatch must stay retryable instead of being cached as sent."""
    calls = []

    def _send_text(self, phone, message):
        calls.append(phone)
        return {
            "success": False,
            "status": "failed",
            "status_code": 400,
            "error_code": 131047,
            "error": "Meta API Error (400): Re-engagement message",
        }

    monkeypatch.setattr(
        "app.integrations.whatsapp.client.WhatsAppClient.is_configured", lambda self: True
    )
    monkeypatch.setattr(
        "app.integrations.whatsapp.client.WhatsAppClient.send_text_message", _send_text
    )

    context = {"booking_ref": "SHF-WINDOW-002", "service_category": "Lounge"}
    first = WhatsAppDepartmentService.dispatch_booking_or_enquiry_notification(context)

    assert first["status"] == "FAILED"
    assert first["recipients_accepted"] == 0
    assert first["status_counts"] == {"WINDOW_CLOSED": 5}
    assert len(calls) == 5

    # A rejected dispatch must not be treated as delivered: retry is allowed.
    second = WhatsAppDepartmentService.dispatch_booking_or_enquiry_notification(context)
    assert second["status"] == "FAILED"
    assert len(calls) == 10


def test_template_rejection_keeps_meta_status_and_subcode(monkeypatch):
    """Template problems must surface the real Meta code, not a bare failure string."""

    def _send_text(self, phone, message):  # pragma: no cover - template path is patched below
        return {"success": True, "message_id": f"wamid.{phone}"}

    def _send_template(self, **kwargs):
        return {
            "success": False,
            "status": "failed",
            "status_code": 400,
            "error_code": 132001,
            "error_subcode": 2494010,
            "error": "Meta API Error (400): Template name does not exist in the translation",
        }

    monkeypatch.setattr("app.services.whatsapp_department_service.settings", _settings("shafsky_ops_alert"))
    monkeypatch.setattr(
        "app.integrations.whatsapp.client.WhatsAppClient.is_configured", lambda self: True
    )
    monkeypatch.setattr(
        "app.integrations.whatsapp.client.WhatsAppClient.send_text_message", _send_text
    )
    monkeypatch.setattr(
        "app.integrations.whatsapp.client.WhatsAppClient.send_message", lambda self, **kw: _send_template(self, **kw)
    )

    result = WhatsAppDepartmentService.dispatch_booking_or_enquiry_notification(
        {"booking_ref": "SHF-TEMPLATE-001", "service_category": "Ticketing"}
    )

    assert result["template_configured"] is True
    assert result["status"] == "FAILED"
    entry = result["recipient_results"][0]
    assert entry["status"] == "TEMPLATE_NOT_FOUND"
    assert entry["http_status"] == 400
    assert entry["error_code"] == 132001
    assert entry["error_subcode"] == 2494010
    assert "WHATSAPP_DEPARTMENT_NOTIFICATION_TEMPLATE" in entry["remedy"]


# ---------------------------------------------------------------------------
# 2. Missing template is a production-level error
# ---------------------------------------------------------------------------

def test_missing_template_uses_approved_default_in_production(monkeypatch, caplog):
    """A production deployment without the env value must still use the approved template."""
    monkeypatch.setattr(
        "app.services.whatsapp_department_service.settings", _settings(production=True)
    )
    _accept_all(monkeypatch)

    with caplog.at_level(logging.WARNING, logger="shafsky.whatsapp_department"):
        result = WhatsAppDepartmentService.dispatch_booking_or_enquiry_notification(
            {"booking_ref": "SHF-NOTPLATE-001", "service_category": "Lounge"}
        )

    assert result["template_configured"] is True
    assert result["status"] == "ACCEPTED"
    messages = [record.getMessage() for record in caplog.records]
    assert any("using the approved default template" in m for m in messages)
    assert not any("131047" in m for m in messages)


# ---------------------------------------------------------------------------
# 3. Durable duplicate guard works across a simulated restart
# ---------------------------------------------------------------------------

def test_durable_guard_blocks_resend_after_process_restart(monkeypatch):
    """Clearing in-memory state must not re-send: the NotificationRecord is the guard.

    This is the regression for `payload["idempotency_key"].astext`, which raised
    AttributeError on the generic JSON column and disabled durable deduplication.
    """
    _accept_all(monkeypatch)
    context = {"booking_ref": "SHF-DURABLE-001", "service_category": "Ground Transport"}

    first = WhatsAppDepartmentService.dispatch_booking_or_enquiry_notification(context)
    assert first["status"] == "ACCEPTED"

    from app.database import SessionLocal

    with SessionLocal() as db:
        stored = db.scalars(
            select(NotificationRecord).where(NotificationRecord.template_type == OPS_TEMPLATE_TYPE)
        ).all()
    assert len(stored) == 1
    assert stored[0].payload["idempotency_key"] == "wa_booking_notify:SHF-DURABLE-001"
    assert stored[0].payload["status_counts"] == {"ACCEPTED": 5}
    assert stored[0].status in (NotificationStatus.COMPLETED, NotificationStatus.DELIVERED)

    # Simulate a worker restart: in-memory state is gone, Redis is unavailable.
    WhatsAppDepartmentService.clear_idempotency_cache()

    second = WhatsAppDepartmentService.dispatch_booking_or_enquiry_notification(context)
    assert second["status"] == "SKIPPED"
    assert second["reason"] == "duplicate"


# ---------------------------------------------------------------------------
# 4. Startup configuration diagnostic
# ---------------------------------------------------------------------------

def test_validate_configuration_reports_missing_template_and_recipients(monkeypatch):
    monkeypatch.setattr(
        "app.services.whatsapp_department_service.settings",
        SimpleNamespace(
            WHATSAPP_BOOKING_NOTIFY_PHONE_1="",
            WHATSAPP_BOOKING_NOTIFY_PHONE_2="",
            WHATSAPP_BOOKING_NOTIFY_PHONE_3="",
            WHATSAPP_BOOKING_NOTIFY_PHONE_4="",
            WHATSAPP_BOOKING_NOTIFY_PHONE_5="",
            WHATSAPP_OFFICER_NOTIFY_PHONE="",
            WHATSAPP_DEPARTMENT_NOTIFICATION_TEMPLATE="",
            WHATSAPP_DEPARTMENT_TEMPLATE_LANG="",
        ),
    )
    monkeypatch.setattr(
        "app.integrations.whatsapp.client.WhatsAppClient.is_configured", lambda self: True
    )

    report = WhatsAppDepartmentService.validate_configuration()

    assert report["ok"] is False
    assert report["recipients_configured"] == 0
    assert report["template_configured"] is False
    assert any("WHATSAPP_BOOKING_NOTIFY_PHONE_1..5" in p for p in report["problems"])
    assert any("WHATSAPP_DEPARTMENT_NOTIFICATION_TEMPLATE" in p for p in report["problems"])


def test_validate_configuration_passes_for_a_complete_setup(monkeypatch):
    monkeypatch.setattr(
        "app.services.whatsapp_department_service.settings", _settings("shafsky_ops_alert")
    )
    monkeypatch.setattr(
        "app.integrations.whatsapp.client.WhatsAppClient.is_configured", lambda self: True
    )

    report = WhatsAppDepartmentService.validate_configuration()

    assert report["ok"] is True
    assert report["recipients_configured"] == 5
    assert report["template"] == "shafsky_ops_alert"
    assert report["problems"] == []


# ---------------------------------------------------------------------------
# 5. Service category drives department routing
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "category,expected_department,expected_label",
    [
        ("Airport Assistance", Department.DEPARTMENT_1, "Meet & Greet"),
        ("Lounge", Department.DEPARTMENT_1, "Lounge"),
        ("Ground Transport", Department.DEPARTMENT_2, "Transport"),
        ("Ticketing", Department.DEPARTMENT_2, "Ticketing"),
        ("Private Charter", Department.DEPARTMENT_3, "Air Charter"),
        ("Cargo & Logistics", Department.DEPARTMENT_3, "Cargo & Logistics"),
        ("Medical Assistance", Department.DEPARTMENT_3, "Special Services"),
    ],
)
def test_resolved_service_category_routes_to_the_right_department(
    category, expected_department, expected_label
):
    department, label = WhatsAppDepartmentService.resolve_department(category, "multi_service")
    assert department == expected_department
    assert label == expected_label


# ---------------------------------------------------------------------------
# 6. Delivery status is per message, never per record
# ---------------------------------------------------------------------------

def _ops_record(message_ids):
    return NotificationRecord(
        template_type=OPS_TEMPLATE_TYPE,
        channel="WHATSAPP_OPS",
        payload={
            "idempotency_key": "wa_booking_notify:SHF-PERMSG-001",
            "messages": [
                {"index": i, "phone": f"919****{mid[-3:]}", "message_id": mid, "status": "accepted"}
                for i, mid in enumerate(message_ids, 1)
            ],
        },
        status=NotificationStatus.COMPLETED,
        message_id=",".join(message_ids),
    )


def test_one_delivered_recipient_does_not_mark_the_whole_record_delivered():
    """Regression: a single personal-number delivery used to hide four 131047 failures."""
    from app.integrations.whatsapp.service import apply_delivery_status

    ids = [f"wamid.A{i}" for i in range(1, 6)]
    rec = _ops_record(ids)

    apply_delivery_status(rec, ids[3], "delivered", {"status": "delivered"})

    assert rec.status == NotificationStatus.COMPLETED, "4 of 5 are still unconfirmed"
    assert rec.delivered_at is None
    statuses = {m["message_id"]: m["status"] for m in rec.payload["messages"]}
    assert statuses[ids[3]] == "delivered"
    assert statuses[ids[0]] == "accepted"


def test_all_five_delivered_marks_the_record_delivered():
    from app.integrations.whatsapp.service import apply_delivery_status

    ids = [f"wamid.B{i}" for i in range(1, 6)]
    rec = _ops_record(ids)

    for mid in ids:
        apply_delivery_status(rec, mid, "delivered", {"status": "delivered"})

    assert rec.status == NotificationStatus.DELIVERED
    assert rec.delivered_at is not None


def test_window_closed_delivery_failure_keeps_the_meta_code_on_the_record():
    from app.integrations.whatsapp.service import apply_delivery_status

    ids = [f"wamid.C{i}" for i in range(1, 6)]
    rec = _ops_record(ids)

    failure = {
        "status": "failed",
        "errors": [
            {
                "code": 131047,
                "title": "Re-engagement message",
                "message": "Re-engagement message",
                "error_data": {
                    "details": "Message failed to send because more than 24 hours have passed "
                    "since the customer last replied to this number."
                },
            }
        ],
    }
    for mid in ids[:4]:
        apply_delivery_status(rec, mid, "failed", failure)
    apply_delivery_status(rec, ids[4], "delivered", {"status": "delivered"})

    assert rec.status == NotificationStatus.FAILED
    assert "131047" in (rec.error_log or "")
    assert "24 hours" in (rec.error_log or "")
    assert "4/5" in (rec.error_log or "")
    failed_entries = [m for m in rec.payload["messages"] if m["status"] == "failed"]
    assert len(failed_entries) == 4
    assert all(m["error_code"] == 131047 for m in failed_entries)


def test_all_five_recipients_attempted_independently_when_business_numbers_fail_window(monkeypatch):
    """
    Confirms all five recipients are attempted independently, and delivery failures
    on the four business-app numbers outside the 24-hour window never prevent, interrupt,
    or corrupt delivery to 8864931247.
    """
    from app.integrations.whatsapp.service import apply_delivery_status

    monkeypatch.setattr("app.services.whatsapp_department_service.settings", _settings())

    sent_order = []

    def mock_send_text(phone, msg):
        sent_order.append(phone)
        return {"success": True, "message_id": f"wamid.INDEP_{phone[-4:]}"}

    monkeypatch.setattr("app.integrations.whatsapp.client.WhatsAppClient.is_configured", lambda self: True)
    monkeypatch.setattr(
        "app.integrations.whatsapp.client.WhatsAppClient.send_text_message",
        lambda self, phone, msg: mock_send_text(phone, msg),
    )

    context = {
        "booking_ref": "SHF-FIVE-RECIPIENTS-001",
        "service_category": "Meet & Greet",
        "service_type": "Gold Package",
    }

    result = WhatsAppDepartmentService.dispatch_booking_or_enquiry_notification(context)

    # 1. Verify all 5 recipients were attempted independently in sequence
    assert len(sent_order) == 5
    assert set(sent_order) == NORMALIZED_RECIPIENTS
    assert result["recipients_attempted"] == 5
    assert result["recipients_accepted"] == 5
    assert result["status"] == "ACCEPTED"

    # 2. Simulate webhook outcomes: 4 business numbers fail with 131047, personal 8864931247 succeeds
    personal_phone = "918864931247"
    personal_mid = f"wamid.INDEP_{personal_phone[-4:]}"

    rec = _ops_record([f"wamid.INDEP_{p[-4:]}" for p in sent_order])

    window_closed_failure = {
        "status": "failed",
        "errors": [
            {
                "code": 131047,
                "title": "Re-engagement message",
                "message": "Re-engagement message",
                "error_data": {
                    "details": "Message failed to send because more than 24 hours have passed since the customer last replied to this number."
                },
            }
        ],
    }

    # Deliver to the personal number
    apply_delivery_status(rec, personal_mid, "delivered", {"status": "delivered"})

    # Fail the four business numbers
    for p in sent_order:
        if p != personal_phone:
            apply_delivery_status(rec, f"wamid.INDEP_{p[-4:]}", "failed", window_closed_failure)

    # 3. Verify per-message attribution
    messages_by_mid = {m["message_id"]: m for m in rec.payload["messages"]}
    assert messages_by_mid[personal_mid]["status"] == "delivered"
    assert "error_code" not in messages_by_mid[personal_mid]

    business_mids = [f"wamid.INDEP_{p[-4:]}" for p in sent_order if p != personal_phone]
    for b_mid in business_mids:
        assert messages_by_mid[b_mid]["status"] == "failed"
        assert messages_by_mid[b_mid]["error_code"] == 131047

    # Overall record captures the 4 failures with error 131047
    assert rec.status == NotificationStatus.FAILED
    assert "131047" in (rec.error_log or "")
    assert "4/5" in (rec.error_log or "")


# ---------------------------------------------------------------------------
# 7. Customer records are single-message: the billing block must be actionable
# ---------------------------------------------------------------------------

def _customer_record(message_id="wamid.CUST1"):
    """A customer notification record has no payload["messages"] map: one wamid per record."""
    return NotificationRecord(
        template_type="WHATSAPP_CUSTOMER_ENQUIRY_ACK",
        channel="WHATSAPP",
        payload={
            "booking_ref": "SHF-20261029-7D58B18A",
            "status": "ENQUIRY",
            "delivery": {
                "template": "shafsky_booking_confirmation",
                "template_language": "en",
                "api_status": "sent",
                "http_status": 200,
                "message_id": message_id,
            },
        },
        status=NotificationStatus.COMPLETED,
        message_id=message_id,
    )


def test_customer_billing_block_is_persisted_with_the_meta_code_and_remedy():
    """Regression: 131042 was stored as a raw dict, so the admin saw FAILED with no cause."""
    from app.integrations.whatsapp.service import apply_delivery_status

    mid = "wamid.CUST131042"
    rec = _customer_record(mid)
    failure = {
        "status": "failed",
        "errors": [
            {
                "code": 131042,
                "title": "Business eligibility payment issue",
                "message": "Business eligibility payment issue",
                "error_data": {
                    "details": "Message failed to send because no payment method is set up "
                    "for your WhatsApp Business account."
                },
            }
        ],
    }

    apply_delivery_status(rec, mid, "failed", failure)

    assert rec.status == NotificationStatus.FAILED
    assert "131042" in rec.error_log
    assert "Business eligibility payment issue" in rec.error_log
    assert "no payment method" in rec.error_log
    assert "{" not in rec.error_log, "raw dict repr must not leak into the admin error log"
    assert rec.delivered_at is None
    assert rec.payload["delivery"]["error_code"] == 131042
    assert "no payment method" in rec.payload["delivery"]["error_detail"]


def test_customer_window_closed_failure_stays_readable():
    from app.integrations.whatsapp.service import apply_delivery_status

    mid = "wamid.CUST131047"
    rec = _customer_record(mid)
    failure = {
        "status": "failed",
        "errors": [
            {
                "code": 131047,
                "title": "Re-engagement message",
                "message": "Re-engagement message",
                "error_data": {
                    "details": "Message failed to send because more than 24 hours have passed "
                    "since the customer last replied to this number."
                },
            }
        ],
    }

    apply_delivery_status(rec, mid, "failed", failure)

    assert rec.status == NotificationStatus.FAILED
    assert "131047" in rec.error_log
    assert "24 hours" in rec.error_log
    assert rec.payload["delivery"]["error_code"] == 131047


def test_customer_delivery_confirmation_still_marks_the_record_delivered():
    from app.integrations.whatsapp.service import apply_delivery_status

    mid = "wamid.CUSTOK"
    rec = _customer_record(mid)

    apply_delivery_status(rec, mid, "delivered", {"status": "delivered"})

    assert rec.status == NotificationStatus.DELIVERED
    assert rec.delivered_at is not None


def test_billing_code_has_a_documented_remedy():
    label, remedy = WhatsAppDepartmentService.META_FAILURE_REMEDIES[131042]
    assert label == "BILLING_ISSUE"
    assert "billing_hub" in remedy
    assert "payment" in remedy.lower()
