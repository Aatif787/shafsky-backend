"""
Authoritative Tax Invoice PDF generation and storage for paid bookings.

Layout is adapted from the existing frontend TAX INVOICE
(src/lib/booking-documents.functions.ts and pdf-engine.server.ts).
Payment confirmation is never rolled back if document generation fails.
"""

from __future__ import annotations

import io
import logging
import os
import re
import threading
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

import httpx
from reportlab.lib.colors import Color, white
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from sqlalchemy import select
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

STORAGE_BUCKET = "booking-docs"
SIGNED_URL_TTL_SECONDS = 60 * 60 * 24 * 7  # 7 days

TEAL = Color(0.05, 0.35, 0.43)
INK = Color(0.08, 0.1, 0.13)
MUTED = Color(0.4, 0.45, 0.5)
SILVER = Color(0.85, 0.87, 0.9)
FOOTER_BG = Color(0.97, 0.98, 0.99)

SUPPORT_PHONE = "+91 9599087959"
SUPPORT_EMAIL = "ops@shafskyaviation.com"


def _safe_text(value: Any, default: str = "") -> str:
    if value is None:
        return default
    text = str(value).strip()
    return text or default


def _money(amount: Any, currency: str = "INR") -> str:
    try:
        return f"{currency} {float(amount):,.2f}"
    except (TypeError, ValueError):
        return f"{currency} 0.00"


def _safe_path_part(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", (value or "").strip())
    cleaned = cleaned.strip(".-") or "unknown"
    return cleaned[:80]


def _fmt_dt(value: Any) -> str:
    if not value:
        return ""
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).strftime("%d %b %Y %H:%M UTC")
    text = str(value).strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return parsed.astimezone(timezone.utc).strftime("%d %b %Y %H:%M UTC")
    except Exception:
        return text[:32]


def _razorpay_ids(transaction) -> Tuple[str, str]:
    """Return (payment_id, order_id) from transaction fields only — never secrets."""
    payment_id = ""
    order_id = ""
    gid = _safe_text(getattr(transaction, "gateway_payment_id", None))
    if gid.startswith("pay_"):
        payment_id = gid
    elif gid.startswith("order_"):
        order_id = gid

    resp = getattr(transaction, "gateway_response", None) or {}
    if not isinstance(resp, dict):
        return payment_id, order_id

    payload = resp.get("payload") if isinstance(resp.get("payload"), dict) else {}
    pay_ent = (payload.get("payment") or {}).get("entity") if isinstance(payload.get("payment"), dict) else {}
    ord_ent = (payload.get("order") or {}).get("entity") if isinstance(payload.get("order"), dict) else {}
    if not isinstance(pay_ent, dict):
        pay_ent = {}
    if not isinstance(ord_ent, dict):
        ord_ent = {}

    if not payment_id:
        candidate = _safe_text(pay_ent.get("id") or resp.get("razorpay_payment_id") or resp.get("payment_id"))
        if candidate.startswith("pay_"):
            payment_id = candidate
    if not order_id:
        candidate = _safe_text(
            pay_ent.get("order_id")
            or ord_ent.get("id")
            or resp.get("razorpay_order_id")
            or resp.get("order_id")
        )
        if candidate.startswith("order_"):
            order_id = candidate
    return payment_id, order_id


def _booking_meta(booking) -> Dict[str, str]:
    meta = getattr(booking, "metadata_json", None) or {}
    if not isinstance(meta, dict):
        meta = {}
    airport = _safe_text(
        meta.get("service_airport")
        or getattr(booking, "origin_code", None)
        or getattr(booking, "dest_code", None)
    )
    journey = _safe_text(meta.get("journey_type") or meta.get("direction") or getattr(booking, "service_type", None))
    flight_type = _safe_text(meta.get("flight_type") or meta.get("travel_type"))
    service_name = _safe_text(meta.get("package") or getattr(booking, "service_type", None))
    travel_dt = getattr(booking, "departure_time", None) or getattr(booking, "arrival_time", None)
    pax = meta.get("pax_adults") or meta.get("guest_count") or 1
    return {
        "airport": airport,
        "journey": journey,
        "flight_type": flight_type,
        "service_name": service_name,
        "travel_date": _fmt_dt(travel_dt),
        "pax": str(pax),
        "terminal": _safe_text(meta.get("terminal")),
    }


