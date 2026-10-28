"""
Travel Services Flow Mixin for the WhatsApp Booking State Machine.

Travel Services offers exactly two options — Visa Assistance and Flight Ticketing —
and each one collects the fields of the matching website enquiry form, one step at a
time, then submits through ``BookingService.create_service_enquiry`` (the same
service behind ``POST /api/bookings/enquiries`` that the website forms call).

States owned by this mixin:
- ``TRAVEL_SERVICE_SELECTION`` — the two-option Travel Services menu
- ``TRAVEL_ENQUIRY`` — field collection, review and submission for either service

Session behaviour (15-minute inactivity, BACK, CANCEL, HELP, HI) stays in
``WhatsAppBookingStateMachine``; every answer lives in
``whatsapp_state_json["travel_enquiry"]`` so ``_reset_conversation_fields`` wipes it
and a new enquiry can never inherit a previous one's data.
"""

import logging
import uuid
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app.models.whatsapp_models import WhatsAppConversation
from app.integrations.whatsapp.client import whatsapp_client
from app.integrations.whatsapp import copy as wa_copy
from app.integrations.whatsapp import delivery as wa_delivery
from app.integrations.whatsapp import travel_enquiries as te

logger = logging.getLogger(__name__)

ENQUIRY_STATE_KEY = "travel_enquiry"

_CONFIRM_COMMANDS = {"confirm", "submit", "submit enquiry", "1", "yes", "ok"}
_EDIT_COMMANDS = {"edit", "change", "change details", "2"}


