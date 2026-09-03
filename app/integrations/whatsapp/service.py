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
from app.integrations.whatsapp.handlers import (
    AirportFlowMixin,
    FlightFlowMixin,
    DetailsFlowMixin,
    CharterHotelFlowMixin,
    ReviewPaymentFlowMixin,
    OFFICIAL_CATEGORIES,
)

logger = logging.getLogger(__name__)

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
    AirportFlowMixin,
    FlightFlowMixin,
    DetailsFlowMixin,
    CharterHotelFlowMixin,
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
                    clean_phone,
                )
            else:
                logger.info("[WhatsApp Session] New session for %s", clean_phone)
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
                        clean_phone,
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
                        clean_phone,
                        conv.current_state,
                    )
            else:
                logger.info(
                    "[WhatsApp Session] Existing session for %s (state: %s)",
                    clean_phone,
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
    def _send_fallback_message(cls, phone_number: str, message: str) -> None:
        """Sends a simple text fallback message when interactive messages fail."""
        wa_delivery.send_text(phone_number, message, client=whatsapp_client)

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
    def _store_wa_menu(cls, db: Session, conv: WhatsAppConversation, items: list) -> None:
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

        cls._set_wa_state_key(db, conv, "_wa_menu", _json_safe(list(items)))

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
                cls._send_service_menu(db, conv, conv.selected_category or "Airport Services")
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
                    cls._send_service_menu(db, conv, conv.selected_category or "Airport Services")
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
        elif curr == "ADDITIONAL_REQUIREMENTS":
            conv.additional_requirements = None
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
        try:
            wa_delivery.send_text(phone_number, message, client=whatsapp_client)
        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.error(
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
            stale_cutoff = now - timedelta(seconds=_CLAIM_STALE_SECONDS)
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

        Clearing processing_worker_id/processing_started_at is what allows Meta's
        next delivery of the same wamid to reclaim immediately. Leaving the claim
        in place would make the retry look like an in-flight event for
        _CLAIM_STALE_SECONDS, and the message would be dropped.
        """
        if not msg_id:
            return
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
        clean = cls._normalize_phone(phone_number)
        lock_name = f"whatsapp:conv:{clean}"
        deadline = time.time() + cls._CONV_LOCK_WAIT_SECONDS

        while time.time() < deadline:
            token = RedisDistributedLock.acquire_lock(lock_name, ttl_seconds=cls._CONV_LOCK_TTL)
            if token:
                return {"token": token, "phone": clean}
            time.sleep(0.05)

        logger.warning("[WhatsApp] Conversation lock unavailable for %s", clean)
        return {"token": None, "phone": clean}

    @classmethod
    def _release_conversation_lock(cls, lock_state: Optional[Dict[str, Any]]) -> None:
        if not lock_state:
            return
        phone = lock_state.get("phone") or "unknown"
        token = lock_state.get("token")
        lock_name = f"whatsapp:conv:{phone}"
        if token:
            RedisDistributedLock.release_lock(lock_name, token)

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
                        logger.info(
                            "[WhatsApp Webhook Status] Message %s status: %s",
                            st.get("id"),
                            st.get("status"),
                        )

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
                                from_phone, wa_copy.UNSUPPORTED_INBOUND
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
                                res = WhatsAppBookingStateMachine.process_incoming_event(
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
    try:
        if not whatsapp_client.is_configured():
            return
        officer_phone = os.getenv("WHATSAPP_OFFICER_NOTIFY_PHONE", "919599087959").strip()
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
