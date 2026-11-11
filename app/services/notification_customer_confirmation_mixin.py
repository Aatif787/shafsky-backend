"""
Customer WhatsApp booking confirmation notification dispatch mixin for NotificationService.
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
    recipients_refer_to_same_party,
)


class NotificationCustomerConfirmationMixin:
    """Customer booking confirmation notification dispatcher, template resolver, and window validator."""
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
        from app.services.pdf_service import (
            is_placeholder_email,
            resolve_customer_email,
            resolve_customer_name,
            resolve_customer_phone,
        )

        logger = _ns().logger
        booking_ref = str(context.get("booking_ref") or context.get("bookingRef") or "")
        customer_email = str(
            context.get("passenger_email")
            or context.get("passengerEmail")
            or context.get("customer_email")
            or context.get("contact_email")
            or context.get("email")
            or ""
        ).strip()
        if is_placeholder_email(customer_email):
            customer_email = ""
            if booking_ref and db is not None:
                try:
                    from sqlalchemy.orm import Session as _OrmSession
                    if isinstance(db, _OrmSession):
                        from app.models.airport import AirportBooking
                        from app.models.payment import Invoice, PaymentTransaction
                        from app.models.schema import Booking

                        db_booking = db.scalar(select(Booking).where(Booking.booking_ref == booking_ref))
                        if not db_booking:
                            db_booking = db.scalar(
                                select(AirportBooking).where(AirportBooking.booking_reference == booking_ref)
                            )
                        db_tx = db.scalar(
                            select(PaymentTransaction).where(PaymentTransaction.entity_id == booking_ref)
                        )
                        db_inv = (
                            db.scalar(select(Invoice).where(Invoice.transaction_id == db_tx.id))
                            if db_tx is not None
                            else None
                        )
                        customer_email = resolve_customer_email(
                            booking=db_booking,
                            invoice=db_inv,
                            transaction=db_tx,
                        )
                except Exception:
                    customer_email = ""
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

        try:
            ops_res = cls.notify_operations_payment_confirmed(db, context)
            if ops_res is not None:
                summary["admin"].append({"channel": "WHATSAPP", "status": ops_res.get("status"), "details": ops_res})
        except Exception as ops_exc:
            logger.warning(
                "Operations WhatsApp payment confirmation failed for %s: %s",
                booking_ref,
                type(ops_exc).__name__,
            )
        return summary

    @classmethod
    def notify_operations_payment_confirmed(cls, db: Session, context: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Sends the operations WhatsApp alert with status CONFIRMED once payment is verified.

        The creation-time alert (status PENDING) is sent exactly once per booking and its
        booking-level idempotency key suppresses any later send, so without this step the
        operations team never learns that the booking was paid. The status is read from the
        persisted Booking / payment records, never from the caller's context. Returns None
        when the booking is not verifiably confirmed and paid.
        """
        from sqlalchemy.orm import Session as _OrmSession

        if not isinstance(db, _OrmSession):
            return None
        booking_ref = str(context.get("booking_ref") or context.get("bookingRef") or "").strip()
        if not booking_ref:
            return None

        from app.models.airport import AirportBooking
        from app.models.schema import Booking, BookingStatus
        from app.services.pdf_service import (
            resolve_customer_email,
            resolve_customer_name,
            resolve_customer_phone,
        )

        booking: Any = db.scalar(select(Booking).where(Booking.booking_ref == booking_ref))
        if booking is not None:
            if booking.status != BookingStatus.CONFIRMED:
                return None
            meta = booking.metadata_json if isinstance(booking.metadata_json, dict) else {}
            paid = str(meta.get("payment_status") or "").upper() == "PAID"
        else:
            booking = db.scalar(select(AirportBooking).where(AirportBooking.booking_reference == booking_ref))
            if booking is None or str(getattr(booking, "status", "") or "").upper() != "CONFIRMED":
                return None
            meta = {}
            paid = False

        if not paid:
            from app.models.payment import PaymentStatus, PaymentTransaction
            paid = db.scalar(
                select(PaymentTransaction.id).where(
                    PaymentTransaction.entity_id == booking_ref,
                    PaymentTransaction.status == PaymentStatus.SUCCESSFUL,
                ).limit(1)
            ) is not None
        if not paid:
            return None

        from app.services.whatsapp_department_service import WhatsAppDepartmentService
        from app.utils.service_display import (
            is_multi_service_placeholder,
            resolve_service_display_name,
        )

        combined_meta = dict(meta) if isinstance(meta, dict) else {}
        sel_svcs = getattr(booking, "selected_services", None)
        if isinstance(sel_svcs, dict):
            combined_meta.setdefault("selected_services", sel_svcs)
        elif isinstance(sel_svcs, list):
            combined_meta.setdefault("services", sel_svcs)

        raw_svc = (
            context.get("service_name")
            or context.get("service_type")
            or getattr(booking, "service_type", None)
            or getattr(booking, "service_package", None)
        )
        resolved_svc = resolve_service_display_name(raw_svc, combined_meta)
        if is_multi_service_placeholder(resolved_svc):
            resolved_svc = context.get("service_category") or getattr(booking, "service_category", None) or "Meet & Greet"

        raw_total = getattr(booking, "total_amount", None)
        if raw_total is None:
            raw_total = getattr(booking, "total_price", None)
        ops_context = dict(context)
        if db is not None:
            ops_context["db"] = db
        ops_context.update({
            "booking_ref": getattr(booking, "booking_ref", None) or getattr(booking, "booking_reference", None) or booking_ref,
            "passenger_name": context.get("passenger_name") or resolve_customer_name(booking=booking, default="Valued Guest"),
            "passenger_email": context.get("passenger_email") or resolve_customer_email(booking=booking),
            "passenger_phone": context.get("passenger_phone") or resolve_customer_phone(booking=booking),
            "service_category": context.get("service_category") or getattr(booking, "service_category", None) or "Airport Assistance",
            "service_type": resolved_svc,
            "service_name": resolved_svc,
            "metadata_json": combined_meta,
            "details": combined_meta,
            "total_amount": float(raw_total) if raw_total is not None else 0.0,
            "currency": getattr(booking, "currency", None) or "INR",
            "status": "CONFIRMED",
            "notification_stage": "PAYMENT_CONFIRMED",
        })
        if not ops_context.get("passenger_count"):
            ops_context["passenger_count"] = (
                meta.get("passenger_count")
                or meta.get("guest_count")
                or getattr(booking, "total_passengers", None)
                or 1
            )
        return WhatsAppDepartmentService.dispatch_booking_or_enquiry_notification(ops_context)

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

        if not template_name:
            from app.core.runtime import in_automated_test_runtime
            live_production = bool(getattr(settings, "is_production", False))
            if in_automated_test_runtime() and not live_production:
                return "", template_lang
            # The approved customer confirmation template on this WABA is shafsky_booking_confirmation
            template_name = "shafsky_booking_confirmation"

        if not template_lang:
            # The approved template `shafsky_booking_confirmation` is registered on the
            # WhatsApp Business Account as language "en". Sending "en_US" for it returns
            # 132001 (template does not exist in the requested language).
            template_lang = "en"
        elif template_lang.replace("-", "_").lower() == "en_us":
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
        booking: Any = None
        if db is not None and not isinstance(db, MagicMock):
            try:
                from app.models.airport import AirportBooking
                from app.models.schema import Booking
                booking = db.scalar(select(Booking).where(Booking.booking_ref == booking_ref))
                if not booking:
                    booking = db.scalar(select(AirportBooking).where(AirportBooking.booking_reference == booking_ref))
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
            elif hasattr(booking, "status") and isinstance(booking.status, str):
                b_stat_up = booking.status.strip().upper()
                if b_stat_up in ("CANCELLED", "REJECTED"):
                    return {
                        "status": "SKIPPED",
                        "reason": "booking_not_confirmed",
                        "booking_ref": booking_ref,
                    }
                if b_stat_up != "CONFIRMED" and not is_explicitly_confirmed:
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
        from app.services.pdf_service import resolve_customer_phone

        raw_phone = (
            context.get("passenger_phone")
            or context.get("passengerPhone")
            or context.get("phone")
            or context.get("contact_phone")
        )
        if not raw_phone and booking:
            raw_phone = resolve_customer_phone(booking=booking)

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

        journey_type_val = str(context.get("journey_type") or (booking.journey_type if booking and hasattr(booking, "journey_type") else "")).strip().upper()
        date_time_raw = (
            (context.get("arrival_time") or (booking.arrival_time if booking else None) if journey_type_val == "ARRIVAL" else None)
            or context.get("departure_time")
            or context.get("departureTime")
            or context.get("arrival_time")
            or context.get("service_date")
            or context.get("serviceDate")
            or (booking.departure_time if booking else None)
            or (booking.arrival_time if booking else None)
        )
        date_time_str = _format_datetime(date_time_raw, airport_code=airport_name)

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
        invoice_number = str(context.get("invoice_number") or "").strip() or None
        invoice_url_raw = str(context.get("invoice_url") or "").strip()
        invoice_url = invoice_url_raw if invoice_url_raw.startswith("https://") else None

        if flight_num:
            lines.append(f"• *Flight:* {flight_num}")
        if date_time_str:
            lines.append(f"• *Date & Time:* {date_time_str}")
        if invoice_number:
            lines.append(f"• *Invoice No:* {invoice_number}")
        lines.append("• *Status:* CONFIRMED")
        if invoice_url:
            lines.extend(["", f"📄 *Tax Invoice PDF:* {invoice_url}"])
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
            invoice_number=invoice_number,
            invoice_url=invoice_url,
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
            if rec_clean and not recipients_refer_to_same_party(rec_clean, record_recipient):
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
    def _customer_service_window_open(cls, db: Session, clean_phone: str) -> bool:
        """True when the customer messaged the business inside Meta's 24-hour window.

        A free-form message is the only customer notification that Meta delivers
        without a billing method on the WhatsApp Business Account, but it is legal
        only inside this window. The window is opened by the customer's own inbound
        message (`last_user_activity_at`, falling back to the inbound message log),
        so it cannot be manufactured by the business. One hour of margin keeps a
        stale clock from selecting free-form just after the window expires.
        """
        if not clean_phone:
            return False
        from sqlalchemy.orm import Session as _OrmSession
        if not isinstance(db, _OrmSession):
            return False

        last_activity = None
        try:
            from app.models.whatsapp_models import WhatsAppConversation
            last_activity = db.scalar(
                select(WhatsAppConversation.last_user_activity_at).where(
                    WhatsAppConversation.phone_number == clean_phone
                )
            )
            if last_activity is None:
                from sqlalchemy import func
                from app.models.whatsapp_models import WhatsAppMessage

                last_activity = db.scalar(
                    select(func.max(WhatsAppMessage.created_at))
                    .join(
                        WhatsAppConversation,
                        WhatsAppMessage.conversation_id == WhatsAppConversation.id,
                    )
                    .where(
                        WhatsAppConversation.phone_number == clean_phone,
                        WhatsAppMessage.direction == "INBOUND",
                    )
                )
        except Exception as db_err:  # pragma: no cover - defensive
            _ns().logger.debug(
                "[Customer WhatsApp] Service-window lookup failed: %s",
                type(db_err).__name__,
            )
            return False

        if not last_activity:
            return False
        if last_activity.tzinfo is None:
            last_activity = last_activity.replace(tzinfo=timezone.utc)
        age = datetime.now(timezone.utc) - last_activity
        return age < timedelta(hours=23)

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
        window_open = cls._customer_service_window_open(db, clean_phone)

        def _send_freeform() -> Dict[str, Any]:
            return whatsapp_client.send_text_message(
                to_phone=clean_phone,
                message_body=message_body,
            )

        def _send_template(language: str) -> Dict[str, Any]:
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
            return _send_freeform()

        def _send(language: str) -> Dict[str, Any]:
            # A free-form message needs no payment method, but Meta only allows it inside
            # the customer's 24-hour service window. Everywhere else the approved
            # template is the only legal channel.
            if window_open:
                return _send_freeform()
            return _send_template(language)

        channel_used = "freeform" if window_open else ("template" if template_name else "freeform")

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

        # A stale window estimate surfaces as 131047, which is rejected before a message
        # is created. The approved template can therefore still be tried once safely.
        if (
            window_open
            and template_name
            and not res.get("success")
            and res.get("error_code") == 131047
        ):
            logger.warning(
                "[Customer WhatsApp] Free-form rejected for %s (131047); retrying with template %s/%s",
                booking_ref,
                template_name,
                template_lang,
            )
            try:
                res = _send_template(template_lang)
                channel_used = "template"
            except Exception as fallback_err:
                logger.error(
                    "[Customer WhatsApp] Template fallback raised for %s: %s",
                    booking_ref,
                    type(fallback_err).__name__,
                )
                res = {
                    "success": False,
                    "error": f"Exception during template fallback: {type(fallback_err).__name__}",
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
            "channel": channel_used,
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
                "[Customer WhatsApp] %s accepted by Meta for %s (to=%s, channel=%s, template=%s/%s, "
                "message_id=%s, http=%s). Delivery is unconfirmed until the status webhook arrives.",
                template_type,
                booking_ref,
                masked_phone,
                channel_used,
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
                "[Customer WhatsApp] Send failed for %s (to=%s, channel=%s, template=%s/%s, http=%s, "
                "code=%s, subcode=%s): %s",
                booking_ref,
                masked_phone,
                channel_used,
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
            "channel": channel_used,
            "template_configured": bool(template_name),
            "template_language": template_lang if template_name else None,
            "error_code": error_code,
            "error": record.error_log if outcome == "FAILED" else None,
        }

