"""
Extended WhatsApp enquiry, booking confirmation,customer template, and invoice
delivery helpers for NotificationService.
"""

import os
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.models.schema import NotificationRecord, NotificationStatus


def _ns():
    import app.services.notification_service as ns_mod
    return ns_mod


# Meta Cloud API rejects any template body parameter that contains a new-line or
# tab character, or more than four consecutive spaces. A formatted multi-line
# alert therefore cannot be used directly as {{1}} -- the request is accepted by
# HTTP but Meta answers 132018 / 132000 and the customer never receives it.
TEMPLATE_PARAM_MAX_CHARS = 900  # leaves headroom inside Meta's 1024-char BODY limit


def build_template_parameter(value: Any, max_chars: int = TEMPLATE_PARAM_MAX_CHARS) -> str:
    """Return a single-line string that is safe to place inside a template variable.

    Enforces the three documented Meta rules for template parameters:
    no new-line characters, no tabs, and never more than four consecutive spaces.
    """
    text = str(value or "")
    text = text.replace("\r\n", " ").replace("\r", " ").replace("\n", " ").replace("\t", " ")
    text = re.sub(r" {2,}", " ", text)
    text = text.strip()
    if max_chars and len(text) > max_chars:
        text = text[: max_chars - 3].rstrip() + "..."
    return text


def build_customer_summary(
    *,
    reference_label: str,
    reference: str,
    guest_name: Optional[str] = None,
    service: Optional[str] = None,
    airport: Optional[str] = None,
    location_label: str = "Airport",
    date_time: Optional[str] = None,
    flight_num: Optional[str] = None,
    passengers: Optional[Any] = None,
    amount: Optional[float] = None,
    currency: Optional[str] = None,
    status_label: Optional[str] = None,
) -> str:
    """Builds the single-line booking/enquiry summary used as the {{1}} value."""
    parts: List[str] = [f"{reference_label}: {reference}"]
    if guest_name:
        parts.append(f"Guest: {guest_name}")
    if service:
        parts.append(f"Service: {service}")
    if airport:
        parts.append(f"{location_label}: {airport}")
    if date_time:
        parts.append(f"Date: {date_time}")
    if flight_num:
        parts.append(f"Flight: {flight_num}")
    try:
        pax = int(passengers) if passengers is not None else 0
    except (TypeError, ValueError):
        pax = 0
    if pax > 0:
        parts.append(f"Guests: {pax}")
    if amount is not None:
        try:
            amount_value = float(amount)
        except (TypeError, ValueError):
            amount_value = 0.0
        if amount_value > 0:
            code = (currency or "INR").upper()
            parts.append(f"Amount: {code} {amount_value:,.0f}")
    if status_label:
        parts.append(f"Status: {status_label}")
    return build_template_parameter(" | ".join(parts))


