"""Unit tests for WhatsApp webhook claim / session helpers (no database required)."""

from datetime import datetime, timezone, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from sqlalchemy.exc import IntegrityError

from app.integrations.whatsapp.service import (
    PROTECTED_STATES,
    WhatsAppBookingStateMachine,
    WhatsAppService,
    _PAYMENT_OPTIONS_TTL_SECONDS,
)
from app.integrations.whatsapp.copy import UNSUPPORTED_INBOUND, BRAND


def _webhook_payload(msg_id: str = "wamid.test", body: str = "Hi") -> dict:
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "messages": [
                                {
                                    "id": msg_id,
                                    "from": "919999999999",
                                    "type": "text",
                                    "text": {"body": body},
                                }
                            ]
                        }
                    }
                ]
            }
        ],
    }


def test_inactivity_treats_naive_datetime_as_utc():
    now = datetime(2026, 8, 29, 12, 0, tzinfo=timezone.utc)
    last = datetime(2026, 8, 29, 11, 50)
    assert WhatsAppBookingStateMachine._inactivity_seconds(last, now) == 600.0


def test_inactivity_clamps_future_last_act():
    now = datetime(2026, 8, 29, 12, 0, tzinfo=timezone.utc)
    last = now + timedelta(minutes=5)
    assert WhatsAppBookingStateMachine._inactivity_seconds(last, now) == 0.0


def test_claim_reports_duplicate_when_event_already_processed():
    db = MagicMock()
    db.commit.side_effect = IntegrityError("INSERT", {}, Exception("duplicate"))
    db.execute.return_value.scalar_one_or_none.return_value = MagicMock(processed=True)
    result = WhatsAppService._claim_webhook_event(
        db, "wamid.dup", {"id": "wamid.dup"}, "worker-a"
    )
    assert result == "duplicate"
    db.rollback.assert_called()


def test_claim_reports_busy_while_another_worker_holds_a_fresh_claim():
    db = MagicMock()
    db.commit.side_effect = IntegrityError("INSERT", {}, Exception("duplicate"))
    db.execute.return_value.scalar_one_or_none.return_value = MagicMock(
        processed=False,
        processing_started_at=datetime.now(timezone.utc),
        processing_worker_id="worker-other",
    )
    result = WhatsAppService._claim_webhook_event(
        db, "wamid.busy", {"id": "wamid.busy"}, "worker-a"
    )
    assert result == "busy"


def test_claim_reclaims_a_released_event_so_meta_retry_is_not_dropped():
    """A released claim (worker id cleared) must be reclaimable immediately."""
    db = MagicMock()
    released = MagicMock(
        processed=False,
        processing_started_at=None,
        processing_worker_id=None,
    )
    calls = {"n": 0}

    def commit():
        calls["n"] += 1
        if calls["n"] == 1:
            raise IntegrityError("INSERT", {}, Exception("duplicate"))

    db.commit.side_effect = commit
    db.execute.return_value.scalar_one_or_none.return_value = released
    db.execute.return_value.rowcount = 1

    result = WhatsAppService._claim_webhook_event(
        db, "wamid.released", {"id": "wamid.released"}, "worker-b"
    )
    assert result == "claimed"


def test_claim_without_msg_id_allows_processing():
    db = MagicMock()
    assert WhatsAppService._claim_webhook_event(db, None, {}, "worker-a") == "claimed"
    db.add.assert_not_called()


def test_parse_text_and_button_and_image():
    text, input_id, msg_type = WhatsAppService._parse_inbound_message(
        {"type": "text", "text": {"body": " Hi "}}
    )
    assert text == "Hi"
    assert input_id is None
    assert msg_type == "text"

    text, input_id, msg_type = WhatsAppService._parse_inbound_message(
        {
            "type": "interactive",
            "interactive": {
                "type": "button_reply",
                "button_reply": {"id": "btn_1", "title": "OK"},
            },
        }
    )
    assert text == "OK"
    assert input_id == "btn_1"

    text, input_id, msg_type = WhatsAppService._parse_inbound_message({"type": "image"})
    assert text == ""
    assert input_id is None
    assert msg_type == "image"


