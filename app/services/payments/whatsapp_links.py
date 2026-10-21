"""
WhatsApp Payment Links Service.
Handles creating, replacing, recovering, and reconciling Razorpay Payment Links
for WhatsApp bookings.
"""

import uuid
import logging
from typing import Dict, Any, Optional
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from sqlalchemy import select, desc

from app.models.payment import (
    PaymentTransaction, PaymentStatus, PaymentMethod, PaymentStateMachine
)
from app.services.timeline_service import TimelineService
from app.services.payments.initiation import (
    payment_link_expire_hours,
    is_https_payment_url,
    is_simulated_payment_link,
    unknown_create_result,
    link_from_gateway_item,
    merge_gateway_response,
)

logger = logging.getLogger(__name__)


def mark_payment_link_terminal(tx: PaymentTransaction, status: PaymentStatus, link_status: str) -> None:
    if tx.status == PaymentStatus.SUCCESSFUL:
        return
    if not PaymentStateMachine.can_transition(tx.status, status):
        return
    tx.status = status
    resp = dict(tx.gateway_response) if isinstance(tx.gateway_response, dict) else {}
    resp["link_status"] = link_status
    tx.gateway_response = resp
    tx.updated_at = datetime.now(timezone.utc)


def recover_gateway_link_by_reference(booking_ref: str) -> Optional[Dict[str, Any]]:
    from app.providers.razorpay_provider import razorpay_provider

    listed = razorpay_provider.list_payment_links_by_reference(booking_ref)
    items = listed.get("items") if listed.get("success") else []
    for item in items or []:
        recovered = link_from_gateway_item(item)
        if recovered:
            return recovered
    return None


def find_reusable_whatsapp_payment_link(db: Session, booking_ref: str) -> Optional[Dict[str, Any]]:
    now_ts = int(datetime.now(timezone.utc).timestamp())
    txs = list(
        db.scalars(
            select(PaymentTransaction)
            .where(
                PaymentTransaction.entity_id == booking_ref,
                PaymentTransaction.gateway_provider == "RAZORPAY",
                PaymentTransaction.status.in_([PaymentStatus.PENDING, PaymentStatus.PROCESSING]),
                PaymentTransaction.is_duplicate.isnot(True),
            )
            .order_by(desc(PaymentTransaction.created_at))
        ).all()
    )
    for tx in txs:
        resp = tx.gateway_response if isinstance(tx.gateway_response, dict) else {}
        plink_id = resp.get("payment_link_id")
        if not plink_id and (tx.gateway_payment_id or "").startswith("plink_"):
            plink_id = tx.gateway_payment_id
        url = resp.get("short_url")
        link_status = str(resp.get("link_status") or "created").lower()
        expire_by = resp.get("expire_by")
        if link_status in ("expired", "cancelled", "paid"):
            continue
        expired_locally = False
        if expire_by:
            try:
                expired_locally = int(expire_by) <= now_ts
            except (TypeError, ValueError):
                expired_locally = False
        if expired_locally:
            mark_payment_link_terminal(tx, PaymentStatus.EXPIRED, "expired")
            continue
        if is_simulated_payment_link(plink_id, url) or not is_https_payment_url(url):
            continue
        return {
            "success": True,
            "reused": True,
            "payment_link_id": plink_id,
            "short_url": url,
            "transaction_ref": tx.transaction_ref,
            "expire_by": expire_by,
        }
    return None