class NotificationServiceExtMixin:
    """Mixin providing extended WhatsApp and confirmation methods for NotificationService."""

    WHATSAPP_CUSTOMER_CONFIRMATION_TEMPLATE_TYPE = "WHATSAPP_CUSTOMER_CONFIRMATION"
    WHATSAPP_CUSTOMER_ENQUIRY_TEMPLATE_TYPE = "WHATSAPP_CUSTOMER_ENQUIRY_ACK"
    WHATSAPP_INVOICE_TEMPLATE = "WHATSAPP_INVOICE_PDF"

    @classmethod
    def _dispatch_round_trip_enquiry_whatsapp(cls, context: Dict[str, Any]) -> Dict[str, Any]:
        """
        Dispatches an outbound WhatsApp notification to WHATSAPP_OFFICER_NOTIFY_PHONE
        for a newly created Round Trip enquiry.
        Never raises: all network and Meta API failures are caught and logged safely.
        """
        ns = _ns()
        settings = ns.settings
        whatsapp_client = ns.whatsapp_client
        logger = ns.logger

        booking_ref = str(context.get("booking_ref") or context.get("bookingRef") or "N/A")
        details = context.get("details") or {}

        officer_phone = (
            getattr(settings, "WHATSAPP_ROUND_TRIP_STAFF_PHONE", None)
            or getattr(settings, "WHATSAPP_FLIGHTS_STAFF_PHONE", None)
            or getattr(settings, "WHATSAPP_OFFICER_NOTIFY_PHONE", None)
            or os.getenv("WHATSAPP_ROUND_TRIP_STAFF_PHONE", "")
            or os.getenv("WHATSAPP_FLIGHTS_STAFF_PHONE", "")
            or os.getenv("WHATSAPP_OFFICER_NOTIFY_PHONE", "")
            or "919599087959"
        ).strip()

        if not officer_phone:
            logger.warning(
                "Round Trip enquiry WhatsApp skipped: staff phone is not configured",
                extra={"booking_ref": booking_ref},
            )
            return {"status": "BYPASSED", "reason": "officer_phone_not_configured"}

        customer_name = str(context.get("passenger_name") or context.get("passengerName") or "N/A").strip()
        customer_phone = str(context.get("passenger_phone") or context.get("passengerPhone") or "N/A").strip()
        customer_email = str(context.get("passenger_email") or context.get("passengerEmail") or "N/A").strip()

        origin = context.get("origin_code") or details.get("origin") or "N/A"
        dest = context.get("dest_code") or details.get("destination") or "N/A"
        outbound_date = (
            details.get("departure_date")
            or details.get("outbound_date")
            or details.get("outboundDate")
            or context.get("departure_time")
            or context.get("service_date")
            or "N/A"
        )
        return_date = details.get("return_date") or details.get("returnDate") or "N/A"
        outbound_flight = details.get("outbound_flight") or details.get("outboundFlight") or context.get("flight_num")
        return_flight = details.get("return_flight") or details.get("returnFlight")
        pax_count = (
            details.get("passenger_count")
            or details.get("passengerCount")
            or context.get("passenger_count")
            or 1
        )
        notes = (
            context.get("notes")
            or details.get("additional_requirements")
            or details.get("notes")
        )

        lines = [
            "✈️ *NEW ROUND TRIP ENQUIRY*",
            "",
            f"• *Reference*: {booking_ref}",
            f"• *Customer*: {customer_name}",
            f"• *Phone*: {customer_phone}",
            f"• *Email*: {customer_email}",
            f"• *Origin*: {origin}",
            f"• *Destination*: {dest}",
            f"• *Outbound Date*: {outbound_date}",
            f"• *Return Date*: {return_date}",
        ]
        if outbound_flight:
            lines.append(f"• *Outbound Flight*: {outbound_flight}")
        if return_flight:
            lines.append(f"• *Return Flight*: {return_flight}")
        lines.append(f"• *Passengers*: {pax_count}")
        if notes and str(notes).strip():
            lines.append(f"• *Additional Requirements*: {str(notes).strip()}")

        lines.append("")
        lines.append("⚡ *Action*: Team to contact customer manually for availability & quotation.")

        message_body = "\n".join(lines)

        try:
            logger.info("Dispatching Round Trip enquiry WhatsApp notification for %s", booking_ref)
            result = whatsapp_client.send_text_message(officer_phone, message_body)
            if result.get("success"):
                logger.info(
                    "Round Trip WhatsApp notification successfully dispatched for %s (msg id: %s)",
                    booking_ref,
                    result.get("message_id"),
                )
                return {"status": "DELIVERED", "message_id": result.get("message_id")}
            logger.warning(
                "Round Trip WhatsApp notification dispatch failed for %s: %s",
                booking_ref,
                result.get("error"),
            )
            return {"status": "FAILED", "error": result.get("error")}
        except Exception as exc:
            logger.warning(
                "Round Trip WhatsApp notification encountered exception for %s: %s",
                booking_ref,
                type(exc).__name__,
            )
            return {"status": "FAILED", "error": type(exc).__name__}

    @classmethod
    def _dispatch_ticketing_enquiry_whatsapp(cls, context: Dict[str, Any]) -> Dict[str, Any]:
        """
        Dispatches an outbound WhatsApp notification to staff assigned to Ticketing
        for a newly created Ticketing enquiry.
        Never raises: all network and Meta API failures are caught and logged safely.
        """
        ns = _ns()
        settings = ns.settings
        whatsapp_client = ns.whatsapp_client
        logger = ns.logger

        booking_ref = str(context.get("booking_ref") or context.get("bookingRef") or "N/A")
        details = context.get("details") or {}

        officer_phone = (
            getattr(settings, "WHATSAPP_TICKETING_STAFF_PHONE", None)
            or getattr(settings, "WHATSAPP_OFFICER_NOTIFY_PHONE", None)
            or os.getenv("WHATSAPP_TICKETING_STAFF_PHONE", "")
            or os.getenv("WHATSAPP_OFFICER_NOTIFY_PHONE", "")
            or "919599087957"
        ).strip()

        if not officer_phone:
            logger.warning(
                "Ticketing enquiry WhatsApp skipped: staff phone is not configured",
                extra={"booking_ref": booking_ref},
            )
            return {"status": "BYPASSED", "reason": "officer_phone_not_configured"}

        customer_name = str(context.get("passenger_name") or context.get("passengerName") or "N/A").strip()
        customer_phone = str(context.get("passenger_phone") or context.get("passengerPhone") or "N/A").strip()
        customer_email = str(context.get("passenger_email") or context.get("passengerEmail") or "N/A").strip()

        origin = context.get("origin_code") or details.get("origin") or "N/A"
        dest = context.get("dest_code") or details.get("destination") or "N/A"
        travel_date = (
            details.get("departure_date")
            or details.get("travel_date")
            or details.get("service_date")
            or context.get("departure_time")
            or context.get("service_date")
            or "N/A"
        )
        pax_count = (
            details.get("passenger_count")
            or details.get("passengerCount")
            or context.get("passenger_count")
            or 1
        )
        notes = (
            context.get("notes")
            or details.get("additional_requirements")
            or details.get("notes")
        )

        lines = [
            "🎫 *NEW TICKETING ENQUIRY*",
            "",
            f"• *Reference*: {booking_ref}",
            f"• *Customer*: {customer_name}",
            f"• *Phone*: {customer_phone}",
            f"• *Email*: {customer_email}",
            f"• *Origin*: {origin}",
            f"• *Destination*: {dest}",
            f"• *Travel Date*: {travel_date}",
            f"• *Passengers*: {pax_count}",
        ]
        if notes and str(notes).strip():
            lines.append(f"• *Additional Requirements*: {str(notes).strip()}")

        lines.append("")
        lines.append("⚡ *Action*: Team to contact customer manually for availability & quotation.")

        message_body = "\n".join(lines)

        try:
            logger.info("Dispatching Ticketing enquiry WhatsApp notification for %s", booking_ref)
            result = whatsapp_client.send_text_message(officer_phone, message_body)
            if result.get("success"):
                logger.info(
                    "Ticketing WhatsApp notification successfully dispatched for %s (msg id: %s)",
                    booking_ref,
                    result.get("message_id"),
                )
                return {"status": "DELIVERED", "message_id": result.get("message_id")}
            logger.warning(
                "Ticketing WhatsApp notification dispatch failed for %s: %s",
                booking_ref,
                result.get("error"),
            )
            return {"status": "FAILED", "error": result.get("error")}
        except Exception as exc:
            logger.warning(
                "Ticketing WhatsApp notification encountered exception for %s: %s",
                booking_ref,
                type(exc).__name__,
            )
            return {"status": "FAILED", "error": type(exc).__name__}

    @classmethod
    def _dispatch_lounge_enquiry_whatsapp(cls, context: Dict[str, Any]) -> Dict[str, Any]:
        """
        Dispatches an outbound WhatsApp notification to staff assigned to Lounge service
        for a newly created Lounge enquiry.
        Never raises: all network and Meta API failures are caught and logged safely.
        """
        ns = _ns()
        settings = ns.settings
        whatsapp_client = ns.whatsapp_client
        logger = ns.logger

        booking_ref = str(context.get("booking_ref") or context.get("bookingRef") or "N/A")
        details = context.get("details") or {}

        officer_phone = (
            getattr(settings, "WHATSAPP_LOUNGE_STAFF_PHONE", None)
            or getattr(settings, "WHATSAPP_OFFICER_NOTIFY_PHONE", None)
            or os.getenv("WHATSAPP_LOUNGE_STAFF_PHONE", "")
            or os.getenv("WHATSAPP_OFFICER_NOTIFY_PHONE", "")
            or "919599087957"
        ).strip()

        if not officer_phone:
            logger.warning(
                "Lounge enquiry WhatsApp skipped: staff phone is not configured",
                extra={"booking_ref": booking_ref},
            )
            return {"status": "BYPASSED", "reason": "officer_phone_not_configured"}

        customer_name = str(context.get("passenger_name") or context.get("passengerName") or "N/A").strip()
        customer_phone = str(context.get("passenger_phone") or context.get("passengerPhone") or "N/A").strip()
        customer_email = str(context.get("passenger_email") or context.get("passengerEmail") or "N/A").strip()

        airport = context.get("origin_code") or details.get("airport") or "N/A"
        lounge_type = context.get("service_type") or details.get("lounge_type") or "Lounge"
        travel_date = (
            details.get("travel_date")
            or details.get("service_date")
            or context.get("departure_time")
            or "N/A"
        )
        pax_count = (
            details.get("passenger_count")
            or details.get("passengerCount")
            or context.get("passenger_count")
            or 1
        )
        notes = (
            context.get("notes")
            or details.get("additional_requirements")
            or details.get("notes")
        )

        message_body = (
            f"🛋️ *NEW LOUNGE ENQUIRY RECEIVED*\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"📋 *Ref:* {booking_ref}\n"
            f"👤 *Guest:* {customer_name}\n"
            f"📞 *Phone:* {customer_phone}\n"
            f"✉️ *Email:* {customer_email}\n"
            f"📍 *Airport:* {airport}\n"
            f"🛋️ *Lounge Type:* {lounge_type}\n"
            f"📅 *Date & Time:* {travel_date}\n"
            f"👥 *Passengers:* {pax_count}\n"
        )
        if notes:
            message_body += f"📝 *Requirements:* {notes}\n"
        message_body += (
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"⚡ *Action Required:* Please contact the guest with lounge availability and rates."
        )

        try:
            result = whatsapp_client.send_text_message(
                to_phone=officer_phone,
                message=message_body,
            )
            if result.get("success"):
                logger.info(
                    "Lounge WhatsApp notification dispatched for %s to %s",
                    booking_ref,
                    officer_phone,
                )
                return {"status": "DELIVERED", "message_id": result.get("message_id")}
            logger.warning(
                "Lounge WhatsApp notification dispatch failed for %s: %s",
                booking_ref,
                result.get("error"),
            )
            return {"status": "FAILED", "error": result.get("error")}
        except Exception as exc:
            logger.warning(
                "Lounge WhatsApp notification encountered exception for %s: %s",
                booking_ref,
                type(exc).__name__,
            )
            return {"status": "FAILED", "error": type(exc).__name__}

    @classmethod
    def notify_booking_confirmed(
        cls,
        db: Session,
        context: Dict[str, Any],
        attachments: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """
        Sends the official BOOKING_CONFIRMATION customer and operations notifications
        only after a payment is verified as successful.
        Protected by _already_notified idempotency check.
        """
        logger = _ns().logger
        booking_ref = str(context.get("booking_ref") or context.get("bookingRef") or "")
        customer_email = str(context.get("passenger_email") or context.get("passengerEmail") or "").strip()
        payload = {
            "booking_ref": booking_ref,
            "bookingRef": booking_ref,
            "passengerName": context.get("passenger_name") or context.get("passengerName"),
            "passengerEmail": customer_email,
            "passengerPhone": context.get("passenger_phone") or context.get("passengerPhone"),
            "passengerCount": context.get("passenger_count") or context.get("passengerCount") or context.get("pax_count") or 1,
            "flightNum": context.get("flight_num") or context.get("flightNum"),
            "originCode": context.get("origin_code") or context.get("originCode"),
            "destCode": context.get("dest_code") or context.get("destCode"),
            "airportCode": context.get("airport_code") or context.get("airportCode"),
            "journeyType": context.get("journey_type") or context.get("journeyType"),
            "service_type": context.get("service_type") or context.get("serviceType"),
            "service_name": context.get("service_name") or context.get("package") or context.get("service_type"),
            "departureTime": context.get("departure_time") or context.get("service_date"),
            "terminal": context.get("terminal"),
            "totalAmount": float(context.get("total_amount") or context.get("totalAmount") or 0.0),
            "currency": context.get("currency") or "INR",
            "status": "CONFIRMED",
            "invoice_number": context.get("invoice_number"),
            "invoice_url": context.get("invoice_url") or "",
            "invoice_attached": bool(context.get("invoice_attached")),
        }

        summary: Dict[str, Any] = {
            "booking_ref": booking_ref,
            "customer": None,
            "customer_whatsapp": None,
            "admin": [],
        }
        logger.info("Official booking confirmation notification requested", extra={"booking_ref": booking_ref})

        try:
            if customer_email:
                summary["customer"] = cls._record_and_send(
                    db,
                    recipient_email=customer_email,
                    template_type="BOOKING_CONFIRMATION",
                    payload=payload,
                    booking_ref=booking_ref,
                    recipient_phone=None,
                    attachments=attachments,
                )
            else:
                logger.warning("Customer confirmation skipped: missing email", extra={"booking_ref": booking_ref})
                summary["customer"] = {"status": "FAILED", "error": "missing_recipient"}

            try:
                confirmed_context = dict(context)
                confirmed_context.setdefault("status", "CONFIRMED")
                summary["customer_whatsapp"] = cls.send_customer_whatsapp_confirmation(db, confirmed_context)
            except Exception as wa_exc:
                logger.exception(
                    "[Customer WhatsApp] Failed to send customer WhatsApp confirmation for %s: %s",
                    booking_ref,
                    type(wa_exc).__name__,
                )
                summary["customer_whatsapp"] = {"status": "FAILED", "error": str(wa_exc)}

            for admin_email in cls.admin_notification_recipients():
                summary["admin"].append(
                    {
                        "recipient_domain": admin_email.split("@")[-1],
                        **cls._record_and_send(
                            db,
                            recipient_email=admin_email,
                            template_type="ADMIN_NEW_BOOKING",
                            payload=payload,
                            booking_ref=booking_ref,
                        ),
                    }
                )
        except Exception as exc:
            logger.exception("Booking confirmation notification failed for %s: %s", booking_ref, type(exc).__name__)
        return summary

    @classmethod
    def _resolve_customer_template_settings(cls) -> Tuple[str, str]:
        """
        Safely extracts and sanitizes the customer confirmation template name and language.
        Gracefully handles values that were inadvertently pasted across multiple lines
        or that contain KEY=VALUE pairs in dashboard environment variables.
        """
        settings = _ns().settings
        raw_name = getattr(settings, "WHATSAPP_CUSTOMER_CONFIRMATION_TEMPLATE", None)
        if raw_name is None:
            raw_name = os.getenv("WHATSAPP_CUSTOMER_CONFIRMATION_TEMPLATE", "")

        raw_lang = getattr(settings, "WHATSAPP_CUSTOMER_TEMPLATE_LANG", None)
        if raw_lang is None:
            raw_lang = os.getenv("WHATSAPP_CUSTOMER_TEMPLATE_LANG", "")

        template_name = str(raw_name or "").strip()
        template_lang = str(raw_lang or "").strip()

        if "\n" in template_name or "\r" in template_name:
            lines = [l.strip() for l in template_name.replace("\r", "\n").split("\n") if l.strip()]
            if lines:
                template_name = lines[0]
                for extra in lines[1:]:
                    if "WHATSAPP_CUSTOMER_TEMPLATE_LANG=" in extra:
                        template_lang = extra.split("=", 1)[1].strip()
                    elif not template_lang and "=" not in extra:
                        template_lang = extra

        if template_lang.startswith("WHATSAPP_CUSTOMER_TEMPLATE_LANG="):
            template_lang = template_lang.split("=", 1)[1].strip()

        if not template_lang:
            # The approved template `shafsky_booking_confirmation` is registered on the
            # WhatsApp Business Account as language "en". Sending "en_US" for it returns
            # 132001 (template does not exist in the requested language).
            template_lang = "en"

        return template_name, template_lang

    @classmethod
    def validate_customer_whatsapp_configuration(cls) -> Dict[str, Any]:
        """
        Diagnostic check for the customer WhatsApp booking confirmation notification path.
        """
        whatsapp_client = _ns().whatsapp_client
        template_name, template_lang = cls._resolve_customer_template_settings()

        is_client_ok = whatsapp_client.is_configured()
        problems: List[str] = []
        if not is_client_ok:
            problems.append(
                "WhatsApp Cloud API credentials missing: set WHATSAPP_ACCESS_TOKEN and WHATSAPP_PHONE_NUMBER_ID."
            )
        if not template_name:
            problems.append(
                "No approved customer template: set WHATSAPP_CUSTOMER_CONFIRMATION_TEMPLATE so customer "
                "notifications work outside the 24-hour service window."
            )

        return {
            "ok": not problems,
            "whatsapp_configured": is_client_ok,
            "template": template_name or None,
            "template_language": template_lang,
            "template_configured": bool(template_name),
            "problems": problems,
        }

    @classmethod
    def send_customer_whatsapp_confirmation(
        cls,
        db: Session,
        context: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Dispatches an outbound WhatsApp booking confirmation notification
        to the customer's provided mobile number.
        """
        from unittest.mock import MagicMock
        from app.services.whatsapp_department_service import WhatsAppDepartmentService

        ns = _ns()
        settings = ns.settings
        whatsapp_client = ns.whatsapp_client
        logger = ns.logger

        booking_ref = str(context.get("booking_ref") or context.get("bookingRef") or "").strip()
        if not booking_ref:
            logger.warning("[Customer WhatsApp] Confirmation skipped: missing booking reference")
            return {"status": "SKIPPED", "reason": "missing_booking_ref"}

        # 1. Booking Lookup & Validation
        booking = None
        if db is not None and not isinstance(db, MagicMock):
            try:
                from app.models.schema import Booking
                booking = db.scalar(select(Booking).where(Booking.booking_ref == booking_ref))
            except Exception as db_err:
                logger.debug("[Customer WhatsApp] DB lookup for booking %s: %s", booking_ref, db_err)

        # 2. Trigger & Payment Validation: only send for CONFIRMED / PAID bookings
        is_explicitly_confirmed = str(context.get("status") or "").strip().upper() in ("CONFIRMED", "SUCCESS", "PAID")

        if booking is not None and not isinstance(booking, MagicMock):
            from app.models.schema import BookingStatus
            if hasattr(booking, "status") and isinstance(booking.status, BookingStatus):
                if booking.status in (BookingStatus.CANCELLED, BookingStatus.REJECTED):
                    logger.info(
                        "[Customer WhatsApp] Confirmation skipped for %s: booking status is '%s'",
                        booking_ref,
                        booking.status.value if hasattr(booking.status, "value") else booking.status,
                    )
                    return {
                        "status": "SKIPPED",
                        "reason": "booking_not_confirmed",
                        "booking_ref": booking_ref,
                    }
                if booking.status != BookingStatus.CONFIRMED and not is_explicitly_confirmed:
                    logger.info(
                        "[Customer WhatsApp] Confirmation skipped for %s: booking status is '%s' (not CONFIRMED)",
                        booking_ref,
                        booking.status.value if hasattr(booking.status, "value") else booking.status,
                    )
                    return {
                        "status": "SKIPPED",
                        "reason": "booking_not_confirmed",
                        "booking_ref": booking_ref,
                    }
        elif context.get("status"):
            if not is_explicitly_confirmed:
                ctx_status = str(context.get("status")).strip().upper()
                logger.info(
                    "[Customer WhatsApp] Confirmation skipped for %s: status is '%s' (not CONFIRMED)",
                    booking_ref,
                    ctx_status,
                )
                return {
                    "status": "SKIPPED",
                    "reason": "booking_not_confirmed",
                    "booking_ref": booking_ref,
                }
        else:
            logger.info(
                "[Customer WhatsApp] Confirmation skipped for %s: no confirmation status provided",
                booking_ref,
            )
            return {
                "status": "SKIPPED",
                "reason": "booking_not_confirmed",
                "booking_ref": booking_ref,
            }

        # 3. Customer Phone Retrieval & Normalization
        raw_phone = (
            context.get("passenger_phone")
            or context.get("passengerPhone")
            or context.get("phone")
            or context.get("contact_phone")
        )
        if not raw_phone and booking:
            raw_phone = getattr(booking, "passenger_phone", None)

        if not raw_phone or not str(raw_phone).strip():
            logger.warning(
                "[Customer WhatsApp] Confirmation skipped for %s: missing customer mobile number",
                booking_ref,
            )
            return {
                "status": "SKIPPED",
                "reason": "missing_customer_phone",
                "booking_ref": booking_ref,
            }

        clean_phone = WhatsAppDepartmentService.normalize_phone_number(raw_phone)
        if not clean_phone or len(clean_phone) < 10:
            masked = WhatsAppDepartmentService._mask_phone(str(raw_phone))
            logger.warning(
                "[Customer WhatsApp] Confirmation failed for %s: invalid customer phone '%s'",
                booking_ref,
                masked,
            )
            return {"status": "FAILED", "reason": "invalid_customer_phone", "booking_ref": booking_ref}

        # 4. WhatsApp Client Configuration Check
        if not whatsapp_client.is_configured():
            logger.warning(
                "[Customer WhatsApp] Confirmation skipped for %s: Meta WhatsApp Cloud API credentials not configured",
                booking_ref,
            )
            return {"status": "BYPASSED", "reason": "whatsapp_client_unconfigured", "booking_ref": booking_ref}

        # 5. Extract available details without inventing missing values
        meta = getattr(booking, "metadata_json", None) if booking else {}
        if not isinstance(meta, dict):
            meta = {}

        customer_name = str(
            context.get("passenger_name")
            or context.get("passengerName")
            or (getattr(booking, "passenger_name", None) if booking else None)
            or "Valued Guest"
        ).strip()

        airport_name = str(
            context.get("airport_name")
            or context.get("airport_code")
            or context.get("airportCode")
            or meta.get("service_airport")
            or meta.get("airport")
            or (getattr(booking, "origin_code", None) if booking else None)
            or (getattr(booking, "dest_code", None) if booking else None)
            or "Airport"
        ).strip()

        service_type = str(
            context.get("journey_type")
            or context.get("journeyType")
            or context.get("service_type")
            or context.get("serviceType")
            or meta.get("journey_type")
            or meta.get("package")
            or (getattr(booking, "service_type", None) if booking else None)
            or context.get("service_name")
            or "VIP Airport Assistance"
        ).strip()

        flight_raw = (
            context.get("flight_num")
            or context.get("flightNum")
            or (getattr(booking, "flight_num", None) if booking else None)
        )
        flight_num = str(flight_raw).strip() if flight_raw and str(flight_raw).strip() not in ("Flight", "N/A", "None", "") else None

        date_time_raw = (
            context.get("departure_time")
            or context.get("departureTime")
            or context.get("service_date")
            or context.get("serviceDate")
            or (booking.departure_time if booking else None)
            or (booking.arrival_time if booking else None)
        )
        date_time_str = None
        if date_time_raw:
            if isinstance(date_time_raw, datetime):
                date_time_str = date_time_raw.strftime("%d %b %Y, %H:%M")
            else:
                date_time_str = str(date_time_raw).strip()

        support_phone = getattr(settings, "SUPPORT_PHONE", None) or os.getenv("SUPPORT_PHONE") or "+91 9599087959"

        # 6. Format Message Content
        #    `message_body` is only used for the freeform fallback (inside the 24h
        #    window). The template variable MUST be a single line: Meta rejects
        #    new-line/tab characters in body parameters, which silently dropped
        #    every customer confirmation while the API still returned 200/4xx.
        lines = [
            "✈️ *Shafsky Aviation Services — Booking Confirmed*",
            "",
            f"Dear *{customer_name}*,",
            "",
            "Thank you for choosing Shafsky Aviation Services. We are delighted to confirm your VIP service booking.",
            "",
            f"• *Booking Reference:* {booking_ref}",
            f"• *Airport:* {airport_name}",
            f"• *Service:* {service_type}",
        ]
        if flight_num:
            lines.append(f"• *Flight:* {flight_num}")
        if date_time_str:
            lines.append(f"• *Date & Time:* {date_time_str}")
        lines.append("• *Status:* CONFIRMED")
        lines.extend([
            "",
            f"Our 24/7 command desk is at your disposal: {support_phone}.",
        ])
        message_body = "\n".join(lines)

        total_amount_raw = context.get("total_amount")
        if total_amount_raw is None and booking is not None:
            total_amount_raw = getattr(booking, "total_amount", None)
        passengers_raw = (
            context.get("passenger_count")
            or context.get("passengerCount")
            or meta.get("passenger_count")
            or meta.get("guest_count")
        )
        template_param = build_customer_summary(
            reference_label="Booking reference",
            reference=booking_ref,
            guest_name=customer_name,
            service=service_type,
            airport=airport_name,
            date_time=date_time_str,
            flight_num=flight_num,
            passengers=passengers_raw,
            amount=total_amount_raw,
            currency=context.get("currency") or (getattr(booking, "currency", None) if booking else None),
            status_label="CONFIRMED",
        )

        payload = {
            "booking_ref": booking_ref,
            "recipient_masked": WhatsAppDepartmentService._mask_phone(clean_phone),
            "passenger_name": customer_name,
            "airport": airport_name,
            "service_type": service_type,
            "flight_num": flight_num,
            "date_time": date_time_str,
            "status": "CONFIRMED",
            "support_phone": support_phone,
        }

        # 7. Idempotency Claim, Template Resolution & Dispatch
        return cls._dispatch_customer_whatsapp(
            db,
            booking_ref=booking_ref,
            clean_phone=clean_phone,
            template_type=cls.WHATSAPP_CUSTOMER_CONFIRMATION_TEMPLATE_TYPE,
            message_body=message_body,
            template_param=template_param,
            payload=payload,
        )

    @classmethod
    def _customer_notification_already_handled(
        cls,
        db: Session,
        *,
        template_type: str,
        booking_ref: str,
        recipient: str,
    ) -> Optional[str]:
        """Returns a claim action when this customer notification must not be sent again.

        `NotificationRecord.COMPLETED` means Meta accepted the message but has not
        confirmed delivery yet, and `PENDING` means a timeout left the outcome
        unknown. Neither may be re-sent blindly: the Cloud API has no idempotency
        key, so a retry would duplicate a message the customer may already have.
        """
        if db is None or not booking_ref:
            return None
        try:
            records = list(
                db.scalars(
                    select(NotificationRecord)
                    .where(NotificationRecord.template_type == template_type)
                    .order_by(desc(NotificationRecord.created_at))
                    .limit(50)
                ).all()
            )
        except Exception as db_err:  # pragma: no cover - defensive
            _ = db_err
            return None

        rec_clean = (recipient or "").strip()
        for record in records:
            payload = record.payload if isinstance(record.payload, dict) else {}
            ref = str(payload.get("booking_ref") or payload.get("bookingRef") or "").strip()
            if ref != booking_ref.strip():
                continue
            record_recipient = (record.recipient_email or record.recipient_phone or "").strip()
            if rec_clean and not (
                record_recipient == rec_clean
                or (len(rec_clean) >= 10 and rec_clean[-10:] in record_recipient)
            ):
                continue
            if record.status in (
                NotificationStatus.DELIVERED,
                NotificationStatus.BYPASSED,
                NotificationStatus.COMPLETED,
                NotificationStatus.READ,
                NotificationStatus.OPENED,
            ):
                return "ALREADY_DELIVERED"
            if record.status in (NotificationStatus.PENDING, NotificationStatus.PROCESSING):
                return "IN_PROGRESS"
        return None

    @classmethod
    def _dispatch_customer_whatsapp(
        cls,
        db: Session,
        *,
        booking_ref: str,
        clean_phone: str,
        template_type: str,
        message_body: str,
        template_param: str,
        payload: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Claims, sends and records one customer WhatsApp notification.

        Honest reporting rules:
        * Meta acceptance (HTTP 200 + wamid) stores COMPLETED, not DELIVERED. Only the
          delivery-status webhook may move a record to DELIVERED.
        * A timeout is ambiguous, so the record is held as PENDING and never retried
          automatically (the Cloud API has no idempotency key).
        """
        from app.services.whatsapp_department_service import WhatsAppDepartmentService

        ns = _ns()
        whatsapp_client = ns.whatsapp_client
        logger = ns.logger
        masked_phone = WhatsAppDepartmentService._mask_phone(clean_phone)

        prior_action = cls._customer_notification_already_handled(
            db,
            template_type=template_type,
            booking_ref=booking_ref,
            recipient=clean_phone,
        )
        if prior_action:
            logger.info(
                "[Customer WhatsApp] Skipping duplicate customer notification (%s, template=%s) for %s",
                prior_action,
                template_type,
                booking_ref,
            )
            return {
                "status": "SKIPPED",
                "reason": "duplicate",
                "claim_status": prior_action,
                "booking_ref": booking_ref,
            }

        # A booking workflow must never fail because a notification claim failed
        # (a stub/non-ORM session also lands here; the booking itself is already committed).
        try:
            record, action = cls._acquire_notification_claim(
                db,
                template_type=template_type,
                booking_ref=booking_ref,
                recipient=clean_phone,
                channel="WHATSAPP",
                payload=payload,
            )
        except Exception as claim_err:
            logger.error(
                "[Customer WhatsApp] Could not claim %s for %s: %s",
                template_type,
                booking_ref,
                type(claim_err).__name__,
            )
            return {"status": "FAILED", "error": "claim_failed", "booking_ref": booking_ref}

        if action in ("ALREADY_DELIVERED", "IN_PROGRESS"):
            logger.info(
                "[Customer WhatsApp] Skipping duplicate customer notification (%s, template=%s) for %s",
                action,
                template_type,
                booking_ref,
            )
            return {"status": "SKIPPED", "reason": "duplicate", "claim_status": action, "booking_ref": booking_ref}
        if action == "MAX_ATTEMPTS_EXCEEDED":
            logger.warning(
                "[Customer WhatsApp] Max attempts exceeded for customer notification %s (template=%s)",
                booking_ref,
                template_type,
            )
            return {"status": "FAILED", "error": "max_attempts_exceeded", "booking_ref": booking_ref}
        if not record:
            return {"status": "FAILED", "error": "claim_failed", "booking_ref": booking_ref}

        template_name, template_lang = cls._resolve_customer_template_settings()
        param_text = build_template_parameter(template_param)

        def _send(language: str) -> Dict[str, Any]:
            if template_name:
                components = [
                    {
                        "type": "body",
                        "parameters": [
                            {"type": "text", "text": param_text}
                        ]
                    }
                ]
                return whatsapp_client.send_message(
                    to_phone=clean_phone,
                    template_name=template_name,
                    template_language=language,
                    template_components=components,
                )
            logger.warning(
                "[Customer WhatsApp] No approved template configured; falling back to freeform text "
                "for %s. Meta only accepts freeform text inside the 24-hour service window (131047 otherwise).",
                booking_ref,
            )
            return whatsapp_client.send_text_message(
                to_phone=clean_phone,
                message_body=message_body,
            )

        try:
            res = _send(template_lang)
        except Exception as send_err:
            logger.exception(
                "[Customer WhatsApp] Exception sending notification for %s to %s: %s",
                booking_ref,
                masked_phone,
                send_err,
            )
            res = {
                "success": False,
                "error": f"Exception during send: {type(send_err).__name__}",
                "status": "failed",
            }

        # 132001 = the template is not registered under the configured language. It is
        # rejected before any message is created (no wamid), so a single retry cannot
        # duplicate a delivery. The usual cause is a regional code (en_US) configured for
        # a template approved as the base language (en).
        if (
            template_name
            and not res.get("success")
            and res.get("error_code") == 132001
            and "_" in template_lang
        ):
            base_lang = template_lang.split("_", 1)[0].strip()
            if base_lang and base_lang != template_lang:
                logger.error(
                    "[Customer WhatsApp] Template '%s' is not approved for language '%s' (132001). "
                    "Retrying once with '%s'. Fix WHATSAPP_CUSTOMER_TEMPLATE_LANG in the deployment "
                    "environment so it matches the approved template language.",
                    template_name,
                    template_lang,
                    base_lang,
                )
                try:
                    retry_res = _send(base_lang)
                except Exception as retry_err:
                    logger.error(
                        "[Customer WhatsApp] Language fallback send raised for %s: %s",
                        booking_ref,
                        type(retry_err).__name__,
                    )
                    retry_res = None
                if retry_res and retry_res.get("success"):
                    res = retry_res
                    template_lang = base_lang

        is_success = res.get("success") is True
        message_id = res.get("message_id")
        error_code = res.get("error_code")
        http_status = res.get("status_code")
        now = datetime.now(timezone.utc)

        delivery_diag: Dict[str, Any] = {
            "template": template_name or None,
            "template_language": template_lang if template_name else None,
            "parameter_chars": len(param_text),
            "api_status": res.get("status"),
            "http_status": http_status,
            "error_code": error_code,
            "message_id": str(message_id) if message_id else None,
            "updated_at": now.isoformat(),
        }
        record.payload = {**(record.payload or payload), "delivery": delivery_diag}

        if is_success and message_id:
            record.status = NotificationStatus.COMPLETED
            record.message_id = str(message_id)
            record.error_log = None
            outcome = "ACCEPTED"
            logger.info(
                "[Customer WhatsApp] %s accepted by Meta for %s (to=%s, template=%s/%s, message_id=%s, "
                "http=%s). Delivery is unconfirmed until the status webhook arrives.",
                template_type,
                booking_ref,
                masked_phone,
                template_name or "freeform",
                template_lang if template_name else "-",
                message_id,
                http_status,
            )
        elif res.get("status") == "timeout_unverified":
            record.status = NotificationStatus.PENDING
            record.error_log = "Timeout connecting to Meta WhatsApp API; delivery state unknown (not retried automatically)."
            outcome = "UNKNOWN"
            logger.error(
                "[Customer WhatsApp] Send timed out for %s (to=%s, template=%s/%s); delivery state unknown, "
                "not retrying to avoid a duplicate.",
                booking_ref,
                masked_phone,
                template_name or "freeform",
                template_lang if template_name else "-",
            )
        else:
            record.status = NotificationStatus.FAILED
            err_text = str(res.get("error") or res.get("error_user_msg") or "delivery_failed")
            if error_code == 132001:
                err_text = (
                    f"{err_text} | template '{template_name}' is not approved for language "
                    f"'{template_lang}'; align WHATSAPP_CUSTOMER_TEMPLATE_LANG with Meta"
                )
            record.error_log = err_text[:400]
            outcome = "FAILED"
            logger.error(
                "[Customer WhatsApp] Send failed for %s (to=%s, template=%s/%s, http=%s, code=%s, subcode=%s): %s",
                booking_ref,
                masked_phone,
                template_name or "freeform",
                template_lang if template_name else "-",
                http_status,
                error_code,
                res.get("error_subcode"),
                err_text,
            )

        record.updated_at = now
        try:
            db.commit()
        except Exception as commit_err:
            db.rollback()
            logger.debug("[Customer WhatsApp] DB commit skipped: %s", commit_err)

        return {
            "status": outcome,
            "booking_ref": booking_ref,
            "recipient_phone": masked_phone,
            "message_id": message_id,
            "template_configured": bool(template_name),
            "template_language": template_lang if template_name else None,
            "error_code": error_code,
            "error": record.error_log if outcome == "FAILED" else None,
        }

    @classmethod
    def send_customer_whatsapp_enquiry_acknowledgement(
        cls,
        db: Session,
        context: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Sends the customer an acknowledgement for a quote-only website enquiry.

        Enquiries (hotel, ground transport, lounge, ticketing, visa, service query) are
        persisted as PENDING zero-value bookings and are NOT confirmed bookings. They
        must never receive the "Booking Confirmed" wording, so this path reuses the
        same approved Meta template (its body says the request "has been received")
        with an enquiry-specific summary and its own idempotency key.
        """
        from unittest.mock import MagicMock
        from app.services.whatsapp_department_service import WhatsAppDepartmentService

        ns = _ns()
        settings = ns.settings
        whatsapp_client = ns.whatsapp_client
        logger = ns.logger

        booking_ref = str(context.get("booking_ref") or context.get("bookingRef") or "").strip()
        if not booking_ref:
            logger.warning("[Customer WhatsApp] Enquiry acknowledgement skipped: missing reference")
            return {"status": "SKIPPED", "reason": "missing_booking_ref"}

        # Without a real ORM session the idempotency claim cannot be persisted, so a
        # send here could not be deduplicated (duplicate customer messages on retry).
        from sqlalchemy.orm import Session as _OrmSession
        if not isinstance(db, _OrmSession):
            logger.warning(
                "[Customer WhatsApp] Enquiry acknowledgement skipped for %s: no ORM session to record the claim",
                booking_ref,
            )
            return {"status": "SKIPPED", "reason": "no_orm_session", "booking_ref": booking_ref}

        booking = None
        if db is not None and not isinstance(db, MagicMock):
            try:
                from app.models.schema import Booking
                booking = db.scalar(select(Booking).where(Booking.booking_ref == booking_ref))
            except Exception as db_err:
                logger.debug("[Customer WhatsApp] DB lookup for booking %s: %s", booking_ref, db_err)

        if booking is not None and not isinstance(booking, MagicMock):
            from app.models.schema import BookingStatus
            if isinstance(booking.status, BookingStatus) and booking.status in (
                BookingStatus.CONFIRMED,
                BookingStatus.CANCELLED,
                BookingStatus.REJECTED,
                BookingStatus.COMPLETED,
            ):
                logger.info(
                    "[Customer WhatsApp] Enquiry acknowledgement skipped for %s: booking is already '%s'",
                    booking_ref,
                    booking.status.value if hasattr(booking.status, "value") else booking.status,
                )
                return {
                    "status": "SKIPPED",
                    "reason": "not_an_open_enquiry",
                    "booking_ref": booking_ref,
                }

        raw_phone = (
            context.get("passenger_phone")
            or context.get("passengerPhone")
            or context.get("phone")
            or context.get("contact_phone")
        )
        if not raw_phone and booking is not None and not isinstance(booking, MagicMock):
            raw_phone = getattr(booking, "passenger_phone", None)

        if not raw_phone or not str(raw_phone).strip():
            logger.warning(
                "[Customer WhatsApp] Enquiry acknowledgement skipped for %s: missing customer mobile number",
                booking_ref,
            )
            return {"status": "SKIPPED", "reason": "missing_customer_phone", "booking_ref": booking_ref}

        clean_phone = WhatsAppDepartmentService.normalize_phone_number(raw_phone)
        if not clean_phone or len(clean_phone) < 10:
            logger.warning(
                "[Customer WhatsApp] Enquiry acknowledgement failed for %s: invalid customer phone '%s'",
                booking_ref,
                WhatsAppDepartmentService._mask_phone(str(raw_phone)),
            )
            return {"status": "FAILED", "reason": "invalid_customer_phone", "booking_ref": booking_ref}

        if not whatsapp_client.is_configured():
            logger.warning(
                "[Customer WhatsApp] Enquiry acknowledgement skipped for %s: Meta WhatsApp Cloud API credentials not configured",
                booking_ref,
            )
            return {"status": "BYPASSED", "reason": "whatsapp_client_unconfigured", "booking_ref": booking_ref}

        meta_details = (context.get("details") or {}) if isinstance(context.get("details"), dict) else {}
        options = (context.get("service_options") or {}) if isinstance(context.get("service_options"), dict) else {}
        booking_meta = (getattr(booking, "metadata_json", None) or {}) if booking is not None and not isinstance(booking, MagicMock) else {}
        if not isinstance(booking_meta, dict):
            booking_meta = {}
        booking_details = booking_meta.get("details") or {}
        if not isinstance(booking_details, dict):
            booking_details = {}

        customer_name = str(
            context.get("passenger_name")
            or context.get("passengerName")
            or (getattr(booking, "passenger_name", None) if booking is not None and not isinstance(booking, MagicMock) else None)
            or "Valued Guest"
        ).strip()

        service_type = str(
            context.get("service_name")
            or context.get("service_type")
            or context.get("serviceType")
            or meta_details.get("service_type")
            or (getattr(booking, "service_type", None) if booking is not None and not isinstance(booking, MagicMock) else None)
            or "Travel Service"
        ).strip()

        service_category = str(
            context.get("service_category")
            or context.get("serviceCategory")
            or booking_meta.get("service_category")
            or (getattr(booking, "service_category", None) if booking is not None and not isinstance(booking, MagicMock) else None)
            or "Enquiry"
        ).strip()

        route_bits = [
            str(context.get("origin_code") or context.get("origin") or "").strip(),
            str(context.get("dest_code") or context.get("destination") or "").strip(),
        ]
        route = " → ".join(bit for bit in route_bits if bit)

        date_raw = (
            context.get("departure_time")
            or context.get("service_date")
            or meta_details.get("service_date")
            or booking_details.get("service_date")
        )
        date_time_str = None
        if date_raw:
            if isinstance(date_raw, datetime):
                date_time_str = date_raw.strftime("%d %b %Y, %H:%M")
            else:
                date_time_str = str(date_raw).strip()[:40]

        support_phone = getattr(settings, "SUPPORT_PHONE", None) or os.getenv("SUPPORT_PHONE") or "+91 9599087959"

        message_body = "\n".join(
            [
                "✈️ *Shafsky Aviation Services — Enquiry Received*",
                "",
                f"Dear *{customer_name}*,",
                "",
                "Thank you for contacting Shafsky Aviation Services. Your request has been received and is with our desk.",
                "",
                f"• *Enquiry Reference:* {booking_ref}",
                f"• *Service:* {service_type}",
            ]
            + ([f"• *Route:* {route}"] if route else [])
            + ([f"• *Date:* {date_time_str}"] if date_time_str else [])
            + [
                "• *Status:* Enquiry received — quotation to follow (no payment has been taken)",
                "",
                f"Our 24/7 command desk is at your disposal: {support_phone}.",
            ]
        )

        template_param = build_customer_summary(
            reference_label="Enquiry reference",
            reference=booking_ref,
            guest_name=customer_name,
            service=f"{service_category} - {service_type}" if service_category and service_category not in service_type else service_type,
            airport=route or None,
            location_label="Route",
            date_time=date_time_str,
            status_label="Enquiry received, quotation to follow (no payment taken)",
        )

        payload = {
            "booking_ref": booking_ref,
            "recipient_masked": WhatsAppDepartmentService._mask_phone(clean_phone),
            "passenger_name": customer_name,
            "service_category": service_category,
            "service_type": service_type,
            "date_time": date_time_str,
            "status": "ENQUIRY",
            "support_phone": support_phone,
        }

        return cls._dispatch_customer_whatsapp(
            db,
            booking_ref=booking_ref,
            clean_phone=clean_phone,
            template_type=cls.WHATSAPP_CUSTOMER_ENQUIRY_TEMPLATE_TYPE,
            message_body=message_body,
            template_param=template_param,
            payload=payload,
        )

    @classmethod
    def _already_sent_whatsapp_invoice(cls, db: Session, booking_ref: str) -> bool:
        if not booking_ref:
            return False
        records = list(
            db.scalars(
                select(NotificationRecord)
                .where(
                    NotificationRecord.template_type == cls.WHATSAPP_INVOICE_TEMPLATE,
                    NotificationRecord.status.in_([NotificationStatus.DELIVERED, NotificationStatus.SENDING]),
                )
                .order_by(desc(NotificationRecord.created_at))
                .limit(30)
            ).all()
        )
        for rec in records:
            payload = rec.payload or {}
            ref = str(payload.get("booking_ref") or payload.get("bookingRef") or "")
            if ref == booking_ref:
                return True
        return False

    @classmethod
    def send_whatsapp_invoice_document(
        cls,
        db: Session,
        *,
        booking_ref: str,
        recipient_phone: str,
        invoice_number: str,
        pdf_bytes: Optional[bytes] = None,
        document_url: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Send the Tax Invoice PDF as a WhatsApp DOCUMENT message.
        Atomically claimed and idempotent per booking_ref + invoice_number.
        Marks DELIVERED only after Meta accepts the message.
        Failures are retryable and never raise to the payment caller.
        """
        ns = _ns()
        whatsapp_client = ns.whatsapp_client
        logger = ns.logger

        booking_ref = (booking_ref or "").strip()
        phone = (recipient_phone or "").strip()
        if not booking_ref or not phone:
            return {"status": "FAILED", "error": "missing_recipient"}

        filename = f"Shafsky-Aviation-Tax-Invoice-{invoice_number}.pdf"
        caption = (
            f"Shafsky Aviation Tax Invoice\n"
            f"Invoice: {invoice_number}\n"
            f"Booking: {booking_ref}"
        )
        payload = {
            "booking_ref": booking_ref,
            "invoice_number": invoice_number,
            "filename": filename,
        }

        record, action = cls._acquire_notification_claim(
            db,
            template_type=cls.WHATSAPP_INVOICE_TEMPLATE,
            booking_ref=booking_ref,
            recipient=phone,
            channel="WHATSAPP",
            payload=payload,
        )

        if action in ("ALREADY_DELIVERED", "IN_PROGRESS"):
            logger.info("Skipping duplicate WhatsApp invoice PDF (%s)", action, extra={"booking_ref": booking_ref})
            return {"status": "SKIPPED", "reason": "duplicate", "claim_status": action}
        if action == "MAX_ATTEMPTS_EXCEEDED":
            logger.warning("Max attempts exceeded for WhatsApp invoice PDF", extra={"booking_ref": booking_ref})
            return {"status": "FAILED", "error": "max_attempts_exceeded"}

        if not record:
            return {"status": "FAILED", "error": "claim_failed"}

        try:
            result = whatsapp_client.send_document_message(
                phone,
                filename=filename,
                caption=caption,
                document_url=document_url,
                pdf_bytes=pdf_bytes,
            )
        except Exception as exc:
            logger.warning("[WhatsApp Invoice] Document send exception: %s", type(exc).__name__)
            result = {"success": False, "error": "exception", "status": "failed"}

        now = datetime.now(timezone.utc)
        if result.get("success"):
            record.status = NotificationStatus.DELIVERED
            record.delivered_at = now
            msg_id = result.get("message_id")
            record.message_id = str(msg_id) if msg_id else None
            record.error_log = None
            try:
                db.commit()
            except Exception:
                db.rollback()
            return {"status": "DELIVERED", "message_id": result.get("message_id")}

        record.status = NotificationStatus.FAILED
        record.error_log = str(result.get("error") or result.get("status") or "whatsapp_document_failed")[:400]
        record.updated_at = now
        try:
            db.commit()
        except Exception:
            db.rollback()
        return {"status": "FAILED", "error": record.error_log}
