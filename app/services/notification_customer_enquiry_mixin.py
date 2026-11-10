"""
Customer WhatsApp enquiry notification dispatch mixin for NotificationService.
"""

import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.models.schema import NotificationRecord, NotificationStatus
from app.services.notification_customer_formatting import (
    _ns,
    TEMPLATE_PARAM_MAX_CHARS,
    build_template_parameter,
    _format_datetime,
    build_customer_summary,
)


class NotificationCustomerEnquiryMixin:
    """Enquiry notification handlers for Round-Trip, Ticketing, Lounge, and General Enquiries."""
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

        from app.models.notification_routing import ROUTING_MODE_DATABASE
        from app.services.notification_routing_service import (
            deliver_operations_text,
            resolve_operations_recipients,
        )

        routing = resolve_operations_recipients("Round Trip")
        if routing["routing_mode"] != ROUTING_MODE_DATABASE and not officer_phone:
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

        if routing["routing_mode"] == ROUTING_MODE_DATABASE:
            logger.info("Dispatching Round Trip enquiry through database routing for %s", booking_ref)
            return deliver_operations_text(
                routing["recipients"],
                message_body,
                lambda phone, body: whatsapp_client.send_text_message(phone, body),
            )

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

        from app.models.notification_routing import ROUTING_MODE_DATABASE
        from app.services.notification_routing_service import (
            deliver_operations_text,
            resolve_operations_recipients,
        )

        routing = resolve_operations_recipients("Ticketing")
        if routing["routing_mode"] != ROUTING_MODE_DATABASE and not officer_phone:
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

        if routing["routing_mode"] == ROUTING_MODE_DATABASE:
            logger.info("Dispatching Ticketing enquiry through database routing for %s", booking_ref)
            return deliver_operations_text(
                routing["recipients"],
                message_body,
                lambda phone, body: whatsapp_client.send_text_message(phone, body),
            )

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

        from app.models.notification_routing import ROUTING_MODE_DATABASE
        from app.services.notification_routing_service import (
            deliver_operations_text,
            resolve_operations_recipients,
        )

        routing = resolve_operations_recipients("Lounge")
        if routing["routing_mode"] != ROUTING_MODE_DATABASE and not officer_phone:
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

        if routing["routing_mode"] == ROUTING_MODE_DATABASE:
            logger.info("Dispatching Lounge enquiry through database routing for %s", booking_ref)
            return deliver_operations_text(
                routing["recipients"],
                message_body,
                lambda phone, body: whatsapp_client.send_text_message(phone, body),
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

        journey_type_val = str(context.get("journey_type") or (booking.journey_type if booking and hasattr(booking, "journey_type") else "")).strip().upper()
        date_raw = (
            (context.get("arrival_time") or (booking.arrival_time if booking else None) if journey_type_val == "ARRIVAL" else None)
            or context.get("departure_time")
            or context.get("arrival_time")
            or context.get("service_date")
            or meta_details.get("service_date")
            or booking_details.get("service_date")
            or (booking.departure_time if booking else None)
            or (booking.arrival_time if booking else None)
        )
        airport_hint = (
            (context.get("dest_code") or context.get("destination") or (getattr(booking, "dest_code", None) if booking else None))
            if journey_type_val == "ARRIVAL"
            else (context.get("origin_code") or context.get("origin") or context.get("airport_code") or (getattr(booking, "origin_code", None) if booking else None))
        )
        date_time_str = _format_datetime(date_raw, airport_code=airport_hint)

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