class TravelServicesFlowMixin:
    """Mixin for the Travel Services menu and its two enquiry flows."""

    # -- infrastructure ---------------------------------------------------

    @classmethod
    def _enquiry_transition(cls, db: Session, conv: WhatsAppConversation, new_state: str) -> None:
        """Resolve the state transition helper even when used outside the full machine."""
        transition = getattr(cls, "_transition_state", None)
        if transition is None:
            from app.integrations.whatsapp.service import WhatsAppBookingStateMachine

            transition = WhatsAppBookingStateMachine._transition_state
        transition(db, conv, new_state)

    @classmethod
    def _load_enquiry_state(cls, conv: WhatsAppConversation) -> Optional[Dict[str, Any]]:
        state = conv.whatsapp_state_json if isinstance(conv.whatsapp_state_json, dict) else {}
        enquiry = state.get(ENQUIRY_STATE_KEY)
        return dict(enquiry) if isinstance(enquiry, dict) else None

    @classmethod
    def _save_enquiry_state(
        cls, db: Session, conv: WhatsAppConversation, enquiry: Optional[Dict[str, Any]]
    ) -> None:
        state = dict(conv.whatsapp_state_json) if isinstance(conv.whatsapp_state_json, dict) else {}
        if enquiry is None:
            state.pop(ENQUIRY_STATE_KEY, None)
        else:
            state[ENQUIRY_STATE_KEY] = enquiry
        conv.whatsapp_state_json = state
        flag_modified(conv, "whatsapp_state_json")
        db.commit()

    @classmethod
    def _sync_conversation(cls, conv: WhatsAppConversation, service: str, answers: Dict[str, Any]) -> None:
        """Keep the conversation columns ops already reads in step with the answers."""
        for field, value in te.conversation_snapshot(service, answers).items():
            setattr(conv, field, value)

    # -- Travel Services menu --------------------------------------------

    @classmethod
    def _send_travel_services_menu(cls, db: Session, conv: WhatsAppConversation) -> Dict[str, Any]:
        """The Travel Services menu: Visa Assistance and Flight Ticketing only."""
        body_text = wa_copy.TRAVEL_SERVICES_BODY
        buttons = [
            {"id": "btn_travel_visa", "title": "Visa Assistance"},
            {"id": "btn_travel_ticketing", "title": "Flight Ticketing"},
        ]
        # Travel Services never needs an airport, flight, travel date or headcount
        # from the airport booking machine.
        conv.requires_airport = False
        conv.requires_flight = False
        conv.requires_date = False
        conv.requires_passenger_count = False
        cls._enquiry_transition(db, conv, "TRAVEL_SERVICE_SELECTION")
        wa_delivery.send_buttons(
            phone=conv.phone_number,
            body_text=body_text,
            buttons=buttons,
            header_text="Travel Services",
            fallback_text=body_text + "\n\nReply *1* for Visa Assistance or *2* for Flight Ticketing.",
            client=whatsapp_client,
        )
        return {"status": "travel_services_menu_sent", "success": True}

    @classmethod
    def _send_service_menu_for_category(
        cls, db: Session, conv: WhatsAppConversation, category_name: Optional[str]
    ) -> Dict[str, Any]:
        """Route a category to its menu, keeping Travel Services on the two-option menu."""
        if (category_name or "").strip().lower() == "travel services":
            return cls._send_travel_services_menu(db, conv)
        return cls._send_service_menu(db, conv, category_name or "Airport Services")

    @classmethod
    def _state_travel_service_selection(
        cls,
        db: Session,
        conv: WhatsAppConversation,
        user_text: str,
        input_id: Optional[str],
    ) -> Dict[str, Any]:
        """Handles the Visa Assistance / Flight Ticketing choice."""
        norm = (input_id or user_text or "").strip().upper()

        service = None
        if norm in {"1", "BTN_TRAVEL_VISA", "VISA", "VISA ASSISTANCE"} or "VISA" in norm:
            service = te.VISA
        elif (
            norm in {"2", "BTN_TRAVEL_TICKETING", "TICKETING", "FLIGHT TICKETING"}
            or "TICKET" in norm
            or "FLIGHT" in norm
        ):
            service = te.TICKETING

        if not service:
            wa_delivery.send_text(
                conv.phone_number,
                "*Travel Services*\n\nPlease reply *1* for Visa Assistance or *2* for Flight Ticketing.",
                client=whatsapp_client,
            )
            return {"status": "invalid_travel_service", "success": False}

        return cls._start_travel_enquiry(db, conv, service)

    # -- enquiry lifecycle ------------------------------------------------

    @classmethod
    def _start_travel_enquiry(cls, db: Session, conv: WhatsAppConversation, service: str) -> Dict[str, Any]:
        """Reset any previous enquiry and ask the first website-form field."""
        title = te.SERVICE_TITLES.get(service, "Travel Services")
        conv.selected_category = "Travel Services"
        conv.selected_service_name = title
        conv.requires_airport = False
        conv.requires_flight = False
        conv.requires_date = False
        conv.requires_passenger_count = False
        # Never let a previous enquiry's dates or headcount leak into this one.
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

        state = {
            "service": service,
            "phase": "collect",
            "index": 0,
            "answers": {},
            "submitted_ref": None,
            "attempt_id": uuid.uuid4().hex,
        }
        cls._save_enquiry_state(db, conv, state)
        cls._enquiry_transition(db, conv, "TRAVEL_ENQUIRY")

        wa_delivery.send_text(
            conv.phone_number,
            wa_copy.TRAVEL_ENQUIRY_INTRO.format(service=title),
            client=whatsapp_client,
        )
        steps = te.plan(service, {})
        return cls._prompt_enquiry_step(db, conv, service, steps, 0, {})

    @classmethod
    def _prompt_enquiry_step(
        cls,
        db: Session,
        conv: WhatsAppConversation,
        service: str,
        steps: List[te.Step],
        index: int,
        answers: Dict[str, Any],
        prefix: str = "",
    ) -> Dict[str, Any]:
        if not steps:
            return cls._send_travel_services_menu(db, conv)
        index = max(0, min(index, len(steps) - 1))
        step = steps[index]
        lines = [f"*{te.SERVICE_TITLES.get(service, 'Travel Services')}* — step {index + 1} of {len(steps)}"]
        if prefix:
            lines.extend(["", prefix])
        lines.extend(["", step.prompt])
        if step.options:
            lines.extend(["", te.option_lines(step)])
        wa_delivery.send_text(conv.phone_number, "\n".join(lines), client=whatsapp_client)
        return {"status": "travel_enquiry_step_sent", "success": True, "step": step.key}

    @classmethod
    def _state_travel_enquiry(
        cls,
        db: Session,
        conv: WhatsAppConversation,
        user_text: str,
        input_id: Optional[str],
    ) -> Dict[str, Any]:
        """Collects one answer, advances the form, or handles the review prompt."""
        state = cls._load_enquiry_state(conv)
        if not state or not state.get("service"):
            # Session lost the enquiry block (e.g. expired) — restart cleanly.
            return cls._send_travel_services_menu(db, conv)

        service = str(state.get("service"))
        answers = dict(state.get("answers") or {})
        phase = str(state.get("phase") or "collect")

        if phase == "submitted":
            # A repeated Confirm must never create a second enquiry.
            wa_delivery.send_text(
                conv.phone_number,
                wa_copy.QUOTE_REQUEST_PENDING_ACK.format(
                    booking_ref=state.get("submitted_ref") or conv.booking_ref or "N/A"
                ),
                client=whatsapp_client,
            )
            return {"status": "enquiry_already_submitted", "success": True}

        steps = te.plan(service, answers)
        index = int(state.get("index") or 0)

        if phase == "review" or not steps or index >= len(steps):
            if phase != "review":
                state.update({"phase": "review", "index": len(steps)})
                cls._save_enquiry_state(db, conv, state)
                return cls._send_enquiry_review(db, conv, service, answers)
            return cls._handle_enquiry_review(db, conv, service, answers, user_text, input_id)

        step = steps[index]
        ok, value, error = te.validate_answer(step, user_text, answers, service, conv.phone_number)
        if not ok:
            return cls._prompt_enquiry_step(
                db,
                conv,
                service,
                steps,
                index,
                answers,
                prefix=f"⚠️ {error}",
            )

        answers[step.key] = value
        state["answers"] = answers
        state["index"] = index + 1
        state["phase"] = "collect"
        cls._sync_conversation(conv, service, answers)

        next_steps = te.plan(service, answers)
        if state["index"] >= len(next_steps):
            state["phase"] = "review"
            state["index"] = len(next_steps)
            cls._save_enquiry_state(db, conv, state)
            return cls._send_enquiry_review(db, conv, service, answers)

        cls._save_enquiry_state(db, conv, state)
        return cls._prompt_enquiry_step(db, conv, service, next_steps, state["index"], answers)

    @classmethod
    def _send_enquiry_review(
        cls, db: Session, conv: WhatsAppConversation, service: str, answers: Dict[str, Any]
    ) -> Dict[str, Any]:
        title = te.SERVICE_TITLES.get(service, "Travel Services")
        wa_delivery.send_text(
            conv.phone_number,
            te.build_review(service, answers),
            client=whatsapp_client,
        )
        body_text = "Ready to send this enquiry to our team?"
        buttons = [
            {"id": "btn_enquiry_confirm", "title": "Submit Enquiry"},
            {"id": "btn_enquiry_edit", "title": "Change Details"},
            {"id": "btn_cancel", "title": "Cancel"},
        ]
        wa_delivery.send_buttons(
            phone=conv.phone_number,
            body_text=body_text,
            buttons=buttons,
            header_text=title,
            fallback_text=body_text + "\n\nReply *1* to submit, *2* to change details, or *CANCEL* to stop.",
            client=whatsapp_client,
        )
        return {"status": "travel_enquiry_review_sent", "success": True}

    @classmethod
    def _handle_enquiry_review(
        cls,
        db: Session,
        conv: WhatsAppConversation,
        service: str,
        answers: Dict[str, Any],
        user_text: str,
        input_id: Optional[str],
    ) -> Dict[str, Any]:
        norm = (user_text or "").strip().lower()

        if input_id == "btn_enquiry_confirm" or norm in _CONFIRM_COMMANDS:
            return cls._submit_travel_enquiry(db, conv)

        if input_id == "btn_enquiry_edit" or norm in _EDIT_COMMANDS:
            state = cls._load_enquiry_state(conv) or {}
            state.update({"phase": "collect", "index": 0})
            cls._save_enquiry_state(db, conv, state)
            steps = te.plan(service, answers)
            return cls._prompt_enquiry_step(db, conv, service, steps, 0, answers)

        # Anything else: repeat the review prompt instead of guessing.
        return cls._send_enquiry_review(db, conv, service, answers)

    @classmethod
    def _submit_travel_enquiry(cls, db: Session, conv: WhatsAppConversation) -> Dict[str, Any]:
        """Persist the enquiry through the website's enquiry service."""
        from fastapi import HTTPException

        from app.services.booking_service import BookingService

        state = cls._load_enquiry_state(conv)
        if not state or not state.get("service"):
            return cls._send_travel_services_menu(db, conv)

        service = str(state.get("service"))
        answers = dict(state.get("answers") or {})

        if state.get("submitted_ref") or state.get("phase") == "submitted":
            wa_delivery.send_text(
                conv.phone_number,
                wa_copy.QUOTE_REQUEST_PENDING_ACK.format(
                    booking_ref=state.get("submitted_ref") or conv.booking_ref or "N/A"
                ),
                client=whatsapp_client,
            )
            return {"status": "enquiry_already_submitted", "success": True}

        payload = te.build_submission(service, answers)
        try:
            booking = BookingService.create_service_enquiry(db, **payload)
        except HTTPException as err:
            db.rollback()
            detail = str(getattr(err, "detail", "") or "")
            logger.warning(
                "[WhatsApp Travel Enquiry] Rejected for %s: %s", conv.phone_number, detail
            )
            wa_delivery.send_text(
                conv.phone_number,
                wa_copy.TRAVEL_ENQUIRY_FAILED + (f"\n\n_{detail}_" if detail else ""),
                client=whatsapp_client,
            )
            return {"status": "travel_enquiry_failed", "success": False, "error": detail}
        except Exception as err:  # pylint: disable=broad-exception-caught
            db.rollback()
            logger.error(
                "[WhatsApp Travel Enquiry] Submission failed for %s: %s",
                conv.phone_number,
                type(err).__name__,
                exc_info=True,
            )
            wa_delivery.send_text(
                conv.phone_number,
                wa_copy.TRAVEL_ENQUIRY_FAILED,
                client=whatsapp_client,
            )
            return {"status": "travel_enquiry_failed", "success": False, "error": str(err)}

        # Mark the enquiry submitted before replying to the customer, so a double tap
        # or a Meta redelivery can never create a second enquiry. Answers now live on
        # the Booking row, so the session keeps only the reference.
        state.update({"phase": "submitted", "submitted_ref": booking.booking_ref, "answers": {}})
        cls._save_enquiry_state(db, conv, state)

        conv.booking_id = booking.id
        conv.booking_ref = booking.booking_ref
        conv.payment_status = "QUOTE_REQUESTED"
        conv.total_amount = 0
        db.commit()

        cls._tag_whatsapp_channel(db, booking)
        cls._enquiry_transition(db, conv, "AWAITING_QUOTE")

        wa_delivery.send_text(
            conv.phone_number,
            wa_copy.TRAVEL_ENQUIRY_REGISTERED.format(
                service=te.SERVICE_TITLES.get(service, "Travel Services"),
                booking_ref=booking.booking_ref,
            ),
            client=whatsapp_client,
        )
        logger.info(
            "[WhatsApp Travel Enquiry] %s enquiry %s registered for %s",
            service,
            booking.booking_ref,
            conv.phone_number,
        )
        return {
            "status": "travel_enquiry_registered",
            "service": service,
            "booking_ref": booking.booking_ref,
            "success": True,
        }

    @classmethod
    def _tag_whatsapp_channel(cls, db: Session, booking: Any) -> None:
        """Mark the intake channel on the row we just created (website rows untouched)."""
        try:
            metadata = dict(booking.metadata_json or {})
            metadata["source"] = "whatsapp"
            metadata["channel"] = "whatsapp"
            booking.metadata_json = metadata
            flag_modified(booking, "metadata_json")
            db.commit()
        except Exception as err:  # pylint: disable=broad-exception-caught
            db.rollback()
            logger.warning("[WhatsApp Travel Enquiry] Channel tag skipped: %s", type(err).__name__)

    @classmethod
    def _travel_enquiry_back(cls, db: Session, conv: WhatsAppConversation) -> Optional[Dict[str, Any]]:
        """BACK inside an enquiry: previous field, or the Travel Services menu."""
        state = cls._load_enquiry_state(conv)
        if not state or not state.get("service"):
            return cls._send_travel_services_menu(db, conv)

        service = str(state.get("service"))
        answers = dict(state.get("answers") or {})
        steps = te.plan(service, answers)
        if not steps:
            return cls._send_travel_services_menu(db, conv)

        if state.get("phase") == "review":
            index = len(steps) - 1
        else:
            index = int(state.get("index") or 0) - 1

        if index < 0:
            cls._save_enquiry_state(db, conv, None)
            return cls._send_travel_services_menu(db, conv)

        state.update({"phase": "collect", "index": index})
        cls._save_enquiry_state(db, conv, state)
        return cls._prompt_enquiry_step(db, conv, service, steps, index, answers)