def build_invoice_storage_path(booking_ref: str, invoice_number: str) -> str:
    return f"invoices/{_safe_path_part(booking_ref)}/{_safe_path_part(invoice_number)}.pdf"


def generate_tax_invoice_pdf(data: Dict[str, Any]) -> bytes:
    """Render a professional A4 TAX INVOICE matching the existing frontend document."""
    buffer = io.BytesIO()
    page_w, page_h = A4
    c = canvas.Canvas(buffer, pagesize=A4)
    c.setPageCompression(0)

    # Header bar
    c.setFillColor(TEAL)
    c.rect(0, page_h - 96, page_w, 96, fill=1, stroke=0)
    c.setFillColor(white)
    c.setFont("Helvetica-Bold", 18)
    c.drawString(40, page_h - 50, "SHAFSKY AVIATION SERVICES")
    c.setFont("Helvetica", 9)
    c.setFillColor(Color(0.85, 0.95, 0.95))
    c.drawString(40, page_h - 72, "Private Aviation · Meet & Greet · Ground Services")
    c.setFillColor(white)
    c.setFont("Helvetica-Bold", 18)
    c.drawRightString(page_w - 40, page_h - 50, "TAX INVOICE")
    c.setFont("Helvetica", 9)
    c.setFillColor(Color(0.85, 0.95, 0.95))
    c.drawRightString(page_w - 40, page_h - 72, f"Ref: {_safe_text(data.get('booking_ref'), 'N/A')}")

    y = page_h - 130
    c.setFillColor(MUTED)
    c.setFont("Helvetica", 10)
    c.drawString(40, y, f"Issued: {_safe_text(data.get('invoice_date'))}")
    c.drawRightString(page_w - 40, y, f"Invoice No: {_safe_text(data.get('invoice_number'))}")

    y -= 36
    c.setFont("Helvetica-Bold", 9)
    c.drawString(40, y, "BILL TO")
    y -= 16
    c.setFillColor(INK)
    c.setFont("Helvetica-Bold", 12)
    c.drawString(40, y, _safe_text(data.get("customer_name"), "Valued Guest"))
    y -= 14
    c.setFont("Helvetica", 10)
    c.drawString(40, y, _safe_text(data.get("customer_email")))
    y -= 12
    c.drawString(40, y, _safe_text(data.get("customer_phone")))

    y -= 28
    box_h = 118
    c.setStrokeColor(SILVER)
    c.setLineWidth(1)
    c.rect(40, y - box_h, page_w - 80, box_h, fill=0, stroke=1)
    c.setFillColor(MUTED)
    c.setFont("Helvetica-Bold", 9)
    c.drawString(52, y - 14, "TRIP / SERVICE DETAILS")
    c.setFillColor(INK)
    c.setFont("Helvetica-Bold", 12)
    origin = _safe_text(data.get("origin"), "—")
    dest = _safe_text(data.get("destination"), "—")
    c.drawString(52, y - 34, f"{origin}  →  {dest}")
    c.setFont("Helvetica", 10)
    lines = [
        f"Airport: {_safe_text(data.get('airport'), '—')}",
        f"Journey: {_safe_text(data.get('journey_type'), '—')}    Flight type: {_safe_text(data.get('flight_type'), '—')}",
        f"Flight: {_safe_text(data.get('flight_num'), '—')}    Travel date: {_safe_text(data.get('travel_date'), '—')}",
        f"Passengers: {_safe_text(data.get('pax'), '1')}"
        + (f"    Terminal: {data.get('terminal')}" if data.get("terminal") else ""),
    ]
    ly = y - 52
    for line in lines:
        c.drawString(52, ly, line)
        ly -= 14

    y = y - box_h - 28
    c.setFillColor(MUTED)
    c.setFont("Helvetica-Bold", 9)
    c.drawString(40, y, "DESCRIPTION")
    c.drawRightString(page_w - 40, y, "AMOUNT")
    y -= 8
    c.setStrokeColor(SILVER)
    c.line(40, y, page_w - 40, y)
    y -= 22
    c.setFillColor(INK)
    c.setFont("Helvetica", 11)
    service_label = _safe_text(data.get("service_name") or data.get("service_type"), "Airport Assistance")
    desc = f"Concierge services rendered — {service_label}"
    c.drawString(40, y, desc[:90])
    currency = _safe_text(data.get("currency"), "INR")
    total_text = _money(data.get("total_amount"), currency)
    c.setFont("Helvetica-Bold", 11)
    c.drawRightString(page_w - 40, y, total_text)
    qty = 1
    try:
        qty = max(1, int(float(data.get("pax") or data.get("quantity") or 1)))
    except (TypeError, ValueError):
        qty = 1
    try:
        unit_price = float(data.get("total_amount") or 0) / float(qty)
    except (TypeError, ValueError, ZeroDivisionError):
        unit_price = 0.0
    y -= 14
    c.setFont("Helvetica", 9)
    c.setFillColor(MUTED)
    c.drawString(40, y, f"Qty: {qty}    Unit price: {_money(unit_price, currency)}    Line total: {total_text}")

    y -= 28
    c.setStrokeColor(SILVER)
    c.line(300, y + 16, page_w - 40, y + 16)
    c.setFont("Helvetica", 10)
    c.setFillColor(MUTED)
    c.drawString(300, y, "Subtotal / Taxable value:")
    c.setFillColor(INK)
    c.drawRightString(page_w - 40, y, _money(data.get("subtotal_amount"), currency))
    y -= 16
    tax_amt = 0.0
    try:
        tax_amt = float(data.get("tax_amount") or 0)
    except (TypeError, ValueError):
        tax_amt = 0.0
    c.setFillColor(MUTED)
    c.drawString(300, y, "CGST (9%):")
    c.setFillColor(INK)
    c.drawRightString(page_w - 40, y, _money(tax_amt / 2.0, currency))
    y -= 16
    c.setFillColor(MUTED)
    c.drawString(300, y, "SGST (9%):")
    c.setFillColor(INK)
    c.drawRightString(page_w - 40, y, _money(tax_amt / 2.0, currency))
    y -= 16
    c.setFillColor(MUTED)
    c.drawString(300, y, "GST / Tax:")
    c.setFillColor(INK)
    c.drawRightString(page_w - 40, y, _money(tax_amt, currency))

    y -= 22
    c.setStrokeColor(TEAL)
    c.setLineWidth(1.5)
    c.line(300, y + 10, page_w - 40, y + 10)
    c.setFillColor(INK)
    c.setFont("Helvetica-Bold", 12)
    c.drawString(300, y - 6, "TOTAL")
    c.setFillColor(TEAL)
    c.setFont("Helvetica-Bold", 14)
    c.drawRightString(page_w - 40, y - 6, total_text)

    y -= 48
    c.setFillColor(MUTED)
    c.setFont("Helvetica-Bold", 9)
    c.drawString(40, y, "PAYMENT")
    y -= 16
    c.setFillColor(INK)
    c.setFont("Helvetica", 10)
    c.drawString(40, y, f"Status: {_safe_text(data.get('payment_status'), 'PAID')}")
    y -= 14
    c.drawString(40, y, f"Amount paid: {total_text}")
    y -= 14
    c.drawString(40, y, f"Payment date: {_safe_text(data.get('payment_date'), '—')}")
    y -= 14
    c.drawString(40, y, f"Razorpay Payment ID: {_safe_text(data.get('razorpay_payment_id'), '—')}")
    y -= 14
    c.drawString(40, y, f"Razorpay Order ID: {_safe_text(data.get('razorpay_order_id'), '—')}")

    c.setFillColor(FOOTER_BG)
    c.rect(0, 0, page_w, 70, fill=1, stroke=0)
    c.setFillColor(MUTED)
    c.setFont("Helvetica", 9)
    c.drawString(40, 42, f"Shafsky Aviation Services Pvt Ltd  ·  {SUPPORT_PHONE}  ·  {SUPPORT_EMAIL}")
    c.drawString(40, 26, "Thank you for choosing Shafsky Airport Services.")

    c.showPage()
    c.save()
    return buffer.getvalue()