def test_unsupported_inbound_copy_uses_brand():
    assert BRAND in UNSUPPORTED_INBOUND
    assert "Hi" in UNSUPPORTED_INBOUND


def test_lock_busy_releases_claim_and_asks_for_retry():
    """
    Regression: leaving the claim in place made Meta's retry look 'busy' for
    _CLAIM_STALE_SECONDS, which silently dropped the customer's message.
    """
    db = MagicMock()
    with patch.object(
        WhatsAppService, "_claim_webhook_event", return_value="claimed"
    ), patch.object(
        WhatsAppService,
        "_acquire_conversation_lock",
        return_value={"backend": "none", "token": None, "phone": "919999999999"},
    ), patch.object(
        WhatsAppBookingStateMachine, "process_incoming_event"
    ) as process_mock, patch.object(
        WhatsAppService, "_mark_event_processed"
    ) as mark_mock, patch.object(
        WhatsAppService, "_release_event_claim"
    ) as release_mock:
        result = WhatsAppService.handle_incoming_webhook(db, _webhook_payload("wamid.lockbusy"))

    process_mock.assert_not_called()
    mark_mock.assert_not_called()
    release_mock.assert_called_once()
    assert result["status"] == "retry"
    assert result["results"][0]["result"]["status"] == "lock_busy"


def test_state_machine_error_releases_claim_for_retry():
    db = MagicMock()
    with patch.object(
        WhatsAppService, "_claim_webhook_event", return_value="claimed"
    ), patch.object(
        WhatsAppService,
        "_acquire_conversation_lock",
        return_value={"backend": "redis", "token": "t", "phone": "919999999999"},
    ), patch.object(
        WhatsAppBookingStateMachine,
        "process_incoming_event",
        side_effect=RuntimeError("boom"),
    ), patch.object(
        WhatsAppService, "_mark_event_processed"
    ) as mark_mock, patch.object(
        WhatsAppService, "_release_event_claim"
    ) as release_mock, patch.object(
        WhatsAppService, "_send_fallback_message"
    ):
        result = WhatsAppService.handle_incoming_webhook(db, _webhook_payload("wamid.err"))

    mark_mock.assert_not_called()
    release_mock.assert_called_once()
    assert result["status"] == "retry"


def test_duplicate_claim_is_not_retried():
    db = MagicMock()
    with patch.object(WhatsAppService, "_claim_webhook_event", return_value="duplicate"), patch.object(
        WhatsAppBookingStateMachine, "process_incoming_event"
    ) as process_mock:
        result = WhatsAppService.handle_incoming_webhook(db, _webhook_payload("wamid.dup2"))

    process_mock.assert_not_called()
    assert result["status"] == "processed"
    assert result["results"][0]["result"]["status"] == "duplicate_skipped"


def test_payment_options_window_gates_bare_numeric_replies():
    now = datetime.now(timezone.utc)
    fresh = SimpleNamespace(
        whatsapp_state_json={"payment_options_prompted_at": now.isoformat()}
    )
    stale = SimpleNamespace(
        whatsapp_state_json={
            "payment_options_prompted_at": (
                now - timedelta(seconds=_PAYMENT_OPTIONS_TTL_SECONDS + 60)
            ).isoformat()
        }
    )
    never = SimpleNamespace(whatsapp_state_json=None)

    assert WhatsAppBookingStateMachine._payment_options_active(fresh) is True
    assert WhatsAppBookingStateMachine._payment_options_active(stale) is False
    assert WhatsAppBookingStateMachine._payment_options_active(never) is False


def test_parse_utc_iso_handles_naive_and_garbage():
    parsed = WhatsAppBookingStateMachine._parse_utc_iso("2026-08-29T12:00:00")
    assert parsed is not None and parsed.tzinfo is timezone.utc
    assert WhatsAppBookingStateMachine._parse_utc_iso("not-a-date") is None
    assert WhatsAppBookingStateMachine._parse_utc_iso(None) is None


def test_quote_state_is_protected_from_inactivity_reset():
    assert "AWAITING_QUOTE" in PROTECTED_STATES
    assert "WAITING_PAYMENT" in PROTECTED_STATES


