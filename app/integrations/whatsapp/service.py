"""
WhatsApp Integration Service & Persistent Booking State Machine Engine.
Coordinator module composing domain flow mixins:
- AirportFlowMixin: Categories, Journey Types, Airports, Terminals, Service Packages
- FlightFlowMixin: AviationStack flight verification, mismatch handling, smart alignment
- DetailsFlowMixin: Travel dates, passengers, customer contact information
- CharterHotelFlowMixin: Private charter, luxury hotels, chauffeur ground transportation
- ReviewPaymentFlowMixin: Booking creation, Razorpay payment link checkout, idempotency
"""

import os
import time
import uuid
import logging
from typing import Dict, Any, Optional, Tuple
from datetime import datetime, timezone, timedelta
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from app.models.whatsapp_models import WhatsAppConversation, WhatsAppMessage, WhatsAppWebhookEvent
from app.core.redis import get_redis_client
from app.core.redis_lock import RedisDistributedLock
from app.models.journey_models import SupportedAirport
from app.integrations.whatsapp.client import whatsapp_client
from app.integrations.whatsapp import copy as wa_copy
from app.integrations.whatsapp import delivery as wa_delivery
from app.config import settings
from app.integrations.whatsapp.handlers import (
    AirportFlowMixin,
    FlightFlowMixin,
    DetailsFlowMixin,
    CharterHotelFlowMixin,
    TravelServicesFlowMixin,
    ReviewPaymentFlowMixin,
    OFFICIAL_CATEGORIES,
)
from app.integrations.whatsapp.service_ext import (
    WhatsAppService,
    WhatsAppStateMachineExtMixin,
    trigger_booking_whatsapp_notifications,
)

logger = logging.getLogger(__name__)


_DELIVERED_STATES = {"delivered", "read"}


def _meta_failure_remedy(code: Any) -> Optional[str]:
    """Actionable remedy for a Meta delivery error code, from the shared catalogue.

    Delivery failures arrive on the status webhook after Meta already accepted the
    message (HTTP 200 + wamid), so the send path cannot surface them. Without this,
    a customer notification killed by 131042 (WABA has no payment method) reads as
    an opaque JSON dump in the admin queue.
    """
    try:
        code_int = int(code) if code is not None else None
    except (TypeError, ValueError):
        return None
    if code_int is None:
        return None
    try:
        from app.services.whatsapp_department_service import WhatsAppDepartmentService
    except Exception:  # pragma: no cover - defensive import guard
        return None
    entry = WhatsAppDepartmentService.META_FAILURE_REMEDIES.get(code_int)
    if not entry:
        return None
    return f"{entry[1]} (classified: {entry[0]})"


def _format_delivery_failure(first_error: Dict[str, Any], detail: Optional[str]) -> str:
    """One readable, bounded line for a failed Meta delivery."""
    code = first_error.get("code")
    title = first_error.get("title") or first_error.get("message")
    bits = [f"code {code}"] if code is not None else []
    if title:
        bits.append(str(title))
    prefix = f"Meta delivery failed ({', '.join(bits)})" if bits else "Meta delivery failed"
    text = str(detail or title or "").strip()
    if len(text) > 400:
        text = text[:397] + "..."
    return f"{prefix}: {text}" if text else prefix