def _supabase_config() -> Tuple[str, str]:
    url = (os.getenv("SUPABASE_URL") or "").rstrip("/")
    key = (
        os.getenv("SUPABASE_SERVICE_ROLE_KEY")
        or os.getenv("SUPABASE_SECRET_KEY")
        or ""
    ).strip()
    return url, key


def storage_is_configured() -> bool:
    url, key = _supabase_config()
    return bool(url and key)


def _storage_headers(key: str) -> Dict[str, str]:
    return {
        "Authorization": f"Bearer {key}",
        "apikey": key,
    }


def upload_invoice_pdf(storage_path: str, pdf_bytes: bytes) -> bool:
    """Upload PDF to the private booking-docs bucket. Returns True on success."""
    url, key = _supabase_config()
    if not url or not key:
        logger.warning("[InvoicePDF] Supabase storage is not configured; skipping upload")
        return False
    if os.getenv("PYTEST_CURRENT_TEST") and os.getenv("INVOICE_STORAGE_ENABLED") != "1":
        logger.info("[InvoicePDF] Skipping remote storage upload during pytest")
        return False
    endpoint = f"{url}/storage/v1/object/{STORAGE_BUCKET}/{storage_path}"
    headers = {
        **_storage_headers(key),
        "Content-Type": "application/pdf",
        "x-upsert": "true",
    }
    try:
        with httpx.Client(timeout=20.0) as client:
            res = client.post(endpoint, headers=headers, content=pdf_bytes)
            if res.status_code in (200, 201):
                return True
            logger.error("[InvoicePDF] Storage upload failed (%s): %s", res.status_code, (res.text or "")[:300])
            return False
    except Exception as err:
        logger.error("[InvoicePDF] Storage upload exception: %s", type(err).__name__)
        return False