def persist_whatsapp_payment_link_tx(
    cls,
    db: Session,
    *,
    booking,
    link: Dict[str, Any],
    previous_link_id: Optional[str],
) -> PaymentTransaction:
    plink_id = link["payment_link_id"]
    existing = db.scalar(
        select(PaymentTransaction)
        .where(
            PaymentTransaction.entity_id == booking.booking_ref,
            PaymentTransaction.gateway_payment_id == plink_id,
            PaymentTransaction.is_duplicate.isnot(True),
        )
        .order_by(desc(PaymentTransaction.created_at))
    )
    if existing and existing.status in (PaymentStatus.PENDING, PaymentStatus.PROCESSING):
        existing.gateway_response = merge_gateway_response(
            existing.gateway_response,
            extra={
                "payment_link_id": plink_id,
                "short_url": link.get("short_url"),
                "order_id": link.get("order_id"),
                "expire_by": link.get("expire_by"),
                "link_status": link.get("status") or "created",
                "channel": "whatsapp",
                "booking_ref": booking.booking_ref,
                "notes": link.get("notes") or {"booking_ref": booking.booking_ref, "channel": "whatsapp"},
            },
        )
        existing.updated_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(existing)
        return existing

    latest = db.scalar(
        select(PaymentTransaction)
        .where(
            PaymentTransaction.entity_id == booking.booking_ref,
            PaymentTransaction.gateway_provider == "RAZORPAY",
            PaymentTransaction.is_duplicate.isnot(True),
        )
        .order_by(desc(PaymentTransaction.created_at))
    )
    prev_id = previous_link_id
    prev_list = []
    if latest:
        prev_resp = latest.gateway_response if isinstance(latest.gateway_response, dict) else {}
        prev_id = prev_id or prev_resp.get("payment_link_id") or (
            latest.gateway_payment_id if (latest.gateway_payment_id or "").startswith("plink_") else None
        )
        prev_list = list(prev_resp.get("previous_payment_links") or [])
        if prev_id and prev_id != plink_id:
            prev_list.append({
                "payment_link_id": prev_id,
                "short_url": prev_resp.get("short_url"),
                "replaced_at": datetime.now(timezone.utc).isoformat(),
            })
        if latest.status in (PaymentStatus.PENDING, PaymentStatus.PROCESSING):
            stale_status = PaymentStatus.EXPIRED
            if str(prev_resp.get("link_status") or "").lower() == "cancelled":
                stale_status = PaymentStatus.CANCELLED
            mark_payment_link_terminal(latest, stale_status, prev_resp.get("link_status") or "expired")

    ref = f"PAY-WA-{uuid.uuid4().hex[:8].upper()}"
    gateway_response = {
        "payment_link_id": plink_id,
        "short_url": link.get("short_url"),
        "order_id": link.get("order_id"),
        "expire_by": link.get("expire_by"),
        "link_status": link.get("status") or "created",
        "channel": "whatsapp",
        "booking_ref": booking.booking_ref,
        "notes": link.get("notes") or {"booking_ref": booking.booking_ref, "channel": "whatsapp"},
        "raw_response": link.get("raw_response"),
        "previous_payment_links": prev_list,
    }
    tx = PaymentTransaction(
        transaction_ref=ref,
        entity_type="AIRPORT_BOOKING",
        entity_id=booking.booking_ref,
        customer_id=str(booking.user_id) if getattr(booking, "user_id", None) else None,
        amount=float(booking.total_amount),
        currency=(booking.currency or "INR").upper(),
        payment_method=PaymentMethod.UPI,
        status=PaymentStatus.PENDING,
        gateway_provider="RAZORPAY",
        gateway_payment_id=plink_id,
        gateway_response=gateway_response,
        notes="whatsapp_payment_link",
    )
    db.add(tx)
    try:
        TimelineService.add_entry(
            db,
            entity_type="AIRPORT_BOOKING",
            entity_id=booking.booking_ref,
            event_type="PAYMENT_LINK_INITIATED",
            title=f"WhatsApp Payment Link Initiated ({ref})",
            details={"payment_link_id": plink_id, "amount": float(booking.total_amount)},
        )
    except Exception:
        pass
    db.commit()
    db.refresh(tx)
    return tx