def apply_delivery_status(rec, message_id: str, status_value: str, status_payload: dict) -> None:
    """Record one Meta delivery status against the message it belongs to.

    The Cloud API accepts a send (HTTP 200 plus a wamid) and reports the real
    outcome only in a later status webhook. Storing a single status per record
    therefore hid failures: one recipient being delivered used to mark a
    five-recipient notification DELIVERED while the other four were rejected with
    131047. This keeps a per-message map and derives the record status from every
    message instead.
    """
    from app.models.schema import NotificationStatus

    payload = rec.payload if isinstance(rec.payload, dict) else {}
    messages = payload.get("messages")
    errors = status_payload.get("errors") or []
    first_error = errors[0] if errors and isinstance(errors[0], dict) else {}
    error_detail = (
        (first_error.get("error_data") or {}).get("details")
        or first_error.get("message")
        or None
    )

    if isinstance(messages, list):
        for item in messages:
            if isinstance(item, dict) and item.get("message_id") == message_id:
                item["status"] = status_value or item.get("status")
                if first_error:
                    item["error_code"] = first_error.get("code")
                    item["error_detail"] = error_detail
                break
        rec.payload = {**payload, "messages": messages}
        flag_modified(rec, "payload")
        states = [
            str(item.get("status") or "").lower()
            for item in messages
            if isinstance(item, dict)
        ]
        failed = [
            item for item in messages
            if isinstance(item, dict) and str(item.get("status") or "").lower() == "failed"
        ]
    else:
        states = []
        failed = []

    if failed:
        codes = sorted({item.get("error_code") for item in failed if item.get("error_code")})
        detail = next((item.get("error_detail") for item in failed if item.get("error_detail")), None)
        bounded_detail = str(detail)[:300] if detail else ""
        rec.status = NotificationStatus.FAILED
        rec.error_log = (
            f"Meta delivery failed for {len(failed)}/{len(states)} recipients "
            f"(codes: {codes}). {bounded_detail}"
        ).strip()
        logger.error(
            "[WhatsApp Webhook Status] Delivery failed for %d/%d recipients on record %s (codes %s): %s",
            len(failed),
            len(states),
            getattr(rec, "id", "?"),
            codes,
            detail or "no detail",
        )
        for code in codes:
            remedy = _meta_failure_remedy(code)
            if remedy:
                logger.error(
                    "[WhatsApp Webhook Status] Error %s on record %s: %s",
                    code,
                    getattr(rec, "id", "?"),
                    remedy,
                )
        return

    if states and all(state in _DELIVERED_STATES for state in states):
        rec.status = NotificationStatus.DELIVERED
        rec.delivered_at = datetime.now(timezone.utc)
        return

    if states:
        # Accepted, but not every message has confirmed delivery yet.
        rec.status = NotificationStatus.COMPLETED
        return

    # Legacy record without a per-message map (single-message notifications —
    # the customer confirmation / enquiry acknowledgement path).
    if status_value in _DELIVERED_STATES:
        rec.status = NotificationStatus.DELIVERED
        rec.delivered_at = datetime.now(timezone.utc)
    elif status_value == "failed":
        rec.status = NotificationStatus.FAILED
        rec.error_log = _format_delivery_failure(first_error, error_detail)
        delivery = dict(payload.get("delivery") or {})
        delivery.update(
            {
                "api_status": "failed",
                "error_code": first_error.get("code"),
                "error_detail": str(error_detail or "")[:400] or None,
                "failed_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        rec.payload = {**payload, "delivery": delivery}
        flag_modified(rec, "payload")
        logger.error(
            "[WhatsApp Webhook Status] Delivery failed for record %s (message %s): %s",
            getattr(rec, "id", "?"),
            message_id,
            rec.error_log,
        )
        remedy = _meta_failure_remedy(first_error.get("code"))
        if remedy:
            logger.error(
                "[WhatsApp Webhook Status] Error %s on record %s: %s",
                first_error.get("code"),
                getattr(rec, "id", "?"),
                remedy,
            )


CANCEL_COMMANDS = {"cancel", "stop", "abort"}
HELP_COMMANDS = {"help", "support", "info"}
RESTART_COMMANDS = {
    "hi", "hello", "hey", "start", "menu", "restart", "main menu", "0",
    "reset", "start over"
}
RESTART_BUTTON_IDS = {"btn_restart", "btn_menu", "btn_main_menu"}

# Stale claim threshold: if a worker claimed an event more than this many seconds
# ago without completing, another worker may reclaim it (crash recovery).
_CLAIM_STALE_SECONDS = 120

# How long "1"/"2" stay bound to the pending-payment options prompt.
_PAYMENT_OPTIONS_TTL_SECONDS = 1800

# States that must never be silently reset by the inactivity timer, because they
# own a booking reference the customer or the payment gateway still refers to.
PROTECTED_STATES = {
    "START",
    "CANCELLED",
    "BOOKING_CONFIRMED",
    "WAITING_PAYMENT",
    "PENDING_PAYMENT",
    "AWAITING_QUOTE",
    "COMPLETED",
    "PAYMENT_PROCESSING",
}


class WhatsAppBookingStateMachine(
    WhatsAppStateMachineExtMixin,
    AirportFlowMixin,
    FlightFlowMixin,
    DetailsFlowMixin,
    CharterHotelFlowMixin,
    TravelServicesFlowMixin,
    ReviewPaymentFlowMixin,
):
    """
    Persistent state machine processor for Shafsky Aviation WhatsApp Booking Flow.
    Enforces: ONE USER MESSAGE = ONE VALIDATION = ONE STATE TRANSITION = ONE NEXT RESPONSE.
    """

    @classmethod
    def _transition_state(cls, db: Session, conv: WhatsAppConversation, new_state: str) -> None:
        """Helper to transition conversation state with standard audit logging."""
        old_state = conv.current_state
        if old_state != new_state:
            logger.info(
                "[WhatsApp Session] State changed: %s -> %s for %s",
                old_state,
                new_state,
                conv.phone_number,
            )
            conv.current_state = new_state
            conv.updated_at = datetime.now(timezone.utc)
            db.commit()

    @classmethod
    def _reset_conversation_fields(cls, conv: WhatsAppConversation, db: Optional[Session] = None) -> WhatsAppConversation:
        """Resets all booking-specific fields for a new session and cancels orphaned pending drafts."""
        if db is not None and conv.booking_id:
            try:
                from app.models.schema import Booking, BookingStatus
                pending_draft = db.scalar(
                    select(Booking).where(Booking.id == conv.booking_id).where(Booking.status == BookingStatus.PENDING)
                )
                if pending_draft:
                    pending_draft.status = BookingStatus.CANCELLED
                    pending_draft.notes = "Cancelled by user session restart/reset"
                    pending_draft.updated_at = datetime.now(timezone.utc)
                    # Commit here: not every caller commits afterwards, and an
                    # uncommitted cancellation would leave the draft PENDING.
                    db.commit()
            except Exception as err:  # pylint: disable=broad-exception-caught
                logger.warning(
                    "[WhatsApp Session] Could not cancel pending draft %s for %s: %s",
                    conv.booking_id,
                    conv.phone_number,
                    err,
                )
                try:
                    db.rollback()
                except Exception:  # pylint: disable=broad-exception-caught
                    pass

        conv.selected_category = None
        conv.selected_service_id = None
        conv.selected_service_name = None
        conv.requires_airport = True
        conv.requires_flight = True
        conv.requires_date = True
        conv.requires_passenger_count = True
        conv.selected_airport_iata = None
        conv.selected_airport_name = None
        conv.selected_airport_city = None
        conv.selected_airport_country = None
        conv.flight_num = None
        conv.flight_details_json = None
        conv.booking_date = None
        conv.passenger_count = 1
        conv.customer_name = None
        conv.customer_email = None
        conv.customer_phone = None
        conv.additional_requirements = None
        conv.total_amount = None
        conv.booking_id = None
        conv.booking_ref = None
        conv.payment_status = "PENDING"
        conv.razorpay_order_id = None
        conv.razorpay_payment_id = None
        conv.razorpay_payment_link_id = None
        conv.razorpay_payment_url = None
        conv.currency = "INR"
        conv.whatsapp_state_json = None
        return conv

    @staticmethod
    def _inactivity_seconds(last_act: Optional[datetime], now_utc: datetime) -> float:
        """Seconds since last activity. Naive datetimes are treated as UTC."""
        if last_act is None:
            return 0.0
        if last_act.tzinfo is None:
            last_act = last_act.replace(tzinfo=timezone.utc)
        return max(0.0, (now_utc - last_act).total_seconds())

    @classmethod
    def _get_payment_session_timeout_seconds(cls) -> float:
        """Configurable payment session timeout. Default: RAZORPAY_PAYMENT_LINK_EXPIRE_HOURS or 60 min."""
        raw = (os.getenv("WHATSAPP_PAYMENT_SESSION_TIMEOUT_MINUTES") or "").strip()
        if raw:
            try:
                return max(5, int(raw)) * 60
            except ValueError:
                pass
        expire_hours_raw = (os.getenv("RAZORPAY_PAYMENT_LINK_EXPIRE_HOURS") or "").strip()
        if expire_hours_raw:
            try:
                return max(1, int(expire_hours_raw)) * 3600
            except ValueError:
                pass
        return 60 * 60  # 60 minutes default

    @classmethod
    def get_or_create_conversation(
        cls, db: Session, phone_number: str
    ) -> Tuple[WhatsAppConversation, bool]:
        """
        Retrieves active conversation session or creates a new one.
        Enforces configurable session inactivity expiry (default 15 minutes).
        Uses last_user_activity_at for session timeout (not updated_at).
        Returns: (conversation_object, is_session_expired_flag)
        """
        clean_phone = "".join(filter(str.isdigit, phone_number))
        # PII: logs must never carry full phone numbers.
        masked_phone_for_log = (
            clean_phone[:3] + "****" + clean_phone[-3:] if len(clean_phone) > 6 else "****"
        )
        stmt = select(WhatsAppConversation).where(WhatsAppConversation.phone_number == clean_phone)
        conv = db.execute(stmt).scalar_one_or_none()

        is_expired = False
        now_utc = datetime.now(timezone.utc)
        timeout_minutes = int(os.getenv("WHATSAPP_SESSION_TIMEOUT_MINUTES", "15"))
        timeout_seconds = timeout_minutes * 60

        if not conv:
            conv = WhatsAppConversation(
                id=uuid.uuid4(),
                phone_number=clean_phone,
                current_state="START",
                customer_phone=clean_phone,
                created_at=now_utc,
                updated_at=now_utc,
                last_user_activity_at=now_utc,
            )
            db.add(conv)
            try:
                db.commit()
                db.refresh(conv)
            except IntegrityError:
                db.rollback()
                conv = db.execute(stmt).scalar_one_or_none()
                if conv is None:
                    raise
                logger.info(
                    "[WhatsApp Session] Lost create race; using existing session for %s",
                    masked_phone_for_log,
                )
            else:
                logger.info("[WhatsApp Session] New session for %s", masked_phone_for_log)
        else:
            # Use last_user_activity_at for timeout, falling back to updated_at
            last_act = conv.last_user_activity_at or conv.updated_at or conv.created_at
            if last_act:
                inactivity_seconds = cls._inactivity_seconds(last_act, now_utc)

                if (
                    inactivity_seconds > timeout_seconds
                    and conv.current_state not in PROTECTED_STATES
                ):
                    logger.info(
                        "[WhatsApp Session] Session expired for %s after %.0fs inactivity.",
                        masked_phone_for_log,
                        inactivity_seconds,
                    )
                    conv = cls._reset_conversation_fields(conv, db)
                    conv.current_state = "START"
                    conv.updated_at = now_utc
                    db.commit()
                    db.refresh(conv)
                    is_expired = True
                else:
                    logger.info(
                        "[WhatsApp Session] Existing session for %s (state: %s)",
                        masked_phone_for_log,
                        conv.current_state,
                    )
            else:
                logger.info(
                    "[WhatsApp Session] Existing session for %s (state: %s)",
                    masked_phone_for_log,
                    conv.current_state,
                )

        return conv, is_expired

    @classmethod
    def _handle_pending_payment_restart(
        cls, db: Session, conv: WhatsAppConversation
    ) -> Dict[str, Any]:
        """
        When customer sends Hi/restart while in WAITING_PAYMENT/PENDING_PAYMENT:
        - Check if payment session is expired
        - If not expired: offer Continue Payment / Cancel & Start New
        - If expired: offer Start New Booking (auto-cancel old)
        """
        from app.models.schema import Booking, BookingStatus

        now_utc = datetime.now(timezone.utc)
        payment_timeout = cls._get_payment_session_timeout_seconds()

        booking = None
        if conv.booking_ref:
            booking = db.scalar(select(Booking).where(Booking.booking_ref == conv.booking_ref))

        # If booking already confirmed, clear state and restart
        if booking and booking.status == BookingStatus.CONFIRMED:
            conv.payment_status = "SUCCESSFUL"
            conv.current_state = "COMPLETED"
            db.commit()
            conv = cls._reset_conversation_fields(conv, db)
            cls._transition_state(db, conv, "CATEGORY_SELECTION")
            return cls._send_category_menu(db, conv)

        # Age the payment session from when the link was issued (or the draft was
        # created). conv.updated_at is unusable here: process_incoming_event bumps
        # it on every inbound message, so it is always seconds old.
        payment_anchor = cls._parse_utc_iso(
            cls._get_wa_state_key(conv, "payment_link_issued_at")
        )
        if payment_anchor is None and booking is not None:
            payment_anchor = booking.created_at
        if payment_anchor is None:
            payment_anchor = conv.created_at or now_utc
        session_age = cls._inactivity_seconds(payment_anchor, now_utc)
        is_expired = session_age > payment_timeout

        if is_expired:
            # Payment session expired — offer fresh start
            if booking and booking.status in (BookingStatus.PENDING,):
                booking.status = BookingStatus.CANCELLED
                booking.notes = "Cancelled — payment session expired"
                booking.updated_at = now_utc
            conv = cls._reset_conversation_fields(conv, db)
            cls._transition_state(db, conv, "CATEGORY_SELECTION")
            db.commit()
            result = cls._send_category_menu(
                db, conv,
                prefix_notice="Your previous payment session has expired. Let's start a new booking.\n\n"
            )
            return result

        # Payment still valid — offer Continue or Cancel
        body_text = (
            f"*Shafsky Aviation Services*\n\n"
            f"You have an existing pending booking *{conv.booking_ref or 'N/A'}*.\n\n"
            f"Would you like to continue with payment or start a new booking?"
        )
        buttons = [
            {"id": "btn_continue_payment", "title": "Continue Payment"},
            {"id": "btn_cancel_start_new", "title": "Cancel & Start New"},
        ]
        cls._set_wa_state_key(db, conv, "payment_options_prompted_at", now_utc.isoformat())
        wa_delivery.send_buttons(
            phone=conv.phone_number,
            body_text=body_text,
            buttons=buttons,
            header_text="Pending Booking",
            fallback_text=(
                f"{body_text}\n\n"
                "Reply *1* to Continue Payment or *2* to Cancel & Start New Booking."
            ),
            client=whatsapp_client,
        )
        return {"status": "pending_payment_options_sent", "success": True}

    @classmethod
    def process_incoming_event(
        cls,
        db: Session,
        from_phone: str,
        user_input: str,
        input_type: str = "text",
        input_id: Optional[str] = None,
        msg_id: Optional[str] = None,
        raw_payload: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:  # pylint: disable=too-many-positional-arguments,too-many-return-statements
        """
        Main entry point for processing customer inputs against current conversation state.
        Strictly enforces ONE MESSAGE = ONE STATE TRANSITION = ONE NEXT RESPONSE.
        """
        try:
            conv, session_expired = cls.get_or_create_conversation(db, from_phone)

            log_msg = WhatsAppMessage(
                id=uuid.uuid4(),
                conversation_id=conv.id,
                message_id=msg_id,
                direction="INBOUND",
                message_type=input_type,
                content=user_input,
                raw_payload=raw_payload,
                created_at=datetime.now(timezone.utc),
            )
            db.add(log_msg)
            # Update last_user_activity_at for session timeout tracking
            now_utc = datetime.now(timezone.utc)
            conv.last_user_activity_at = now_utc
            conv.updated_at = now_utc
            db.commit()

            # Immediate read receipt for instant perceived responsiveness
            if msg_id:
                try:
                    whatsapp_client.mark_message_as_read(msg_id)
                except Exception:
                    pass

            text_clean = (user_input or "").strip()
            text_lower = text_clean.lower()

            if session_expired:
                # Session was reset to START in get_or_create_conversation.
                # Do NOT auto-send the welcome menu — only greet on an explicit
                # Hi/restart. Random inbound after idle used to dump the full menu.
                logger.info(
                    "[WhatsApp Session] Expired session for %s awaiting Hi (input: %r)",
                    conv.phone_number,
                    text_clean[:40],
                )
                if text_lower in RESTART_COMMANDS or input_id in RESTART_BUTTON_IDS:
                    conv = cls._reset_conversation_fields(conv, db)
                    cls._transition_state(db, conv, "CATEGORY_SELECTION")
                    result = cls._send_category_menu(
                        db, conv, prefix_notice=wa_copy.EXPIRED_PREFIX
                    )
                    if not result.get("success") and result.get("status") != "category_menu_sent":
                        cls._send_fallback_message(
                            conv.phone_number,
                            "Your session has expired. Please type 'Hi' to start again.",
                        )
                    return result
                wa_delivery.send_text(
                    conv.phone_number,
                    wa_copy.SESSION_EXPIRED_PROMPT,
                    client=whatsapp_client,
                )
                return {"status": "session_expired_awaiting_hi", "success": True}

            # 1. Global Interrupts (Cancel, Help, Back)
            if text_lower in CANCEL_COMMANDS or input_id == "btn_cancel":
                # Resetting also cancels the orphaned PENDING draft and clears
                # booking_ref, which would otherwise linger in the ops dashboard.
                conv = cls._reset_conversation_fields(conv, db)
                cls._transition_state(db, conv, "CANCELLED")
                db.commit()
                msg = (
                    "*Shafsky Aviation Services*\n\n"
                    "Your booking process has been cancelled.\n\n"
                    "Type *Hi* anytime to begin a new reservation."
                )
                wa_delivery.send_text(conv.phone_number, msg, client=whatsapp_client)
                return {"status": "cancelled", "state": conv.current_state}

            if text_lower in HELP_COMMANDS and conv.current_state not in (
                "WAITING_PAYMENT", "PENDING_PAYMENT"
            ):
                help_msg = (
                    "*Shafsky Aviation Services*\n\n"
                    f"{wa_copy.SESSION_TIMEOUT_HINT}\n\n"
                    "• Type *Hi* to restart your booking.\n"
                    "• Type *BACK* to return to the previous step.\n"
                    "• Type *CANCEL* to cancel your current booking.\n"
                    "• Reply with a number to choose a listed service.\n\n"
                    "Airport services: 12 hours' notice for domestic, 24 hours for international.\n"
                    "For urgent assistance, call our executive on *+91-9599087959*."
                )
                wa_delivery.send_text(conv.phone_number, help_msg, client=whatsapp_client)
                return {"status": "help_sent", "state": conv.current_state}

            # Quote-request state: the team owes the customer a price, so there is
            # no payment link to resend and no menu to route to.
            if conv.current_state == "AWAITING_QUOTE":
                if text_lower in RESTART_COMMANDS or input_id in RESTART_BUTTON_IDS:
                    conv = cls._reset_conversation_fields(conv, db)
                    cls._transition_state(db, conv, "CATEGORY_SELECTION")
                    db.commit()
                    return cls._send_category_menu(db, conv)
                wa_delivery.send_text(
                    conv.phone_number,
                    wa_copy.QUOTE_REQUEST_PENDING_ACK.format(
                        booking_ref=conv.booking_ref or "N/A"
                    ),
                    client=whatsapp_client,
                )
                return {"status": "quote_pending_ack", "success": True}

            # Payment-pending state: handle specially before any restart/state routing
            if conv.current_state in ("WAITING_PAYMENT", "PENDING_PAYMENT"):
                # "1"/"2" only mean Continue/Cancel while the options prompt is live.
                # Outside that window a stray "2" must not cancel a real booking.
                options_active = cls._payment_options_active(conv)

                if (
                    input_id == "btn_cancel_start_new"
                    or text_lower in ("cancel & start new", "cancel and start new")
                    or (options_active and text_lower == "2")
                ):
                    conv = cls._reset_conversation_fields(conv, db)
                    cls._transition_state(db, conv, "CATEGORY_SELECTION")
                    db.commit()
                    return cls._send_category_menu(
                        db, conv,
                        prefix_notice="Previous booking cancelled. Let's start fresh.\n\n"
                    )

                # Handle "Continue Payment" button
                if (
                    input_id == "btn_continue_payment"
                    or text_lower in ("continue payment", "continue")
                    or (options_active and text_lower == "1")
                ):
                    cls._set_wa_state_key(db, conv, "payment_options_prompted_at", None)
                    return cls._state_waiting_payment(db, conv, "resend", input_id)

                # Restart commands (Hi, Hello, etc.) while payment pending
                if text_lower in RESTART_COMMANDS or input_id in RESTART_BUTTON_IDS:
                    return cls._handle_pending_payment_restart(db, conv)

                # All other input while waiting payment: delegate to payment handler
                return cls._state_waiting_payment(db, conv, user_input, input_id)

            # 2. Global Restart Commands (Hi, Hello, Start, Menu, 0)
            if text_lower in RESTART_COMMANDS or input_id in RESTART_BUTTON_IDS:
                logger.info(
                    "[WhatsApp Session] Restart command detected for %s (input: '%s')",
                    conv.phone_number,
                    user_input,
                )
                conv = cls._reset_conversation_fields(conv, db)
                cls._transition_state(db, conv, "CATEGORY_SELECTION")
                result = cls._send_category_menu(db, conv)
                if not result.get("success") and result.get("status") != "category_menu_sent":
                    logger.warning(
                        "[WhatsApp Session] Failed to send category menu for %s",
                        conv.phone_number,
                    )
                    cls._send_fallback_message(
                        conv.phone_number,
                        "Welcome to Shafsky Aviation Services. Please select a service category.",
                    )
                return result

            if text_lower == "back" or input_id == "btn_back":
                cls._handle_back_action(db, conv)
                return {"status": "back", "state": conv.current_state}

            # 3. Route by State
            state = conv.current_state

            if state in ["START", "CANCELLED", "BOOKING_CONFIRMED"]:
                result = cls._state_start(db, conv, user_input)
            elif state == "CATEGORY_SELECTION":
                result = cls._state_category_selection(db, conv, user_input, input_id)
            elif state in ["JOURNEY_TYPE_SELECTION", "AIRPORT_JOURNEY_TYPE"]:
                result = cls._state_journey_type_selection(db, conv, user_input, input_id)
            elif state == "AIRPORT_TRAVEL_TYPE":
                result = cls._state_travel_type_selection(db, conv, user_input, input_id)
            elif state == "AIRPORT_TRANSIT_TYPE":
                result = cls._state_transit_type_selection(db, conv, user_input, input_id)
            elif state == "AIRPORT_SELECTION":
                result = cls._state_airport_selection(db, conv, user_input)
            elif state == "AIRPORT_CONFIRMATION":
                result = cls._state_airport_confirmation(db, conv, user_input, input_id)
            elif state == "TERMINAL_SELECTION":
                result = cls._state_terminal_selection(db, conv, user_input, input_id)
            elif state in ["SERVICE_SELECTION", "AIRPORT_PACKAGE_SELECTION"]:
                result = cls._state_service_selection(db, conv, user_input, input_id)
            elif state == "TRAVEL_SERVICE_SELECTION":
                result = cls._state_travel_service_selection(db, conv, user_input, input_id)
            elif state == "TRAVEL_ENQUIRY":
                result = cls._state_travel_enquiry(db, conv, user_input, input_id)
            elif state == "HOTEL_TRANSPORT_SUBMENU":
                result = cls._state_hotel_transport_submenu(db, conv, user_input, input_id)
            elif state == "CHARTER_ORIGIN":
                result = cls._state_charter_origin(db, conv, user_input)
            elif state == "CHARTER_DESTINATION":
                result = cls._state_charter_destination(db, conv, user_input)
            elif state == "TRANSPORT_PICKUP":
                result = cls._state_transport_pickup(db, conv, user_input)
            elif state == "TRANSPORT_DROPOFF":
                result = cls._state_transport_dropoff(db, conv, user_input)
            elif state == "HOTEL_CITY":
                result = cls._state_hotel_city(db, conv, user_input)
            elif state == "HOTEL_NIGHTS":
                result = cls._state_hotel_nights(db, conv, user_input)
            elif state == "FLIGHT_INPUT":
                result = cls._state_flight_input(db, conv, user_input, input_id)
            elif state == "FLIGHT_TYPE_MISMATCH":
                result = cls._state_flight_type_mismatch(db, conv, user_input, input_id)
            elif state == "FLIGHT_CONFIRMATION":
                result = cls._state_flight_confirmation(db, conv, user_input, input_id)
            elif state == "DATE_SELECTION":
                result = cls._state_date_selection(db, conv, user_input)
            elif state == "PASSENGER_COUNT":
                result = cls._state_passenger_count(db, conv, user_input)
            elif state == "CUSTOMER_NAME":
                result = cls._state_customer_name(db, conv, user_input)
            elif state == "CUSTOMER_EMAIL":
                result = cls._state_customer_email(db, conv, user_input)
            elif state == "CUSTOMER_PHONE":
                result = cls._state_customer_phone(db, conv, user_input)
            elif state == "COMPANION_NAMES":
                result = cls._state_companion_names(db, conv, user_input)
            elif state == "ADDITIONAL_REQUIREMENTS":
                result = cls._state_additional_requirements(db, conv, user_input)
            elif state == "BOOKING_REVIEW":
                result = cls._state_booking_review(db, conv, user_input, input_id)
            else:
                result = cls._state_start(db, conv, user_input)

            if not result.get("success") and result.get("status") not in [
                "invalid_category",
                "invalid_journey_type",
                "invalid_travel_type",
                "invalid_transit_type",
                "invalid_terminal",
                "invalid_service",
                "invalid_travel_service",
                "invalid_travel_enquiry_answer",
                "travel_enquiry_failed",
                "invalid_flight_format",
                "invalid_flight_confirmation",
                "flight_type_mismatch",
                "invalid_mismatch_choice",
                "flight_airport_mismatch",
                "flight_not_found",
                "flight_verify_failed",
                "flight_verify_timeout",
                "flight_verify_rate_limited",
                "flight_verify_not_configured",
                "flight_reverify_required",
                "flight_incomplete",
                "flight_ambiguous",
                "unverified_flight_blocked",
                "booking_cutoff_blocked",
                "invalid_date_format",
                "past_date_rejected",
                "cutoff_violation",
                "invalid_passenger_count",
                "invalid_name",
                "invalid_email",
                "invalid_phone",
                "invalid_summary_choice",
                "empty_airport_query",
                "unsupported_airport",
                "payment_link_failed",
                "payment_not_found",
                "payment_still_pending",
                "missing_booking",
                "already_confirmed",
                "reprompt_flight",
                "reprompt_airport",
                "edit_prompt",
                "companion_names_partial",
                "invalid_companion_names",
                "companion_names_pending_ops",
                "companion_names_saved",
                "back_to_phone",
            ]:
                logger.warning(
                    "[WhatsApp Session] State handler returned unsuccessful result for %s in state %s",
                    conv.phone_number,
                    state,
                )
                cls._send_fallback_message(
                    conv.phone_number,
                    (
                        "Please select an option from the menu above, "
                        "or type *BACK* to return to the previous step."
                    ),
                )

            return result

        except Exception as e:  # pylint: disable=broad-exception-caught
            # Rollback any dirty DB session state before sending fallback
            try:
                db.rollback()
            except Exception:  # pylint: disable=broad-exception-caught
                pass
            logger.error(
                "[WhatsApp Session] Exception in process_incoming_event for phone=%s msg_id=%s: %s",
                from_phone,
                msg_id,
                str(e),
                exc_info=True,
            )
            cls._send_fallback_message(
                from_phone,
                (
                    "I encountered an issue processing your request. Please reply with "
                    "your selection, or type *BACK* to return to the previous step."
                ),
            )
            return {"status": "error", "error": str(e)}

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
        return age <= _PAYMENT_OPTIONS_TTL_SECONDS

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