def create_signed_invoice_url(storage_path: str) -> Optional[str]:
    url, key = _supabase_config()
    if not url or not key or not storage_path:
        return None
    if os.getenv("PYTEST_CURRENT_TEST") and os.getenv("INVOICE_STORAGE_ENABLED") != "1":
        return None
    endpoint = f"{url}/storage/v1/object/sign/{STORAGE_BUCKET}/{storage_path}"
    try:
        with httpx.Client(timeout=15.0) as client:
            res = client.post(
                endpoint,
                headers={**_storage_headers(key), "Content-Type": "application/json"},
                json={"expiresIn": SIGNED_URL_TTL_SECONDS},
            )
        if res.status_code not in (200, 201):
            logger.warning("[InvoicePDF] Signed URL failed (%s)", res.status_code)
            return None
        signed = (res.json() or {}).get("signedURL") or (res.json() or {}).get("signedUrl")
        if not signed:
            return None
        if str(signed).startswith("http"):
            return str(signed)
        return f"{url}/storage/v1{signed}" if str(signed).startswith("/") else f"{url}/storage/v1/{signed}"
    except Exception as err:
        logger.warning("[InvoicePDF] Signed URL exception: %s", type(err).__name__)
        return None


def invoice_pdf_data_from_records(invoice, booking, transaction) -> Dict[str, Any]:
    meta = _booking_meta(booking) if booking else {
        "airport": "",
        "journey": "",
        "flight_type": "",
        "service_name": "",
        "travel_date": "",
        "pax": "1",
        "terminal": "",
    }
    payment_id, order_id = _razorpay_ids(transaction) if transaction else ("", "")
    issued = getattr(invoice, "issued_at", None) or getattr(invoice, "paid_at", None) or datetime.now(timezone.utc)
    paid_at = getattr(invoice, "paid_at", None) or getattr(transaction, "updated_at", None) or issued
    return {
        "invoice_number": _safe_text(getattr(invoice, "invoice_number", None)),
        "invoice_date": _fmt_dt(issued),
        "booking_ref": _safe_text(getattr(booking, "booking_ref", None) if booking else getattr(transaction, "entity_id", None)),
        "customer_name": _safe_text(getattr(invoice, "customer_name", None) or (getattr(booking, "passenger_name", None) if booking else None), "Valued Guest"),
        "customer_email": _safe_text(getattr(invoice, "customer_email", None) or (getattr(booking, "passenger_email", None) if booking else None)),
        "customer_phone": _safe_text(getattr(booking, "passenger_phone", None) if booking else None),
        "origin": _safe_text(getattr(booking, "origin_code", None) if booking else None),
        "destination": _safe_text(getattr(booking, "dest_code", None) if booking else None),
        "airport": meta["airport"],
        "journey_type": meta["journey"],
        "flight_type": meta["flight_type"],
        "flight_num": _safe_text(getattr(booking, "flight_num", None) if booking else None),
        "travel_date": meta["travel_date"],
        "pax": meta["pax"],
        "terminal": meta["terminal"],
        "service_name": meta["service_name"] or _safe_text(getattr(booking, "service_type", None) if booking else None),
        "service_type": _safe_text(getattr(booking, "service_type", None) if booking else None),
        "subtotal_amount": getattr(invoice, "subtotal_amount", 0) or 0,
        "tax_amount": getattr(invoice, "tax_amount", 0) or 0,
        "total_amount": getattr(invoice, "total_amount", 0) or 0,
        "currency": _safe_text(getattr(invoice, "currency", None), "INR"),
        "razorpay_payment_id": payment_id,
        "razorpay_order_id": order_id,
        "payment_date": _fmt_dt(paid_at),
        "payment_status": "PAID",
        "quantity": meta.get("pax") or "1",
        "amount_paid": getattr(invoice, "total_amount", 0) or 0,
    }