def test_menu_is_read_back_from_the_column_it_was_written_to():
    db = MagicMock()
    conv = SimpleNamespace(whatsapp_state_json=None, flight_details_json=None)
    items = [{"id": "1", "title": "Meet & Greet", "price": 5000}]
    with patch("app.integrations.whatsapp.service.flag_modified"):
        WhatsAppBookingStateMachine._store_wa_menu(db, conv, items)
    assert WhatsAppBookingStateMachine._get_wa_menu(conv) == items


def test_menu_falls_back_to_legacy_flight_details_column():
    conv = SimpleNamespace(
        whatsapp_state_json=None,
        flight_details_json={"_wa_menu": [{"id": "9", "title": "Legacy"}]},
    )
    assert WhatsAppBookingStateMachine._get_wa_menu(conv) == [{"id": "9", "title": "Legacy"}]


def _fake_conv(**overrides):
    base = dict(
        id="conv-1",
        phone_number="919999999999",
        current_state="START",
        booking_ref=None,
        booking_id=None,
        payment_status=None,
        whatsapp_state_json=None,
        flight_details_json=None,
        last_user_activity_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
        created_at=datetime.now(timezone.utc),
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def test_expired_session_without_hi_does_not_send_welcome_menu():
    """Idle timeout must not dump the welcome menu on a random inbound message."""
    db = MagicMock()
    conv = _fake_conv(current_state="START")

    with patch.object(
        WhatsAppBookingStateMachine, "get_or_create_conversation", return_value=(conv, True)
    ), patch.object(
        WhatsAppBookingStateMachine, "_reset_conversation_fields", return_value=conv
    ), patch.object(
        WhatsAppBookingStateMachine, "_send_category_menu"
    ) as menu_mock, patch(
        "app.integrations.whatsapp.service.wa_delivery.send_text"
    ) as send_text, patch(
        "app.integrations.whatsapp.service.wa_delivery.send_list"
    ):
        result = WhatsAppBookingStateMachine.process_incoming_event(
            db, "919999999999", "ok"
        )

    menu_mock.assert_not_called()
    send_text.assert_called_once()
    text = send_text.call_args[0][1]
    assert "Type *Hi*" in text
    assert "expired" in text.lower()
    assert result["status"] == "session_expired_awaiting_hi"


def test_expired_session_with_hi_sends_welcome_menu():
    db = MagicMock()
    conv = _fake_conv(current_state="START")

    with patch.object(
        WhatsAppBookingStateMachine, "get_or_create_conversation", return_value=(conv, True)
    ), patch.object(
        WhatsAppBookingStateMachine, "_reset_conversation_fields", return_value=conv
    ), patch.object(
        WhatsAppBookingStateMachine, "_transition_state"
    ), patch.object(
        WhatsAppBookingStateMachine,
        "_send_category_menu",
        return_value={"status": "category_menu_sent", "success": True},
    ) as menu_mock:
        result = WhatsAppBookingStateMachine.process_incoming_event(
            db, "919999999999", "Hi"
        )

    menu_mock.assert_called_once()
    assert menu_mock.call_args.kwargs.get("prefix_notice") or (
        len(menu_mock.call_args.args) >= 3 and "expired" in menu_mock.call_args.args[2].lower()
    )
    assert result["status"] == "category_menu_sent"


def test_start_state_without_hi_does_not_send_welcome_menu():
    """START / CANCELLED / COMPLETED must wait for Hi before opening the menu."""
    db = MagicMock()
    conv = _fake_conv(current_state="START")

    with patch.object(
        WhatsAppBookingStateMachine, "get_or_create_conversation", return_value=(conv, False)
    ), patch.object(
        WhatsAppBookingStateMachine, "_send_category_menu"
    ) as menu_mock, patch(
        "app.integrations.whatsapp.delivery.send_text"
    ) as send_text, patch(
        "app.integrations.whatsapp.handlers.airport_flow.wa_delivery.send_text",
        send_text,
    ):
        result = WhatsAppBookingStateMachine.process_incoming_event(
            db, "919999999999", "random noise"
        )

    menu_mock.assert_not_called()
    send_text.assert_called()
    text = send_text.call_args[0][1]
    assert "Type *Hi*" in text
    assert result["status"] == "awaiting_hi"