def initiate_whatsapp_payment_link(cls, db: Session, booking_ref: str) -> Dict[str, Any]:
    """
    Create or reuse a Razorpay Payment Link for a PENDING WhatsApp booking.
    Always persists/reuses a local PaymentTransaction BEFORE the customer is sent a URL.
    Never confirms the booking. Never sends simulated/localhost/http URLs.
    """
    from app.models.schema import Booking, BookingStatus
    from app.providers.razorpay_provider import razorpay_provider

    booking = db.scalar(select(Booking).where(Booking.booking_ref == booking_ref))
    if not booking:
        return {"success": False, "error": "BOOKING_NOT_FOUND", "reason": f"Booking '{booking_ref}' not found."}
    if booking.status == BookingStatus.CONFIRMED:
        return {"success": False, "error": "ALREADY_CONFIRMED", "reason": "Booking is already confirmed."}
    if booking.status in (BookingStatus.CANCELLED, BookingStatus.REJECTED):
        return {"success": False, "error": "BOOKING_NOT_PAYABLE", "reason": "Booking cannot accept payment."}
    if booking.total_amount is None:
        return {"success": False, "error": "NO_AMOUNT", "reason": "Booking has no authoritative amount."}

    from app.services.booking_cutoff import evaluate_cutoff_for_booking
    # WhatsApp 'flight not confirmed yet' bookings carry no scheduled times:
    # the ops team confirms the flight with the customer, so the flight-time
    # cutoff cannot apply. Flagged via metadata (flight_later).
    if not (booking.metadata_json or {}).get("flight_later", False):
        cutoff = evaluate_cutoff_for_booking(db, booking)
        if not cutoff.allowed:
            return {
                "success": False,
                "error": "BOOKING_CUTOFF",
                "reason": cutoff.reason,
                "customer_message": cutoff.customer_message,
            }

    authoritative_amount = float(booking.total_amount)
    currency = (booking.currency or "INR").upper()

    reusable = cls._find_reusable_whatsapp_payment_link(db, booking_ref)
    if reusable:
        return reusable

    expire_hours = cls._payment_link_expire_hours()
    expire_by = int(datetime.now(timezone.utc).timestamp()) + (expire_hours * 3600)

    recovered = cls._recover_gateway_link_by_reference(booking.booking_ref)
    if recovered:
        tx = cls._persist_whatsapp_payment_link_tx(
            db,
            booking=booking,
            link=recovered,
            previous_link_id=None,
        )
        return {
            "success": True,
            "reused": True,
            "recovered": True,
            "payment_link_id": recovered["payment_link_id"],
            "short_url": recovered["short_url"],
            "transaction_ref": tx.transaction_ref,
            "expire_by": recovered.get("expire_by") or expire_by,
        }

    intent = razorpay_provider.create_payment_link(
        amount=authoritative_amount,
        currency=currency,
        reference_id=booking.booking_ref,
        booking_ref=booking.booking_ref,
        description=f"Shafsky Aviation booking {booking.booking_ref}",
        customer_name=booking.passenger_name or "Guest",
        customer_email=booking.passenger_email or "",
        customer_phone=booking.passenger_phone or "",
        expire_by=expire_by,
        channel="whatsapp",
        notes={"booking_ref": booking.booking_ref, "channel": "whatsapp"},
    )

    if (not intent.get("success")) and unknown_create_result(intent):
        recovered = cls._recover_gateway_link_by_reference(booking.booking_ref)
        if recovered:
            intent = recovered
        else:
            return {
                "success": False,
                "error": "LINK_CREATE_UNKNOWN",
                "reason": "Payment link creation timed out. Please try again in a moment.",
            }

    if not intent.get("success") and "reference" in str(intent.get("error") or "").lower():
        recovered = cls._recover_gateway_link_by_reference(booking.booking_ref)
        if recovered:
            intent = recovered

    if not intent.get("success"):
        return {
            "success": False,
            "error": "LINK_CREATE_FAILED",
            "reason": intent.get("error") or "Payment link could not be created.",
        }

    plink_id = intent.get("payment_link_id")
    short_url = intent.get("short_url")
    if is_simulated_payment_link(plink_id, short_url, bool(intent.get("simulated"))):
        from app.core.runtime import offline_third_party_calls
        if not offline_third_party_calls():
            logger.warning("[PaymentService] Simulated Payment Link rejected for customer delivery (booking %s)", booking_ref)
            return {
                "success": False,
                "error": "SIMULATED_LINK_REJECTED",
                "reason": "Payment gateway is not configured for live Payment Links.",
            }
    if not is_https_payment_url(short_url):
        return {
            "success": False,
            "error": "UNDELIVERABLE_URL",
            "reason": "Payment link URL is not a secure HTTPS Razorpay URL.",
        }

    tx = cls._persist_whatsapp_payment_link_tx(
        db,
        booking=booking,
        link={
            "payment_link_id": plink_id,
            "short_url": short_url,
            "order_id": intent.get("order_id"),
            "expire_by": intent.get("expire_by") or expire_by,
            "status": intent.get("status") or "created",
            "notes": intent.get("notes") or {"booking_ref": booking.booking_ref, "channel": "whatsapp"},
            "raw_response": intent.get("raw_response") or intent,
            "simulated": False,
        },
        previous_link_id=None,
    )
    return {
        "success": True,
        "reused": False,
        "payment_link_id": plink_id,
        "short_url": short_url,
        "transaction_ref": tx.transaction_ref,
        "expire_by": intent.get("expire_by") or expire_by,
    }