def resolve_whatsapp_invoice_recipient(db: Session, booking, transaction) -> Optional[str]:
    """
    Return the WhatsApp Automation session phone if this booking originated there.
    Never infers origin from passenger phone alone.
    """
    booking_ref = _safe_text(getattr(booking, "booking_ref", None) if booking else None)
    booking_id = str(getattr(booking, "id", "") or "") if booking else ""

    try:
        from app.models.whatsapp_models import WhatsAppConversation
        conv = None
        if booking_ref:
            conv = db.scalar(select(WhatsAppConversation).where(WhatsAppConversation.booking_ref == booking_ref))
        if not conv and booking_id:
            try:
                conv = db.scalar(
                    select(WhatsAppConversation).where(WhatsAppConversation.booking_id == __import__("uuid").UUID(booking_id))
                )
            except Exception:
                conv = None
        if conv:
            return _safe_text(conv.customer_phone or conv.phone_number) or None
    except Exception:
        logger.warning("[InvoicePDF] WhatsApp origin lookup failed")

    meta = getattr(booking, "metadata_json", None) if booking else None
    if isinstance(meta, dict):
        channel = str(meta.get("channel") or meta.get("source") or meta.get("origin_channel") or "").strip().lower()
        if channel in ("whatsapp", "whatsapp_automation"):
            return _safe_text(getattr(booking, "passenger_phone", None) if booking else None) or None

    notes_channel = ""
    resp = getattr(transaction, "gateway_response", None) if transaction else None
    if isinstance(resp, dict):
        payload = resp.get("payload") if isinstance(resp.get("payload"), dict) else {}
        pay_ent = (payload.get("payment") or {}).get("entity") if isinstance(payload.get("payment"), dict) else {}
        if isinstance(pay_ent, dict):
            notes = pay_ent.get("notes") if isinstance(pay_ent.get("notes"), dict) else {}
            notes_channel = str(notes.get("channel") or "").strip().lower()
        if not notes_channel:
            notes_channel = str((resp.get("notes") or {}).get("channel") if isinstance(resp.get("notes"), dict) else "").strip().lower()
    if notes_channel in ("whatsapp", "whatsapp_automation"):
        return _safe_text(getattr(booking, "passenger_phone", None) if booking else None) or None
    return None


