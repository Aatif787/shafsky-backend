"""
Review & Payment Flow Mixin for WhatsApp Booking State Machine.
Handles:
- Booking review verification and email guard
- Booking request persistence in database
- Cutoff notice re-validation before link creation
- Razorpay Payment Link generation, idempotency, and resend
- Officer notification
- Waiting payment handling, reconciliation ("I paid"), and completion
- Payment success callback handler
"""

import os
import uuid
import logging
from typing import Dict, Any, Optional
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified
from sqlalchemy import select

from app.models.whatsapp_models import WhatsAppConversation
from app.models.schema import Booking, BookingStatus
from app.integrations.whatsapp.client import whatsapp_client
from app.utils.customer_email import (
    is_acceptable_customer_email as is_acceptable_whatsapp_customer_email,
    REAL_EMAIL_HELP as _REAL_EMAIL_HELP,
)

from app.integrations.whatsapp.handlers.base import BaseFlowMixin

logger = logging.getLogger(__name__)


class ReviewPaymentFlowMixin(BaseFlowMixin):
    """Mixin for booking confirmation, Razorpay payment link issuance, and reconciliation."""

    @classmethod
    def _state_booking_review(cls, db: Session, conv: WhatsAppConversation, user_text: str, input_id: Optional[str]) -> Dict[str, Any]:
        """Handles booking confirmation or change details."""
        text_u = (input_id or user_text).strip().upper()

        if "CHANGE" in text_u or "EDIT" in text_u or text_u == "btn_change_details":
            cls._transition_state(db, conv, "CUSTOMER_NAME")
            whatsapp_client.send_text_message(conv.phone_number, "Let's update your details. May I have your full name?")
            return {"status": "edit_prompt", "success": True}

        if "CONFIRM" in text_u or "YES" in text_u or text_u == "1" or text_u == "btn_confirm_booking":
            email_ok, _ = is_acceptable_whatsapp_customer_email(conv.customer_email or "")
            if not email_ok:
                cls._transition_state(db, conv, "CUSTOMER_EMAIL")
                whatsapp_client.send_text_message(conv.phone_number, _REAL_EMAIL_HELP)
                return {"status": "invalid_email", "success": False, "reason": "reserved_or_placeholder"}
            if (conv.selected_category or "").strip().lower() == "private charter":
                # Private Charter is enquiry-only - no booking/payment pipeline.
                return cls._submit_charter_enquiry(db, conv)
            return cls._create_booking_request(db, conv)

        whatsapp_client.send_text_message(conv.phone_number, "Please select *Confirm Booking*, *Change Details*, or *Cancel*.")
        return {"status": "invalid_summary_choice", "success": False}

    @classmethod
    def _create_booking_request(cls, db: Session, conv: WhatsAppConversation) -> Dict[str, Any]:
        """
        Creates the Booking as PENDING, then creates/reuses a Razorpay Payment Link.
        Does not confirm the booking. Confirmation happens only via PaymentService webhooks.
        """
        if conv.booking_ref:
            return cls._issue_or_resend_payment_link(db, conv)

        metadata = conv.flight_details_json if isinstance(conv.flight_details_json, dict) else {}
        flight_later = metadata.get("flight_later") is True or metadata.get("verification_status") == "flight_later"
        if conv.requires_flight and not flight_later:
            status = metadata.get("verification_status")
            mismatch_ok = (
                status == "mismatch_customer_confirmed"
                and metadata.get("mismatch_override") is True
                and metadata.get("verification_api_performed") is True
            )
            verified_ok = status == "verified"
            accepted = (
                (verified_ok or mismatch_ok)
                and metadata.get("verification_provider")
                and conv.flight_num
                and metadata.get("origin_iata")
                and metadata.get("destination_iata")
            )
            if not accepted:
                conv.flight_num = None
                cls._transition_state(db, conv, "FLIGHT_INPUT")
                whatsapp_client.send_text_message(
                    conv.phone_number,
                    "We need a verified flight number before creating your booking. "
                    "Please enter your Flight Number (e.g., *EK501*, *AI2424*, *6E224*):",
                )
                return {"status": "unverified_flight_blocked", "success": False}

            from app.services.booking_cutoff import (
                evaluate_booking_cutoff,
                lookup_airport_timezone,
                service_leg_scheduled_datetime,
                airport_tzinfo,
            )
            from app.services.service_airport_rules import derive_flight_type_from_route

            jt_cut = metadata.get("journey_type", "DEPARTURE")
            origin_for_route = metadata.get("origin_iata") or metadata.get("departure_iata")
            dest_for_route = metadata.get("destination_iata") or metadata.get("arrival_iata")
            cutoff_type = None
            if jt_cut != "TRANSIT":
                try:
                    cutoff_type = derive_flight_type_from_route(db, origin_for_route, dest_for_route, jt_cut)
                except (ValueError, Exception):
                    cutoff_type = None

            # Defensive check: ensure verified route type matches selected travel type
            selected_tt_def = (metadata.get("travel_type") or "DOMESTIC").upper()
            if (
                jt_cut != "TRANSIT"
                and cutoff_type in ("DOMESTIC", "INTERNATIONAL")
                and selected_tt_def in ("DOMESTIC", "INTERNATIONAL")
                and cutoff_type != selected_tt_def
            ):
                logger.error(
                    f"[Booking Creation Blocked] Flight type mismatch: selected={selected_tt_def}, verified={cutoff_type}"
                )
                conv.flight_num = None
                cls._transition_state(db, conv, "FLIGHT_INPUT")
                whatsapp_client.send_text_message(
                    conv.phone_number,
                    "⚠️ Inconsistent booking: Your flight route does not match the selected service type. "
                    "Please enter your flight number again."
                )
                return {"status": "flight_type_mismatch_blocked", "success": False}

            tz_name = lookup_airport_timezone(db, conv.selected_airport_iata)
            tz = airport_tzinfo(tz_name)

            if conv.booking_date:
                try:
                    b_date = None
                    for fmt in ("%d %B %Y", "%d %b %Y", "%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d"):
                        try:
                            b_date = datetime.strptime(conv.booking_date.strip(), fmt).date()
                            break
                        except Exception:
                            continue
                    if b_date:
                        metadata = cls._align_scheduled_datetimes_to_date(metadata, b_date, tz)
                        conv.flight_details_json = metadata
                        flag_modified(conv, "flight_details_json")
                except Exception:
                    pass

            scheduled = service_leg_scheduled_datetime(
                jt_cut,
                metadata.get("departure_scheduled"),
                metadata.get("arrival_scheduled"),
                tz,
            )
            cutoff_eval = evaluate_booking_cutoff(
                scheduled_dt=scheduled,
                now_utc=datetime.now(timezone.utc),
                airport_tz_name=tz_name,
                flight_type=cutoff_type or metadata.get("travel_type") or metadata.get("flight_type"),
            )
            if not cutoff_eval.allowed:
                whatsapp_client.send_text_message(conv.phone_number, cutoff_eval.customer_message)
                return {
                    "status": "booking_cutoff_blocked",
                    "success": False,
                    "reason": cutoff_eval.reason,
                    "state": conv.current_state,
                }

        from app.services.booking_service import BookingService

        booking_ref = BookingService.generate_booking_ref()
        passengers = max(1, conv.passenger_count or 1)
        amount = conv.total_amount or 0.0

        jt = metadata.get("journey_type") or "DEPARTURE"
        initial_tt = metadata.get("travel_type") or "DOMESTIC"

        authoritative_travel_type = initial_tt
        if jt != "TRANSIT":
            try:
                from app.services.service_airport_rules import derive_flight_type_from_route
                derived = derive_flight_type_from_route(
                    db,
                    metadata.get("origin_iata"),
                    metadata.get("destination_iata"),
                    jt
                )
                if derived:
                    authoritative_travel_type = derived
            except Exception:
                authoritative_travel_type = initial_tt

        # Resolve authoritative package price if amount is missing or zero
        if (amount <= 0 or not conv.selected_service_name) and conv.selected_airport_iata:
            try:
                from app.models.journey_models import SupportedAirport
                from app.integrations.whatsapp import copy as wa_copy
                airport_obj = db.execute(
                    select(SupportedAirport).where(SupportedAirport.iata_code == conv.selected_airport_iata)
                ).scalar_one_or_none()
                if airport_obj:
                    intl_or_dom = [authoritative_travel_type, "ALL"]
                    matching = cls._get_authoritative_airport_packages(
                        db, airport_obj.id, jt, intl_or_dom, terminal=metadata.get("terminal")
                    )
                    if matching:
                        current_tier = wa_copy._extract_tier_name(conv.selected_service_name or "").lower()
                        picked = None
                        for aps, s in matching:
                            if current_tier and current_tier in (s.name or "").lower():
                                picked = (aps, s)
                                break
                        if not picked:
                            picked = matching[0]
                        aps, s = picked
                        unit_p = float(aps.price)
                        conv.selected_service_id = str(s.id)
                        conv.selected_service_name = s.name
                        amount = unit_p * passengers
                        conv.total_amount = amount
            except Exception:
                pass

        from app.services.booking_cutoff import airport_tzinfo, lookup_airport_timezone, parse_scheduled_datetime

        booking_meta = {
            "channel": "whatsapp",
            "source": "whatsapp",
            "journey_type": jt,
            "travel_type": authoritative_travel_type,
            "flight_type": authoritative_travel_type,
            "service_airport": conv.selected_airport_iata,
            "package": conv.selected_service_name,
            "terminal": metadata.get("terminal"),
            "origin_iata": metadata.get("origin_iata"),
            "destination_iata": metadata.get("destination_iata"),
            "departure_scheduled": metadata.get("departure_scheduled"),
            "arrival_scheduled": metadata.get("arrival_scheduled"),
            "verification_status": metadata.get("verification_status"),
            "flight_later": bool(metadata.get("flight_later")),
            "mismatch_override": bool(metadata.get("mismatch_override")),
            "pax_adults": passengers,
            "guest_count": passengers,
            "airport_timezone": lookup_airport_timezone(db, conv.selected_airport_iata),
        }

        tz = airport_tzinfo(lookup_airport_timezone(db, conv.selected_airport_iata))
        dep_dt = parse_scheduled_datetime(metadata.get("departure_scheduled"), tz)
        arr_dt = parse_scheduled_datetime(metadata.get("arrival_scheduled"), tz)

        try:
            # 1. Reuse existing PENDING booking for this conversation if present
            existing_booking = None
            if conv.booking_id:
                existing_booking = db.scalar(
                    select(Booking).where(Booking.id == conv.booking_id).where(Booking.status == BookingStatus.PENDING)
                )

            if existing_booking:
                existing_booking.passenger_name = conv.customer_name or "Guest"
                existing_booking.passenger_email = conv.customer_email or "guest@shafsky.com"
                existing_booking.passenger_phone = conv.customer_phone or conv.phone_number
                existing_booking.service_category = conv.selected_category or "Airport Services"
                existing_booking.service_type = conv.selected_service_name or "VIP Service"
                existing_booking.origin_code = metadata.get("origin_iata") or conv.selected_airport_iata or "N/A"
                existing_booking.dest_code = metadata.get("destination_iata") or conv.selected_airport_iata or "N/A"
                existing_booking.flight_num = conv.flight_num or "N/A"
                existing_booking.departure_time = dep_dt
                existing_booking.arrival_time = arr_dt
                existing_booking.total_amount = amount
                existing_booking.notes = conv.additional_requirements or ""
                existing_booking.metadata_json = booking_meta
                existing_booking.updated_at = datetime.now(timezone.utc)
                booking_ref = existing_booking.booking_ref
                new_booking = existing_booking
            else:
                # 2. Cancel any stale uncompleted PENDING drafts for this customer phone
                caller_phone = conv.customer_phone or conv.phone_number
                if caller_phone:
                    clean_digits = "".join(filter(str.isdigit, caller_phone))
                    stale_bookings = db.scalars(
                        select(Booking)
                        .where(Booking.status == BookingStatus.PENDING)
                        .where(Booking.deleted_at.is_(None))
                    ).all()
                    for sb in stale_bookings:
                        sb_digits = "".join(filter(str.isdigit, sb.passenger_phone or ""))
                        if sb_digits and (sb_digits.endswith(clean_digits[-10:]) or clean_digits.endswith(sb_digits[-10:])):
                            sb.status = BookingStatus.CANCELLED
                            sb.notes = f"Superseded by new booking request {booking_ref}"
                            sb.updated_at = datetime.now(timezone.utc)

                new_booking = Booking(
                    id=uuid.uuid4(),
                    booking_ref=booking_ref,
                    passenger_name=conv.customer_name or "Guest",
                    passenger_email=conv.customer_email or "guest@shafsky.com",
                    passenger_phone=conv.customer_phone or conv.phone_number,
                    service_category=conv.selected_category or "Airport Services",
                    service_type=conv.selected_service_name or "VIP Service",
                    origin_code=metadata.get("origin_iata") or conv.selected_airport_iata or "N/A",
                    dest_code=metadata.get("destination_iata") or conv.selected_airport_iata or "N/A",
                    flight_num=conv.flight_num or "N/A",
                    departure_time=dep_dt,
                    arrival_time=arr_dt,
                    total_amount=amount,
                    currency="INR",
                    status=BookingStatus.PENDING,
                    notes=conv.additional_requirements,
                    metadata_json=booking_meta,
                    created_at=datetime.now(timezone.utc),
                    updated_at=datetime.now(timezone.utc)
                )
                db.add(new_booking)

            conv.booking_ref = booking_ref
            conv.booking_id = new_booking.id
            conv.total_amount = amount
            conv.payment_status = "PENDING"
            cls._transition_state(db, conv, "WAITING_PAYMENT")
            db.commit()

        except Exception as db_err:
            db.rollback()
            logger.error(f"[WhatsApp Booking] DB Error creating booking record: {db_err}")
            whatsapp_client.send_text_message(
                conv.phone_number,
                "⚠️ There was an error processing your booking request. Our team has been alerted and will assist you shortly."
            )
            return {"status": "error", "error": str(db_err), "success": False}

        try:
            from app.services.notification_service import NotificationService
            NotificationService.notify_booking_created(db, {
                "booking_ref": booking_ref,
                "passenger_name": conv.customer_name,
                "passenger_email": conv.customer_email,
                "passenger_phone": conv.customer_phone,
                "passenger_count": conv.passenger_count,
                "flight_num": conv.flight_num,
                "origin_code": metadata.get("origin_iata") or conv.selected_airport_iata,
                "dest_code": metadata.get("destination_iata"),
                "airport_code": conv.selected_airport_iata,
                "journey_type": metadata.get("journey_type"),
                "service_type": conv.selected_service_name,
                "departure_time": metadata.get("departure_scheduled") or conv.booking_date,
                "total_amount": amount,
                "currency": "INR",
                "status": "PENDING",
            })
        except Exception as email_err:
            logger.warning(f"[WhatsApp Booking Notification] Email notification failed: {email_err}")

        return cls._issue_or_resend_payment_link(db, conv, notify_officer=True)

    @classmethod
    def _payment_link_customer_message(cls, conv: WhatsAppConversation, short_url: str) -> str:
        metadata = conv.flight_details_json if isinstance(conv.flight_details_json, dict) else {}
        jt = metadata.get("journey_type")
        tt = metadata.get("travel_type")
        amount = conv.total_amount or 0.0
        lines = ["✅ *Booking Summary*\n"]
        lines.append(f"Service: {conv.selected_service_name or 'VIP Service'}")
        if conv.selected_airport_iata:
            airport_label = conv.selected_airport_name or conv.selected_airport_iata
            lines.append(f"Airport: {airport_label} ({conv.selected_airport_iata})")
        if jt:
            lines.append(f"Journey: {str(jt).replace('_', ' ').title()}")
        if tt:
            tt_display = "Domestic" if tt == "DOMESTIC" else ("International" if tt == "INTERNATIONAL" else str(tt).replace("_", " ").title())
            lines.append(f"Flight Type: {tt_display}")
        if conv.booking_date:
            lines.append(f"Date: {conv.booking_date}")
        if conv.booking_ref:
            lines.append(f"Reference: {conv.booking_ref}")
        lines.append(f"Total: ₹{int(amount):,}")
        lines.append("")
        lines.append("Please complete your payment using the secure Razorpay link below:")
        lines.append("")
        lines.append(short_url)
        lines.append("")
        lines.append("After payment, your booking will be confirmed automatically.")
        lines.append("")
        lines.append("Reply *resend* if you need the link again, or *I paid* to check payment status.")
        return "\n".join(lines)

    @classmethod
    def _issue_or_resend_payment_link(
        cls,
        db: Session,
        conv: WhatsAppConversation,
        force_new: bool = False,
        notify_officer: bool = False,
    ) -> Dict[str, Any]:
        from app.services.payment_service import PaymentService

        booking_ref = conv.booking_ref
        if not booking_ref:
            whatsapp_client.send_text_message(
                conv.phone_number,
                "We could not find your booking. Please type *Hi* to start again."
            )
            return {"status": "missing_booking", "success": False}

        booking = db.scalar(select(Booking).where(Booking.booking_ref == booking_ref))
        if booking and booking.status == BookingStatus.CONFIRMED:
            conv.payment_status = "SUCCESSFUL"
            conv.current_state = "COMPLETED"
            db.commit()
            whatsapp_client.send_text_message(
                conv.phone_number,
                f"✅ Payment received. Booking *{booking_ref}* is confirmed."
            )
            return {"status": "already_confirmed", "booking_ref": booking_ref, "success": True}

        flight_later_flag = isinstance(conv.flight_details_json, dict) and conv.flight_details_json.get("flight_later") is True
        if conv.requires_flight and not flight_later_flag:
            from app.services.booking_cutoff import evaluate_cutoff_for_booking
            cutoff = evaluate_cutoff_for_booking(db, booking) if booking else None
            if cutoff and not cutoff.allowed:
                whatsapp_client.send_text_message(conv.phone_number, cutoff.customer_message)
                return {
                    "status": "booking_cutoff_blocked",
                    "success": False,
                    "reason": cutoff.reason,
                    "booking_ref": booking_ref,
                }

        # Services with no published online price (hotels, ground transport, bespoke
        # charter) cannot be paid via Razorpay: a zero-amount link always fails, and
        # telling the customer to "reply resend" would loop forever.
        quote_amount = float(conv.total_amount or (booking.total_amount if booking else 0) or 0)
        if quote_amount <= 0:
            return cls._register_quote_request(db, conv, booking_ref, notify_officer=notify_officer)

        if force_new:
            link_result = PaymentService.replace_whatsapp_payment_link(db, booking_ref)
        else:
            link_result = PaymentService.initiate_whatsapp_payment_link(db, booking_ref)

        if not link_result.get("success"):
            if link_result.get("error") == "BOOKING_CUTOFF" and link_result.get("customer_message"):
                whatsapp_client.send_text_message(conv.phone_number, link_result["customer_message"])
                return {
                    "status": "booking_cutoff_blocked",
                    "booking_ref": booking_ref,
                    "success": False,
                    "reason": link_result.get("reason"),
                }
            cls._transition_state(db, conv, "WAITING_PAYMENT")
            conv.payment_status = "PENDING"
            db.commit()
            whatsapp_client.send_text_message(
                conv.phone_number,
                "Your booking is saved. We could not generate a payment link just now.\n\n"
                "Please reply *resend* to try again. Your booking will stay pending until payment is completed."
            )
            if notify_officer:
                cls._notify_officer_booking(conv, booking_ref, float(conv.total_amount or 0), link_sent=False)
            return {
                "status": "payment_link_failed",
                "booking_ref": booking_ref,
                "success": False,
                "reason": link_result.get("reason") or link_result.get("error"),
            }

        short_url = str(link_result.get("short_url") or "")
        plink_id = link_result.get("payment_link_id")
        conv.razorpay_payment_link_id = plink_id
        conv.razorpay_payment_url = short_url or None
        conv.payment_status = "PENDING"
        cls._transition_state(db, conv, "WAITING_PAYMENT")
        db.commit()
        # Anchors the payment-session expiry window.
        cls._set_wa_state_key(
            db, conv, "payment_link_issued_at", datetime.now(timezone.utc).isoformat()
        )

        cust_msg = cls._payment_link_customer_message(conv, short_url)
        whatsapp_client.send_text_message(conv.phone_number, cust_msg)
        if notify_officer:
            cls._notify_officer_booking(conv, booking_ref, float(conv.total_amount or 0), link_sent=True)

        return {
            "status": "payment_link_sent" if not link_result.get("reused") else "payment_link_reused",
            "booking_ref": booking_ref,
            "state": conv.current_state,
            "reused": bool(link_result.get("reused")),
            "success": True,
        }

    @classmethod
    def _register_quote_request(
        cls,
        db: Session,
        conv: WhatsAppConversation,
        booking_ref: str,
        notify_officer: bool = False,
    ) -> Dict[str, Any]:
        """
        Park a booking that has no online price as a quote request, so the customer
        gets a clear next step instead of a payment link that can never succeed.
        """
        from app.integrations.whatsapp import copy as wa_copy

        conv.payment_status = "QUOTE_REQUESTED"
        conv.razorpay_payment_link_id = None
        conv.razorpay_payment_url = None
        cls._transition_state(db, conv, "AWAITING_QUOTE")
        db.commit()

        whatsapp_client.send_text_message(
            conv.phone_number,
            wa_copy.QUOTE_REQUEST_REGISTERED.format(booking_ref=booking_ref),
        )
        if notify_officer:
            cls._notify_officer_booking(conv, booking_ref, 0.0, link_sent=False, quote_request=True)

        logger.info(
            "[WhatsApp Booking] Registered quote request %s for %s (no online price)",
            booking_ref,
            conv.phone_number,
        )
        return {
            "status": "quote_request_registered",
            "booking_ref": booking_ref,
            "state": conv.current_state,
            "success": True,
        }

    @classmethod
    def _notify_officer_booking(
        cls,
        conv: WhatsAppConversation,
        booking_ref: str,
        amount: float,
        link_sent: bool,
        quote_request: bool = False,
    ) -> None:
        officer_phone = os.getenv("WHATSAPP_OFFICER_NOTIFY_PHONE", "919599087959").strip()
        if quote_request:
            status_line = "QUOTE REQUESTED — no online price, send a manual quote + payment link"
        elif link_sent:
            status_line = "PENDING — payment link sent to customer"
        else:
            status_line = "PENDING — payment link not yet issued (customer can retry)"
        headline = (
            "🚨 *NEW QUOTE REQUEST RECEIVED*\n\n"
            if quote_request
            else "🚨 *NEW BOOKING REQUEST RECEIVED*\n\n"
        )
        amount_line = "On request" if quote_request else f"₹{int(amount):,}"
        flight_note = ""
        if isinstance(conv.flight_details_json, dict) and conv.flight_details_json.get("flight_later") is True:
            flight_note = " • *Flight*: TO BE CONFIRMED with customer\n"
        team_msg = (
            headline
            + f"• *Booking Ref*: {booking_ref}\n"
            + f"• *Customer*: {conv.customer_name} ({conv.customer_phone})\n"
            + f"• *Email*: {conv.customer_email}\n"
            + f"• *Service*: {conv.selected_service_name}\n"
            + f"• *Airport*: {conv.selected_airport_iata or 'N/A'}\n"
            + f"• *Flight*: {conv.flight_num or 'N/A'}\n"
            + flight_note
            + f"• *Date*: {conv.booking_date}\n"
            + f"• *Passengers*: {conv.passenger_count}\n"
            + f"• *Amount*: {amount_line}\n"
            + f"• *Status*: {status_line}"
        )
        try:
            whatsapp_client.send_text_message(officer_phone, team_msg)
        except Exception as err:
            logger.warning("[WhatsApp Booking] Officer notify failed: %s", type(err).__name__)

    @classmethod
    def _state_waiting_payment(
        cls,
        db: Session,
        conv: WhatsAppConversation,
        user_text: str,
        input_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        from app.services.payment_service import PaymentService

        text = (input_id or user_text or "").strip().lower()
        paid_phrases = {
            "i paid", "paid", "payment done", "i have paid", "have paid",
            "payment completed", "done payment", "check payment", "payment status",
            "status", "i've paid", "already paid",
        }
        expired_or_new = {"expired", "new link", "new payment link", "replace"}

        booking = None
        if conv.booking_ref:
            booking = db.scalar(select(Booking).where(Booking.booking_ref == conv.booking_ref))

        if booking and booking.status == BookingStatus.CONFIRMED:
            conv.payment_status = "SUCCESSFUL"
            conv.current_state = "COMPLETED"
            db.commit()
            whatsapp_client.send_text_message(
                conv.phone_number,
                f"✅ Payment received. Booking *{conv.booking_ref}* is confirmed."
            )
            return {"status": "already_confirmed", "success": True}

        if text in paid_phrases:
            if not conv.booking_ref:
                whatsapp_client.send_text_message(
                    conv.phone_number,
                    "We could not find a booking to check. Please type *Hi* to start again."
                )
                return {"status": "missing_booking", "success": False}
            recon = PaymentService.reconcile_whatsapp_payment_link(db, conv.booking_ref)
            db.refresh(conv)
            if booking:
                db.refresh(booking)
            if recon.get("paid") or (booking and booking.status == BookingStatus.CONFIRMED):
                whatsapp_client.send_text_message(
                    conv.phone_number,
                    f"✅ Payment confirmed. Booking *{conv.booking_ref}* is now active."
                )
                return {"status": "payment_reconciled", "success": True}
            whatsapp_client.send_text_message(
                conv.phone_number,
                "We have not received payment confirmation yet. Please complete payment using the Razorpay link. "
                "Your booking will confirm automatically after payment — we cannot mark it paid from this chat."
            )
            return cls._issue_or_resend_payment_link(db, conv)

        if conv.payment_status in ("EXPIRED", "CANCELLED") or text in expired_or_new:
            return cls._issue_or_resend_payment_link(db, conv, force_new=conv.payment_status in ("EXPIRED", "CANCELLED"))

        return cls._issue_or_resend_payment_link(db, conv)

    @classmethod
    def handle_payment_success(cls, db: Session, booking_ref: str, payment_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Handles payment completion specifically for WhatsApp bookings.
        Updates conversation and booking records, dispatches confirmation messages.
        """
        from app.services.notification_service import NotificationService

        logger.info(f"[WhatsAppBookingStateMachine] handle_payment_success for '{booking_ref}' (payment_id: {payment_id})")

        booking = db.scalar(select(Booking).where(Booking.booking_ref == booking_ref))
        if booking and booking.status != BookingStatus.CONFIRMED:
            booking.status = BookingStatus.CONFIRMED
            booking.updated_at = datetime.now(timezone.utc)

        conv = db.scalar(select(WhatsAppConversation).where(WhatsAppConversation.booking_ref == booking_ref))
        if conv:
            conv.payment_status = "SUCCESSFUL"
            conv.current_state = "COMPLETED"
            conv.updated_at = datetime.now(timezone.utc)

            # Cancel any older unpaid PENDING drafts for this customer phone
            caller_phone = conv.customer_phone or conv.phone_number
            if caller_phone:
                clean_digits = "".join(filter(str.isdigit, caller_phone))
                other_pending = db.scalars(
                    select(Booking)
                    .where(Booking.status == BookingStatus.PENDING)
                    .where(Booking.booking_ref != booking_ref)
                    .where(Booking.deleted_at.is_(None))
                ).all()
                for op in other_pending:
                    op_digits = "".join(filter(str.isdigit, op.passenger_phone or ""))
                    if op_digits and (op_digits.endswith(clean_digits[-10:]) or clean_digits.endswith(op_digits[-10:])):
                        op.status = BookingStatus.CANCELLED
                        op.notes = f"Superseded by confirmed booking {booking_ref}"
                        op.updated_at = datetime.now(timezone.utc)

            if conv.customer_phone:
                try:
                    conf_msg = (
                        f"✅ *PAYMENT CONFIRMED — BOOKING ACTIVE*\n\n"
                        f"Thank you, *{conv.customer_name or 'Valued Guest'}*! We have successfully received your payment for booking *{booking_ref}*.\n\n"
                        f"• *Service*: {conv.selected_service_name or (booking.service_type if booking else 'Airport Service')}\n"
                        f"• *Airport*: {conv.selected_airport_iata or 'N/A'}\n"
                        f"• *Status*: CONFIRMED & DISPATCHED\n\n"
                        f"Our airport concierge team has been assigned to your flight. Have a wonderful journey!"
                    )
                    whatsapp_client.send_text_message(conv.customer_phone, conf_msg)
                except Exception as wa_err:
                    logger.warning(f"[WhatsApp Payment Conf] Failed to send customer WhatsApp confirmation: {wa_err}")

        db.commit()

        if booking:
            try:
                meta = booking.metadata_json or {}
                NotificationService.notify_booking_confirmed(db, {
                    "booking_ref": booking.booking_ref,
                    "passenger_name": booking.passenger_name,
                    "passenger_email": booking.passenger_email,
                    "passenger_phone": booking.passenger_phone,
                    "passenger_count": conv.passenger_count if conv else 1,
                    "flight_num": booking.flight_num,
                    "origin_code": booking.origin_code,
                    "dest_code": booking.dest_code,
                    "airport_code": meta.get("service_airport") or booking.origin_code or booking.dest_code,
                    "journey_type": meta.get("journey_type") or booking.service_type,
                    "service_type": booking.service_type,
                    "service_name": meta.get("package") or booking.service_type,
                    "departure_time": booking.departure_time or booking.arrival_time,
                    "arrival_time": booking.arrival_time,
                    "total_amount": booking.total_amount or 0.0,
                    "currency": booking.currency or "INR",
                    "payment_id": payment_id,
                    "status": "CONFIRMED",
                })
            except Exception as mail_err:
                logger.warning(f"[WhatsApp Booking Conf] Notification dispatch error: {mail_err}")

        return {"status": "payment_confirmed", "booking_ref": booking_ref, "success": True}
