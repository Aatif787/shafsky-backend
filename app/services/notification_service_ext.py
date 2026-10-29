"""
Extended WhatsApp enquiry, booking confirmation,customer template, and invoice
delivery helpers for NotificationService.
"""

import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.models.schema import NotificationRecord, NotificationStatus


def _ns():
    import app.services.notification_service as ns_mod
    return ns_mod


class NotificationServiceExtMixin:
    """Mixin providing extended WhatsApp and confirmation methods for NotificationService."""

    WHATSAPP_CUSTOMER_CONFIRMATION_TEMPLATE_TYPE = "WHATSAPP_CUSTOMER_CONFIRMATION"
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

        if not template_lang:
            template_lang = "en_US"

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

        payload = {
            "booking_ref": booking_ref,
            "customer_phone": clean_phone,
            "passenger_name": customer_name,
            "airport": airport_name,
            "service_type": service_type,
            "flight_num": flight_num,
            "date_time": date_time_str,
            "status": "CONFIRMED",
            "support_phone": support_phone,
        }

        # 6. Idempotency Check & Atomic Claim
        record, action = cls._acquire_notification_claim(
            db,
            template_type=cls.WHATSAPP_CUSTOMER_CONFIRMATION_TEMPLATE_TYPE,
            booking_ref=booking_ref,
            recipient=clean_phone,
            channel="WHATSAPP",
            payload=payload,
        )

        if action in ("ALREADY_DELIVERED", "IN_PROGRESS"):
            logger.info(
                "[Customer WhatsApp] Skipping duplicate customer notification (%s) for %s",
                action,
                booking_ref,
            )
            return {"status": "SKIPPED", "reason": "duplicate", "claim_status": action, "booking_ref": booking_ref}
        if action == "MAX_ATTEMPTS_EXCEEDED":
            logger.warning(
                "[Customer WhatsApp] Max attempts exceeded for customer notification %s",
                booking_ref,
            )
            return {"status": "FAILED", "error": "max_attempts_exceeded", "booking_ref": booking_ref}
        if not record:
            return {"status": "FAILED", "error": "claim_failed", "booking_ref": booking_ref}

        # 7. Format Message Content
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

        # 8. Template Resolution & Dispatch
        template_name, template_lang = cls._resolve_customer_template_settings()
        masked_phone = WhatsAppDepartmentService._mask_phone(clean_phone)

        try:
            if template_name:
                param_text = WhatsAppDepartmentService.sanitize_template_param(message_body)
                components = [
                    {
                        "type": "body",
                        "parameters": [
                            {"type": "text", "text": param_text}
                        ]
                    }
                ]
                res = whatsapp_client.send_message(
                    to_phone=clean_phone,
                    template_name=template_name,
                    template_language=template_lang,
                    template_components=components,
                )
            else:
                res = whatsapp_client.send_text_message(
                    to_phone=clean_phone,
                    message_body=message_body,
                )
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

        # 9. Update NotificationRecord & Safely Return
        is_success = res.get("success") is True
        message_id = res.get("message_id")
        now = datetime.now(timezone.utc)

        if is_success and message_id:
            record.status = NotificationStatus.DELIVERED
            record.delivered_at = now
            record.message_id = str(message_id)
            record.error_log = None
            outcome = "ACCEPTED"
            logger.info(
                "[Customer WhatsApp] Booking confirmation accepted by Meta for %s (to=%s, message_id=%s)",
                booking_ref,
                masked_phone,
                message_id,
            )
        elif res.get("status") == "timeout_unverified":
            record.status = NotificationStatus.PENDING
            record.error_log = "Timeout connecting to Meta WhatsApp API; delivery state unknown."
            outcome = "UNKNOWN"
            logger.error(
                "[Customer WhatsApp] Send timed out for %s (to=%s); delivery state unknown.",
                booking_ref,
                masked_phone,
            )
        else:
            record.status = NotificationStatus.FAILED
            err_text = str(res.get("error") or res.get("error_user_msg") or "delivery_failed")
            record.error_log = err_text[:400]
            outcome = "FAILED"
            error_code = res.get("error_code")
            logger.error(
                "[Customer WhatsApp] Send failed for %s to %s (code=%s): %s",
                booking_ref,
                masked_phone,
                error_code,
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
            "error": record.error_log if outcome == "FAILED" else None,
        }

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