def generate_invoice_pdf_bytes(invoice, booking, transaction) -> bytes:
    return generate_tax_invoice_pdf(invoice_pdf_data_from_records(invoice, booking, transaction))


def _notification_context(booking, invoice, signed_url: Optional[str], attached: bool) -> Dict[str, Any]:
    meta = _booking_meta(booking) if booking else {}
    ctx: Dict[str, Any] = {
        "booking_ref": getattr(booking, "booking_ref", None) if booking else None,
        "passenger_name": getattr(booking, "passenger_name", None) if booking else getattr(invoice, "customer_name", None),
        "passenger_email": getattr(booking, "passenger_email", None) if booking else getattr(invoice, "customer_email", None),
        "passenger_phone": getattr(booking, "passenger_phone", None) if booking else None,
        "flight_num": getattr(booking, "flight_num", None) if booking else None,
        "origin_code": getattr(booking, "origin_code", None) if booking else None,
        "dest_code": getattr(booking, "dest_code", None) if booking else None,
        "airport_code": meta.get("airport"),
        "journey_type": meta.get("journey") or (getattr(booking, "service_type", None) if booking else None),
        "service_type": getattr(booking, "service_type", None) if booking else None,
        "service_name": meta.get("service_name") or (getattr(booking, "service_type", None) if booking else None),
        "departure_time": booking.departure_time.isoformat() if booking and booking.departure_time else None,
        "terminal": meta.get("terminal"),
        "total_amount": float(booking.total_amount) if booking and booking.total_amount is not None else float(getattr(invoice, "total_amount", 0) or 0),
        "currency": (booking.currency if booking else None) or getattr(invoice, "currency", None) or "INR",
        "invoice_number": getattr(invoice, "invoice_number", None),
        "invoice_url": signed_url or "",
        "invoice_attached": attached,
    }
    return ctx


def fulfill_paid_invoice(
    db: Session,
    invoice_id: str,
    *,
    send_notifications: bool = True,
) -> Dict[str, Any]:
    """
    Generate PDF, upload to storage, set Invoice.pdf_url, then optionally send confirmation.
    Failures are logged and never reverse payment/booking/invoice status.

    send_notifications=False is for safe storage backfills (no email/WhatsApp spam).
    """
    try:
        return _fulfill_paid_invoice_inner(db, invoice_id, send_notifications=send_notifications)
    except Exception:
        logger.exception("[InvoicePDF] Fulfillment crashed; payment is left unchanged")
        try:
            db.rollback()
        except Exception:
            pass
        return {"success": False, "error": "fulfillment_failed"}