def replace_whatsapp_payment_link(cls, db: Session, booking_ref: str) -> Dict[str, Any]:
    """Force a new Payment Link for a still-PENDING booking after expiry/cancellation."""
    from app.models.schema import Booking, BookingStatus

    booking = db.scalar(select(Booking).where(Booking.booking_ref == booking_ref))
    if not booking:
        return {"success": False, "error": "BOOKING_NOT_FOUND"}
    if booking.status != BookingStatus.PENDING:
        return {"success": False, "error": "BOOKING_NOT_PENDING"}

    latest = db.scalar(
        select(PaymentTransaction)
        .where(
            PaymentTransaction.entity_id == booking_ref,
            PaymentTransaction.gateway_provider == "RAZORPAY",
            PaymentTransaction.is_duplicate.isnot(True),
        )
        .order_by(desc(PaymentTransaction.created_at))
    )
    if latest and latest.status in (PaymentStatus.PENDING, PaymentStatus.PROCESSING):
        mark_payment_link_terminal(latest, PaymentStatus.EXPIRED, "expired")
        db.flush()
    return cls.initiate_whatsapp_payment_link(db, booking_ref)


def reconcile_whatsapp_payment_link(cls, db: Session, booking_ref: str) -> Dict[str, Any]:
    """
    Fetch live Payment Link status. Confirms only if Razorpay reports paid,
    via handle_verified_payment – never from customer text.
    """
    from app.models.schema import Booking, BookingStatus
    from app.providers.razorpay_provider import razorpay_provider

    booking = db.scalar(select(Booking).where(Booking.booking_ref == booking_ref))
    if not booking:
        return {"success": False, "status": "NOT_FOUND", "paid": False}
    if booking.status == BookingStatus.CONFIRMED:
        return {"success": True, "status": "ALREADY_CONFIRMED", "paid": True}

    tx = db.scalar(
        select(PaymentTransaction)
        .where(
            PaymentTransaction.entity_id == booking_ref,
            PaymentTransaction.gateway_provider == "RAZORPAY",
            PaymentTransaction.is_duplicate.isnot(True),
        )
        .order_by(desc(PaymentTransaction.created_at))
    )
    resp = tx.gateway_response if tx and isinstance(tx.gateway_response, dict) else {}
    plink_id = (resp.get("payment_link_id") if resp else None) or (
        tx.gateway_payment_id if tx and (tx.gateway_payment_id or "").startswith("plink_") else None
    )
    if not plink_id:
        return {"success": True, "status": "NO_LINK", "paid": False}

    fetched = razorpay_provider.fetch_payment_link(plink_id)
    if not fetched.get("success"):
        return {
            "success": True,
            "status": "STATUS_UNAVAILABLE",
            "paid": False,
            "reason": fetched.get("error"),
        }

    remote_status = str(fetched.get("status") or "").lower()
    if remote_status in ("expired", "cancelled"):
        if tx:
            target = PaymentStatus.EXPIRED if remote_status == "expired" else PaymentStatus.CANCELLED
            mark_payment_link_terminal(tx, target, remote_status)
            db.commit()
        return {"success": True, "status": remote_status.upper(), "paid": False}

    if remote_status != "paid":
        return {"success": True, "status": remote_status.upper() or "CREATED", "paid": False}

    amount = fetched.get("amount_paid")
    if amount is None:
        amount = fetched.get("amount")
    currency = fetched.get("currency") or (booking.currency if booking else "INR")
    data = fetched.get("data") if isinstance(fetched.get("data"), dict) else {}
    raw_payload = {
        "event": "payment_link.paid",
        "payload": {
            "payment_link": {
                "entity": data or {
                    "id": plink_id,
                    "amount": amount,
                    "amount_paid": amount,
                    "currency": currency,
                    "status": "paid",
                    "order_id": fetched.get("order_id"),
                    "reference_id": booking_ref,
                    "notes": {"booking_ref": booking_ref, "channel": "whatsapp"},
                }
            }
        },
    }
    result = cls.handle_verified_payment(
        db,
        event_name="payment_link.paid",
        gateway_provider="RAZORPAY",
        order_id=fetched.get("order_id") or (data.get("order_id") if data else None),
        payment_id=None,
        booking_ref=booking_ref,
        raw_payload=raw_payload,
        channel="whatsapp",
        amount=amount,
        currency=currency,
    )
    return {
        "success": bool(result.get("success")),
        "status": result.get("status"),
        "paid": result.get("status") in ("CONFIRMED",) or bool(result.get("already_confirmed")),
        "details": result,
    }
