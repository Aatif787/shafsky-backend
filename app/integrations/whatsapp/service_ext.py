"""
Extended state machine helpers, webhook ingestion service (WhatsAppService),
and direct booking notification hook for WhatsApp integration.
"""

import os
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Tuple

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app.models.whatsapp_models import WhatsAppConversation, WhatsAppWebhookEvent


def _svc():
    import app.integrations.whatsapp.service as svc_mod
    return svc_mod


class WhatsAppStateMachineExtMixin:
    """Mixin providing state helper methods and back-navigation for WhatsAppBookingStateMachine."""

    @classmethod
    def _send_fallback_message(cls, phone_number: str, message: str) -> None:
        """Sends a simple text fallback message when interactive messages fail."""
        s = _svc()
        s.wa_delivery.send_text(phone_number, message, client=s.whatsapp_client)

    @classmethod
    def _merge_flight_details(
        cls, db: Session, conv: WhatsAppConversation, extra: Dict[str, Any]
    ) -> None:
        meta = dict(conv.flight_details_json) if isinstance(conv.flight_details_json, dict) else {}
        meta.update(extra)
        conv.flight_details_json = meta
        flag_modified(conv, "flight_details_json")
        db.commit()

    @classmethod
    def _set_wa_state_key(
        cls, db: Session, conv: WhatsAppConversation, key: str, value: Any
    ) -> None:
        """Set (or remove, when value is None) a key in the transient WhatsApp UI state."""
        state = dict(conv.whatsapp_state_json) if isinstance(conv.whatsapp_state_json, dict) else {}
        if value is None:
            state.pop(key, None)
        else:
            state[key] = value
        conv.whatsapp_state_json = state
        flag_modified(conv, "whatsapp_state_json")
        db.commit()

    @classmethod
    def _get_wa_state_key(cls, conv: WhatsAppConversation, key: str) -> Any:
        if isinstance(conv.whatsapp_state_json, dict):
            return conv.whatsapp_state_json.get(key)
        return None

    @staticmethod
    def _parse_utc_iso(raw: Any) -> Optional[datetime]:
        """Parse an ISO timestamp stored in JSON state. Naive values are treated as UTC."""
        if not raw:
            return None
        try:
            parsed = datetime.fromisoformat(str(raw))
        except (TypeError, ValueError):
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed

    @classmethod
    def _payment_options_active(cls, conv: WhatsAppConversation) -> bool:
        """
        True only while the Continue Payment / Cancel & Start New prompt is live.
        Bare "1"/"2" must not cancel a booking outside this window.
        """
        prompted_at = cls._parse_utc_iso(
            cls._get_wa_state_key(conv, "payment_options_prompted_at")
        )
        if prompted_at is None:
            return False
        age = cls._inactivity_seconds(prompted_at, datetime.now(timezone.utc))
        return age <= _svc()._PAYMENT_OPTIONS_TTL_SECONDS

    @classmethod
    def _store_wa_menu(cls, db: Session, conv: WhatsAppConversation, menu_items: list) -> None:
        """Store WhatsApp UI menu state in whatsapp_state_json (not flight_details_json)."""
        from decimal import Decimal as _Decimal

        def _json_safe(value):
            if isinstance(value, _Decimal):
                return float(value)
            if isinstance(value, dict):
                return {k: _json_safe(v) for k, v in value.items()}
            if isinstance(value, (list, tuple)):
                return [_json_safe(v) for v in value]
            return value

        cls._set_wa_state_key(db, conv, "_wa_menu", _json_safe(list(menu_items)))

    @classmethod
    def _get_wa_menu(cls, conv: WhatsAppConversation) -> list:
        """Retrieve stored menu from whatsapp_state_json, with backward-compat fallback."""
        if isinstance(conv.whatsapp_state_json, dict):
            menu = conv.whatsapp_state_json.get("_wa_menu")
            if menu is not None:
                return list(menu)
        # Backward compatibility: check flight_details_json for legacy _wa_menu
        if isinstance(conv.flight_details_json, dict):
            menu = conv.flight_details_json.get("_wa_menu")
            if menu is not None:
                return list(menu)
        return []

    @classmethod
    def _handle_back_action(cls, db: Session, conv: WhatsAppConversation):  # pylint: disable=inconsistent-return-statements
        """Reverts to the previous logical state and cleans dependent fields."""
        s = _svc()
        wa_delivery = s.wa_delivery
        whatsapp_client = s.whatsapp_client
        SupportedAirport = s.SupportedAirport

        curr = conv.current_state
        if curr in ["CATEGORY_SELECTION", "START"]:
            cls._state_start(db, conv, "Hi")
        elif curr in ["JOURNEY_TYPE_SELECTION", "AIRPORT_JOURNEY_TYPE"]:
            # Clear journey-dependent fields
            conv.selected_airport_iata = None
            conv.selected_airport_name = None
            conv.selected_airport_city = None
            conv.selected_airport_country = None
            conv.selected_service_id = None
            conv.selected_service_name = None
            conv.total_amount = None
            conv.whatsapp_state_json = None
            cls._send_category_menu(db, conv)
        elif curr == "AIRPORT_TRAVEL_TYPE":
            cls._transition_state(db, conv, "JOURNEY_TYPE_SELECTION")
            cls._prompt_journey_type(conv)
        elif curr == "AIRPORT_TRANSIT_TYPE":
            cls._transition_state(db, conv, "JOURNEY_TYPE_SELECTION")
            cls._prompt_journey_type(conv)
        elif curr == "AIRPORT_SELECTION":
            # Clear airport-dependent fields
            conv.selected_airport_iata = None
            conv.selected_airport_name = None
            conv.selected_airport_city = None
            conv.selected_airport_country = None
            conv.selected_service_id = None
            conv.selected_service_name = None
            conv.total_amount = None
            conv.whatsapp_state_json = None
            jt = (
                (conv.flight_details_json or {}).get("journey_type", "DEPARTURE")
                if isinstance(conv.flight_details_json, dict)
                else "DEPARTURE"
            )
            if jt == "TRANSIT":
                cls._transition_state(db, conv, "AIRPORT_TRANSIT_TYPE")
                cls._prompt_transit_type(conv)
            else:
                cls._transition_state(db, conv, "AIRPORT_TRAVEL_TYPE")
                cls._prompt_travel_type(conv, jt.title())
        elif curr == "AIRPORT_CONFIRMATION":
            conv.selected_airport_iata = None
            conv.selected_airport_name = None
            conv.selected_airport_city = None
            conv.selected_airport_country = None
            conv.selected_service_id = None
            conv.selected_service_name = None
            conv.total_amount = None
            conv.whatsapp_state_json = None
            cls._transition_state(db, conv, "AIRPORT_SELECTION")
            wa_delivery.send_text(
                conv.phone_number,
                "Please enter your Airport Name, City, or IATA Code (e.g., Delhi, DEL):",
                client=whatsapp_client,
            )
        elif curr == "TERMINAL_SELECTION":
            # Clear terminal and downstream service fields
            conv.selected_service_id = None
            conv.selected_service_name = None
            conv.total_amount = None
            conv.whatsapp_state_json = None
            # Clear terminal from flight_details_json
            if isinstance(conv.flight_details_json, dict) and "terminal" in conv.flight_details_json:
                meta = dict(conv.flight_details_json)
                meta.pop("terminal", None)
                conv.flight_details_json = meta
                flag_modified(conv, "flight_details_json")
            cls._transition_state(db, conv, "AIRPORT_SELECTION")
            wa_delivery.send_text(
                conv.phone_number,
                "Please enter your Airport Name, City, or IATA Code (e.g., Delhi, DEL):",
                client=whatsapp_client,
            )
        elif curr in ["SERVICE_SELECTION", "AIRPORT_PACKAGE_SELECTION"]:
            # Clear service-dependent fields
            conv.selected_service_id = None
            conv.selected_service_name = None
            conv.total_amount = None
            conv.whatsapp_state_json = None
            if conv.requires_airport:
                metadata = (
                    conv.flight_details_json
                    if isinstance(conv.flight_details_json, dict)
                    else {}
                )
                airport = db.execute(
                    select(SupportedAirport).where(
                        SupportedAirport.iata_code == conv.selected_airport_iata
                    )
                ).scalar_one_or_none()
                if airport:
                    jt_back = metadata.get("journey_type", "DEPARTURE")
                    tt_back = metadata.get("travel_type", "DOMESTIC")
                    tt_back, route_err = cls._authoritative_catalog_travel_type(
                        db, conv, jt_back, tt_back
                    )
                    if route_err:
                        return cls._send_route_classification_error(conv, route_err)
                    applicable_terminals = cls._get_applicable_terminals(
                        db, airport, jt_back, tt_back
                    )
                    if len(applicable_terminals) > 1:
                        cls._transition_state(db, conv, "TERMINAL_SELECTION")
                        cls._prompt_terminal_selection(conv, airport, applicable_terminals)
                        return
                cls._transition_state(db, conv, "AIRPORT_SELECTION")
                wa_delivery.send_text(
                    conv.phone_number,
                    "Please enter your Airport Name, City, or IATA Code (e.g., Delhi, DEL):",
                    client=whatsapp_client,
                )
            else:
                cls._send_category_menu(db, conv)
        elif curr in ["FLIGHT_INPUT", "FLIGHT_CONFIRMATION", "FLIGHT_TYPE_MISMATCH"]:
            # Clear flight-specific fields, preserve journey/travel/terminal metadata
            conv.flight_num = None
            if isinstance(conv.flight_details_json, dict):
                preserved_keys = {"journey_type", "travel_type", "flight_type", "terminal"}
                meta = {k: v for k, v in conv.flight_details_json.items() if k in preserved_keys}
                conv.flight_details_json = meta if meta else None
                flag_modified(conv, "flight_details_json")
            else:
                conv.flight_details_json = None
            cls._transition_state(db, conv, "SERVICE_SELECTION")
            if conv.requires_airport:
                cls._send_airport_services_menu(db, conv)
            else:
                cls._send_service_menu_for_category(db, conv, conv.selected_category)
        elif curr == "DATE_SELECTION":
            conv.booking_date = None
            if conv.requires_flight:
                cls._transition_state(db, conv, "FLIGHT_INPUT")
                wa_delivery.send_text(
                    conv.phone_number,
                    "Please enter your Flight Number (e.g., *EK501*, *AI2424*):",
                    client=whatsapp_client,
                )
            else:
                cls._transition_state(db, conv, "SERVICE_SELECTION")
                if conv.requires_airport:
                    cls._send_airport_services_menu(db, conv)
                else:
                    cls._send_service_menu_for_category(db, conv, conv.selected_category)
        elif curr == "PASSENGER_COUNT":
            conv.passenger_count = 1
            conv.total_amount = None
            cls._transition_state(db, conv, "DATE_SELECTION")
            wa_delivery.send_text(
                conv.phone_number,
                "Please enter your Date of Travel in DD/MM/YYYY format (e.g., 25/08/2026):",
                client=whatsapp_client,
            )
        elif curr == "CUSTOMER_NAME":
            conv.customer_name = None
            cls._transition_state(db, conv, "PASSENGER_COUNT")
            wa_delivery.send_text(
                conv.phone_number,
                "How many passengers will be travelling? (Enter a number, e.g., 2):",
                client=whatsapp_client,
            )
        elif curr == "CUSTOMER_EMAIL":
            conv.customer_email = None
            cls._transition_state(db, conv, "CUSTOMER_NAME")
            wa_delivery.send_text(
                conv.phone_number,
                "May I have your full name?",
                client=whatsapp_client,
            )
        elif curr == "CUSTOMER_PHONE":
            conv.customer_phone = None
            cls._transition_state(db, conv, "CUSTOMER_EMAIL")
            wa_delivery.send_text(
                conv.phone_number,
                "Please provide your email address for booking confirmation:",
                client=whatsapp_client,
            )
        elif curr == "COMPANION_NAMES":
            # Clear any partial companion names and return to phone step
            if isinstance(conv.flight_details_json, dict):
                meta = dict(conv.flight_details_json)
                meta.pop("companion_names_partial", None)
                meta.pop("companion_names_attempts", None)
                meta.pop("passenger_names", None)
                meta.pop("companion_names_pending", None)
                conv.flight_details_json = meta
                flag_modified(conv, "flight_details_json")
            cls._transition_state(db, conv, "CUSTOMER_PHONE")
            wa_delivery.send_text(
                conv.phone_number,
                "Please provide your contact phone number (or type 'Same' to use this WhatsApp number):",
                client=whatsapp_client,
            )
        elif curr == "ADDITIONAL_REQUIREMENTS":
            conv.additional_requirements = None
            # If multi-pax, go back to companion names; otherwise phone
            companions = max(0, (conv.passenger_count or 1) - 1)
            if companions > 0:
                cls._transition_state(db, conv, "COMPANION_NAMES")
                plural = "s" if companions != 1 else ""
                wa_delivery.send_text(
                    conv.phone_number,
                    (
                        f"Noted - {companions} more passenger{plural} travelling with you.\n\n"
                        f"Please send their full name{plural} in one message, separated by commas "
                        f"(e.g., *Rahul Sharma, Priya Sharma*):"
                    ),
                    client=whatsapp_client,
                )
            else:
                cls._transition_state(db, conv, "CUSTOMER_PHONE")
                wa_delivery.send_text(
                    conv.phone_number,
                    "Please provide your contact phone number (or type 'Same'):",
                    client=whatsapp_client,
                )
        elif curr == "BOOKING_REVIEW":
            cls._transition_state(db, conv, "ADDITIONAL_REQUIREMENTS")
            wa_delivery.send_text(
                conv.phone_number,
                (
                    "Do you have any special requirements or notes? "
                    "(Type *None* if no special requests):"
                ),
                client=whatsapp_client,
            )
        elif curr == "TRAVEL_SERVICE_SELECTION":
            cls._transition_state(db, conv, "CATEGORY_SELECTION")
            cls._send_category_menu(db, conv)
        elif curr == "TRAVEL_ENQUIRY":
            cls._travel_enquiry_back(db, conv)
        elif curr in ["WAITING_PAYMENT", "PENDING_PAYMENT"]:
            cls._state_waiting_payment(db, conv, "resend")
            return
        else:
            cls._send_category_menu(db, conv)