def _fulfill_paid_invoice_inner(
    db: Session,
    invoice_id: str,
    *,
    send_notifications: bool = True,
) -> Dict[str, Any]:
    from app.models.payment import Invoice
    from app.models.schema import Booking
    from app.services.notification_service import NotificationService

    try:
        inv_uuid = __import__("uuid").UUID(str(invoice_id))
    except Exception:
        logger.error("[InvoicePDF] Invalid invoice id")
        return {"success": False, "error": "invalid_invoice_id"}

    invoice = db.scalar(select(Invoice).where(Invoice.id == inv_uuid))
    if not invoice:
        logger.error("[InvoicePDF] Invoice %s not found", invoice_id)
        return {"success": False, "error": "invoice_not_found"}

    transaction = invoice.transaction
    booking = None
    entity_id = getattr(transaction, "entity_id", None) if transaction else None
    if entity_id:
        booking = db.scalar(select(Booking).where(Booking.booking_ref == str(entity_id)))
        if not booking:
            try:
                booking = db.scalar(select(Booking).where(Booking.id == __import__("uuid").UUID(str(entity_id))))
            except Exception:
                booking = None

    pdf_bytes: Optional[bytes] = None
    attached = False
    signed_url: Optional[str] = None
    storage_path = invoice.pdf_url if invoice.pdf_url and not str(invoice.pdf_url).startswith("http") else None

    if invoice.pdf_url:
        storage_path = storage_path or invoice.pdf_url
        try:
            pdf_bytes = generate_invoice_pdf_bytes(invoice, booking, transaction)
        except Exception:
            logger.exception("[InvoicePDF] Regeneration of existing invoice failed; continuing with stored path")
    else:
        try:
            pdf_bytes = generate_invoice_pdf_bytes(invoice, booking, transaction)
        except Exception:
            logger.exception("[InvoicePDF] PDF generation failed for %s", invoice.invoice_number)
            pdf_bytes = None

        if pdf_bytes:
            booking_ref = getattr(booking, "booking_ref", None) or entity_id or "unknown"
            storage_path = build_invoice_storage_path(str(booking_ref), invoice.invoice_number)
            uploaded = upload_invoice_pdf(storage_path, pdf_bytes)
            if uploaded:
                invoice.pdf_url = storage_path
                db.commit()
                db.refresh(invoice)
            else:
                logger.error("[InvoicePDF] Storage upload failed for %s; invoice row kept without pdf_url", invoice.invoice_number)
                storage_path = None

    if invoice.pdf_url:
        signed_url = create_signed_invoice_url(invoice.pdf_url)
    attached = bool(pdf_bytes)

    if send_notifications:
        try:
            ctx = _notification_context(booking, invoice, signed_url, attached)
            NotificationService.notify_booking_confirmed(
                db,
                ctx,
                attachments=[{"filename": f"{invoice.invoice_number}.pdf", "content": pdf_bytes}] if attached and pdf_bytes else None,
            )
        except Exception:
            logger.exception("[InvoicePDF] Confirmation notification failed for invoice %s", invoice.invoice_number)

        wa_phone = resolve_whatsapp_invoice_recipient(db, booking, transaction)
        if wa_phone and (pdf_bytes or (signed_url and str(signed_url).startswith("https://"))):
            try:
                NotificationService.send_whatsapp_invoice_document(
                    db,
                    booking_ref=getattr(booking, "booking_ref", None) or "",
                    recipient_phone=wa_phone,
                    invoice_number=invoice.invoice_number,
                    pdf_bytes=pdf_bytes,
                    document_url=signed_url if signed_url and str(signed_url).startswith("https://") else None,
                )
            except Exception:
                logger.exception("[InvoicePDF] WhatsApp invoice document failed for %s", invoice.invoice_number)
        elif not wa_phone:
            logger.info(
                "[InvoicePDF] Skipping WhatsApp invoice PDF (not a WhatsApp-originated booking)",
                extra={"invoice_number": invoice.invoice_number},
            )

    return {
        "success": True,
        "invoice_number": invoice.invoice_number,
        "pdf_url": invoice.pdf_url,
        "attached": attached,
        "signed_url": bool(signed_url),
        "notifications_sent": bool(send_notifications),
    }


def _run_fulfillment(invoice_id: str) -> None:
    from app.database import SessionLocal

    db = SessionLocal()
    try:
        fulfill_paid_invoice(db, invoice_id)
    except Exception:
        logger.exception("[InvoicePDF] Background fulfillment failed for %s", invoice_id)
    finally:
        db.close()


def schedule_invoice_fulfillment(invoice_id: str) -> None:
    """Do not block the webhook: run PDF/storage/email after payment is committed."""
    if not invoice_id:
        return
    if os.getenv("PYTEST_CURRENT_TEST"):
        _run_fulfillment(str(invoice_id))
        return
    thread = threading.Thread(target=_run_fulfillment, args=(str(invoice_id),), daemon=True, name=f"invoice-pdf-{invoice_id}")
    thread.start()