class WhatsAppService:
    """Unified WhatsApp Ingestion & Webhook Handler with Event Idempotency."""

    _CONV_LOCK_TTL = 120
    _CONV_LOCK_WAIT_SECONDS = 15

    @classmethod
    def _send_fallback_message(cls, phone_number: str, message: str) -> None:
        s = _svc()
        try:
            s.wa_delivery.send_text(phone_number, message, client=s.whatsapp_client)
        except Exception as e:  # pylint: disable=broad-exception-caught
            s.logger.error(
                "[WhatsApp Service] Failed to send fallback message to %s: %s",
                phone_number,
                str(e),
            )

    @classmethod
    def _normalize_phone(cls, phone_number: Optional[str]) -> str:
        return "".join(filter(str.isdigit, phone_number or "")) or "unknown"

    @classmethod
    def _generate_worker_id(cls) -> str:
        """Generate a unique worker ID for this processing invocation."""
        return uuid.uuid4().hex[:24]

    @classmethod
    def _claim_webhook_event(
        cls, db: Session, msg_id: Optional[str], payload: Dict[str, Any], worker_id: str
    ) -> str:
        """
        Atomically claim an event for processing. Returns one of:
        - "claimed" — this worker now owns the event
        - "duplicate" — event already processed, skip
        - "busy" — another worker is actively processing
        - "error" — database failure, caller should decide on retry
        """
        if not msg_id:
            return "claimed"

        s = _svc()
        logger = s.logger
        now = datetime.now(timezone.utc)
        try:
            db.add(
                WhatsAppWebhookEvent(
                    id=uuid.uuid4(),
                    event_id=msg_id,
                    event_type="message",
                    payload=payload,
                    processed=False,
                    processing_started_at=now,
                    processing_worker_id=worker_id,
                    attempt_count=1,
                    created_at=now,
                )
            )
            db.commit()
            return "claimed"
        except IntegrityError:
            db.rollback()
        except Exception as db_err:  # pylint: disable=broad-exception-caught
            db.rollback()
            logger.error(
                "[WhatsApp Webhook] Database error during event claim for %s: %s",
                msg_id, str(db_err),
            )
            return "error"

        # Event already exists — check its state
        try:
            existing = db.execute(
                select(WhatsAppWebhookEvent).where(WhatsAppWebhookEvent.event_id == msg_id)
            ).scalar_one_or_none()

            if existing is None:
                # Extremely unlikely: vanished between insert collision and select
                return "error"

            if existing.processed:
                logger.info("[WhatsApp Webhook] Duplicate message ignored: %s", msg_id)
                return "duplicate"

            # Not processed — check if another worker is actively processing
            stale_cutoff = now - timedelta(seconds=s._CLAIM_STALE_SECONDS)
            if (
                existing.processing_started_at is not None
                and existing.processing_started_at > stale_cutoff
                and existing.processing_worker_id
                and existing.processing_worker_id != worker_id
            ):
                logger.info(
                    "[WhatsApp Webhook] Event %s is being processed by worker %s, skipping.",
                    msg_id, existing.processing_worker_id,
                )
                return "busy"

            # Stale or unowned claim — attempt atomic reclaim
            old_worker = existing.processing_worker_id
            result = db.execute(
                update(WhatsAppWebhookEvent)
                .where(WhatsAppWebhookEvent.event_id == msg_id)
                .where(WhatsAppWebhookEvent.processed == False)  # noqa: E712
                .where(
                    WhatsAppWebhookEvent.processing_worker_id == old_worker
                )
                .values(
                    processing_worker_id=worker_id,
                    processing_started_at=now,
                    attempt_count=WhatsAppWebhookEvent.attempt_count + 1,
                    error_message=None,
                )
            )
            db.commit()

            if getattr(result, "rowcount", 0) == 1:
                logger.info(
                    "[WhatsApp Webhook] Reclaimed stale event %s (was worker %s).",
                    msg_id, old_worker,
                )
                return "claimed"
            else:
                logger.info(
                    "[WhatsApp Webhook] Failed to reclaim event %s (race lost).", msg_id
                )
                return "busy"

        except Exception as db_err:  # pylint: disable=broad-exception-caught
            db.rollback()
            logger.error(
                "[WhatsApp Webhook] Error during event claim check for %s: %s",
                msg_id, str(db_err),
            )
            return "error"

    @classmethod
    def _mark_event_processed(cls, db: Session, msg_id: Optional[str], worker_id: str) -> None:
        """Mark event as successfully processed with timestamp."""
        if not msg_id:
            return
        logger = _svc().logger
        try:
            now = datetime.now(timezone.utc)
            db.execute(
                update(WhatsAppWebhookEvent)
                .where(WhatsAppWebhookEvent.event_id == msg_id)
                .where(WhatsAppWebhookEvent.processing_worker_id == worker_id)
                .values(processed=True, processed_at=now, error_message=None)
            )
            db.commit()
        except Exception as db_err:  # pylint: disable=broad-exception-caught
            db.rollback()
            logger.error(
                "[WhatsApp Webhook] Failed to mark event processed %s: %s",
                msg_id, str(db_err),
            )

    @classmethod
    def _release_event_claim(
        cls,
        db: Session,
        msg_id: Optional[str],
        worker_id: str,
        error: Optional[str] = None,
    ) -> None:
        """
        Give up this worker's claim without marking the event processed.
        """
        if not msg_id:
            return
        logger = _svc().logger
        try:
            db.execute(
                update(WhatsAppWebhookEvent)
                .where(WhatsAppWebhookEvent.event_id == msg_id)
                .where(WhatsAppWebhookEvent.processed == False)  # noqa: E712
                .where(WhatsAppWebhookEvent.processing_worker_id == worker_id)
                .values(
                    processing_worker_id=None,
                    processing_started_at=None,
                    error_message=error[:2000] if error else None,
                )
            )
            db.commit()
        except Exception as db_err:  # pylint: disable=broad-exception-caught
            db.rollback()
            logger.error(
                "[WhatsApp Webhook] Failed to release event claim for %s: %s",
                msg_id, str(db_err),
            )

    @classmethod
    def _acquire_conversation_lock(cls, phone_number: Optional[str]) -> Dict[str, Any]:
        """
        Acquire a distributed conversation lock via Redis (primary) or in-memory (fallback).
        Ensures one customer = one active state transition at a time across workers.
        """
        s = _svc()
        clean = cls._normalize_phone(phone_number)
        lock_name = f"whatsapp:conv:{clean}"
        deadline = time.time() + cls._CONV_LOCK_WAIT_SECONDS

        while time.time() < deadline:
            token = s.RedisDistributedLock.acquire_lock(lock_name, ttl_seconds=cls._CONV_LOCK_TTL)
            if token:
                return {"token": token, "phone": clean}
            time.sleep(0.05)

        s.logger.warning("[WhatsApp] Conversation lock unavailable for %s", clean)
        return {"token": None, "phone": clean}

    @classmethod
    def _release_conversation_lock(cls, lock_state: Optional[Dict[str, Any]]) -> None:
        if not lock_state:
            return
        phone = lock_state.get("phone") or "unknown"
        token = lock_state.get("token")
        lock_name = f"whatsapp:conv:{phone}"
        if token:
            _svc().RedisDistributedLock.release_lock(lock_name, token)

    @classmethod
    def _parse_inbound_message(cls, msg: Dict[str, Any]) -> Tuple[str, Optional[str], Optional[str]]:
        msg_type = msg.get("type")
        user_text = ""
        input_id = None
        if msg_type == "text":
            user_text = (msg.get("text") or {}).get("body", "").strip()
        elif msg_type == "interactive":
            inter = msg.get("interactive") or {}
            i_type = inter.get("type")
            if i_type == "button_reply":
                btn = inter.get("button_reply") or {}
                input_id = btn.get("id")
                user_text = btn.get("title") or ""
            elif i_type == "list_reply":
                lst = inter.get("list_reply") or {}
                input_id = lst.get("id")
                user_text = lst.get("title") or ""
        return user_text, input_id, msg_type

    @classmethod
    def handle_incoming_webhook(cls, db: Session, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Parses Meta webhook events safely with event-level idempotency."""
        s = _svc()
        logger = s.logger
        try:
            if (
                not isinstance(payload, dict)
                or payload.get("object") != "whatsapp_business_account"
            ):
                return {"status": "ignored", "reason": "Not a whatsapp_business_account event"}

            messages_handled = 0
            statuses_handled = 0
            results = []
            needs_retry = False

            for entry in payload.get("entry", []):
                for change in entry.get("changes", []):
                    val = change.get("value", {})
                    if not isinstance(val, dict):
                        continue

                    for st in val.get("statuses", []):
                        statuses_handled += 1
                        st_id = st.get("id")
                        st_val = str(st.get("status") or "").lower()
                        logger.info(
                            "[WhatsApp Webhook Status] Message %s status: %s",
                            st_id,
                            st_val,
                        )
                        if st_id and db is not None:
                            try:
                                from sqlalchemy import or_, select
                                from app.models.schema import NotificationRecord
                                matched = list(db.scalars(
                                    select(NotificationRecord).where(
                                        or_(
                                            NotificationRecord.message_id == st_id,
                                            NotificationRecord.message_id.like(f"%{st_id}%"),
                                        )
                                    )
                                ))
                                for rec in matched:
                                    s.apply_delivery_status(rec, st_id, st_val, st)
                                if matched:
                                    db.commit()
                            except Exception as st_err:
                                logger.debug("[WhatsApp Webhook Status] NotificationRecord update skipped: %s", st_err)

                    for msg in val.get("messages", []):
                        if not isinstance(msg, dict):
                            continue
                        msg_id = msg.get("id")
                        from_phone = msg.get("from")
                        if not msg_id or not from_phone:
                            logger.warning(
                                "[WhatsApp Webhook] Skipped message missing id or from: %s", msg
                            )
                            continue

                        worker_id = cls._generate_worker_id()

                        # Atomic event claim
                        claim_result = cls._claim_webhook_event(db, msg_id, msg, worker_id)
                        if claim_result == "duplicate":
                            results.append({"from": from_phone, "result": {"status": "duplicate_skipped"}})
                            continue
                        if claim_result == "busy":
                            results.append({"from": from_phone, "result": {"status": "busy_skipped"}})
                            continue
                        if claim_result == "error":
                            needs_retry = True
                            results.append({"from": from_phone, "result": {"status": "claim_error"}})
                            continue

                        user_text, input_id, msg_type = cls._parse_inbound_message(msg)
                        if not user_text and not input_id:
                            cls._send_fallback_message(
                                from_phone, s.wa_copy.UNSUPPORTED_INBOUND
                            )
                            cls._mark_event_processed(db, msg_id, worker_id)
                            results.append(
                                {
                                    "from": from_phone,
                                    "result": {"status": "unsupported_inbound"},
                                }
                            )
                            continue

                        lock_state = None
                        try:
                            lock_state = cls._acquire_conversation_lock(from_phone)
                            if not lock_state or not lock_state.get("token"):
                                needs_retry = True
                                # Release the claim so Meta's retry can reprocess at once
                                cls._release_event_claim(
                                    db, msg_id, worker_id, error="conversation_lock_unavailable"
                                )
                                results.append(
                                    {
                                        "from": from_phone,
                                        "result": {"status": "lock_busy"},
                                    }
                                )
                                continue

                            db.expire_all()

                            try:
                                res = s.WhatsAppBookingStateMachine.process_incoming_event(
                                    db=db,
                                    from_phone=from_phone,
                                    user_input=user_text,
                                    input_type=msg_type or "text",
                                    input_id=input_id,
                                    msg_id=msg_id,
                                    raw_payload=msg,
                                )
                                messages_handled += 1
                                results.append({"from": from_phone, "result": res})
                                # Mark processed AFTER successful state machine execution
                                cls._mark_event_processed(db, msg_id, worker_id)
                            except Exception as state_err:  # pylint: disable=broad-exception-caught
                                try:
                                    db.rollback()
                                except Exception:  # pylint: disable=broad-exception-caught
                                    pass
                                logger.error(
                                    "[WhatsApp Webhook] State machine error for phone=%s msg_id=%s: %s",
                                    from_phone,
                                    msg_id,
                                    str(state_err),
                                    exc_info=True,
                                )
                                # Release the claim so Meta's retry can reprocess at once
                                cls._release_event_claim(db, msg_id, worker_id, str(state_err))
                                try:
                                    cls._send_fallback_message(
                                        from_phone,
                                        "I apologize, but I encountered an error. Please type 'Hi' to restart.",
                                    )
                                except Exception as fallback_err:  # pylint: disable=broad-exception-caught
                                    logger.error(
                                        "[WhatsApp Webhook] Failed to send fallback message: %s",
                                        str(fallback_err),
                                    )
                                results.append(
                                    {
                                        "from": from_phone,
                                        "result": {"status": "error", "error": str(state_err)},
                                    }
                                )
                                needs_retry = True
                        finally:
                            cls._release_conversation_lock(lock_state)

            return {
                "status": "retry" if needs_retry else "processed",
                "messages_handled": messages_handled,
                "statuses_handled": statuses_handled,
                "results": results,
            }

        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.error(
                "[WhatsApp Webhook] Exception in handle_incoming_webhook: %s",
                str(e),
                exc_info=True,
            )
            return {"status": "error", "error": str(e)}


def trigger_booking_whatsapp_notifications(booking: Any) -> None:
    """Non-blocking notification helper for direct web bookings."""
    s = _svc()
    whatsapp_client = s.whatsapp_client
    settings = s.settings
    logger = s.logger
    try:
        if not whatsapp_client.is_configured():
            return
        officer_phone = (
            getattr(settings, "WHATSAPP_OFFICER_NOTIFY_PHONE", None)
            or os.getenv("WHATSAPP_OFFICER_NOTIFY_PHONE", "")
        ).strip()
        if not officer_phone:
            logger.warning("[WhatsApp Notification Hook] WHATSAPP_OFFICER_NOTIFY_PHONE not set; skipping officer notify")
            return
        msg = (
            "🚨 *NEW DIRECT BOOKING CREATED*\n\n"
            f"• *Booking Ref*: {getattr(booking, 'booking_ref', 'N/A')}\n"
            f"• *Customer*: {getattr(booking, 'passenger_name', 'N/A')} ({getattr(booking, 'passenger_phone', 'N/A')})\n"
            f"• *Service*: {getattr(booking, 'service_type', 'N/A')}\n"
            f"• *Airport*: {getattr(booking, 'origin_code', 'N/A')}\n"
            f"• *Amount*: ₹{int(getattr(booking, 'total_amount', 0)):,}\n"
            f"• *Status*: {getattr(booking, 'status', 'PENDING')}"
        )
        whatsapp_client.send_text_message(officer_phone, msg)
    except Exception as err:  # pylint: disable=broad-exception-caught
        logger.warning("[WhatsApp Notification Hook] Exception: %s", err)
