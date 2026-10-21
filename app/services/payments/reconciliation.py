"""
Payment Reconciliation and Ledger Service.
Handles anomaly detection, automated sync against Razorpay/ICICI,
stale transaction expiration, and enterprise payment ledger reporting.
"""

import logging
from typing import Dict, Any, Optional, List
from datetime import datetime, timezone, timedelta
from sqlalchemy.orm import Session
from sqlalchemy import select, desc, or_

from app.models.payment import PaymentTransaction, PaymentStatus

logger = logging.getLogger(__name__)


def get_reconciliation_report(cls, db: Session) -> Dict[str, Any]:
    """
    Scans all payment transactions and bookings to identify discrepancies,
    anomalies, duplicate payments, amount mismatches, and orphan records.
    """
    from app.models.schema import Booking, BookingStatus

    transactions = list(db.scalars(select(PaymentTransaction).order_by(desc(PaymentTransaction.created_at))).all())
    bookings = {b.booking_ref: b for b in db.scalars(select(Booking)).all()}

    anomalies = []
    duplicate_payments = []
    paid_count = 0
    refunded_count = 0
    total_settled = 0.0
    total_refunded = 0.0

    tx_by_booking: Dict[str, List[PaymentTransaction]] = {}
    for tx in transactions:
        b_ref = tx.entity_id
        tx_by_booking.setdefault(b_ref, []).append(tx)

        if tx.status == PaymentStatus.SUCCESSFUL:
            paid_count += 1
            total_settled += tx.amount
        elif tx.status in (PaymentStatus.REFUNDED, PaymentStatus.PARTIALLY_REFUNDED):
            refunded_count += 1
            for r in tx.refunds:
                total_refunded += r.amount

    now = datetime.now(timezone.utc)
    for b_ref, tx_list in tx_by_booking.items():
        booking = bookings.get(b_ref)
        successful_txs = [t for t in tx_list if t.status == PaymentStatus.SUCCESSFUL]

        # 1. Multiple Successful Payments for 1 Booking
        if len(successful_txs) > 1:
            duplicate_payments.append({
                "booking_ref": b_ref,
                "count": len(successful_txs),
                "payments": [
                    {"tx_ref": t.transaction_ref, "gateway_payment_id": t.gateway_payment_id, "amount": t.amount}
                    for t in successful_txs
                ]
            })
            anomalies.append({
                "type": "DUPLICATE_PAYMENTS",
                "severity": "CRITICAL",
                "booking_ref": b_ref,
                "message": f"Booking '{b_ref}' received {len(successful_txs)} successful payments."
            })

        # 2. Paid Transaction but Booking is PENDING
        if successful_txs and booking and booking.status == BookingStatus.PENDING:
            anomalies.append({
                "type": "PAID_WITHOUT_CONFIRMATION",
                "severity": "HIGH",
                "booking_ref": b_ref,
                "message": f"Payment is SUCCESSFUL ({successful_txs[0].transaction_ref}) but booking status is PENDING."
            })

        # 3. Amount Mismatch
        if successful_txs and booking:
            for stx in successful_txs:
                if abs(float(stx.amount) - float(booking.total_amount)) > 0.01:
                    anomalies.append({
                        "type": "AMOUNT_MISMATCH",
                        "severity": "HIGH",
                        "booking_ref": b_ref,
                        "message": f"Tx amount (\u20b9{stx.amount}) != Booking amount (\u20b9{booking.total_amount})."
                    })

        # 4. Currency Mismatch
        if successful_txs and booking:
            for stx in successful_txs:
                if (stx.currency or "INR").upper() != (booking.currency or "INR").upper():
                    anomalies.append({
                        "type": "CURRENCY_MISMATCH",
                        "severity": "MEDIUM",
                        "booking_ref": b_ref,
                        "message": f"Tx currency ({stx.currency}) != Booking currency ({booking.currency})."
                    })

        # 5. Stale Pending Transactions (> 24 hours)
        for ptx in tx_list:
            if ptx.status == PaymentStatus.PENDING:
                tx_age = (now - ptx.created_at).total_seconds() / 3600.0 if ptx.created_at else 0
                if tx_age > 24.0:
                    anomalies.append({
                        "type": "STALE_PENDING",
                        "severity": "LOW",
                        "booking_ref": b_ref,
                        "message": f"Transaction '{ptx.transaction_ref}' has been PENDING for {round(tx_age, 1)} hours."
                    })

    # 6. Booking Confirmed without any Successful Payment
    for b_ref, booking in bookings.items():
        if booking.status == BookingStatus.CONFIRMED:
            matching_txs = tx_by_booking.get(b_ref, [])
            has_success = any(t.status == PaymentStatus.SUCCESSFUL for t in matching_txs)
            if not has_success:
                anomalies.append({
                    "type": "CONFIRMED_WITHOUT_PAYMENT",
                    "severity": "CRITICAL",
                    "booking_ref": b_ref,
                    "message": f"Booking '{b_ref}' is marked CONFIRMED without any verified SUCCESSFUL payment transaction."
                })

    return {
        "summary": {
            "total_transactions": len(transactions),
            "total_settled_inr": round(total_settled, 2),
            "total_refunded_inr": round(total_refunded, 2),
            "paid_transactions": paid_count,
            "refunded_transactions": refunded_count,
            "total_anomalies": len(anomalies),
            "critical_anomalies": len([a for a in anomalies if a["severity"] == "CRITICAL"]),
        },
        "anomalies": anomalies,
        "duplicate_payments": duplicate_payments
    }


def reconcile_booking_payment(cls, db: Session, booking_ref: str) -> Dict[str, Any]:
    """
    Queries Razorpay or ICICI directly for live order/payment status and synchronizes the local database state.
    """
    from app.models.schema import Booking
    from app.providers.razorpay_provider import razorpay_provider
    from app.providers.icici_provider import icici_provider

    booking = db.scalar(select(Booking).where(Booking.booking_ref == booking_ref))
    if not booking:
        raise ValueError(f"Booking '{booking_ref}' not found.")

    tx = db.scalar(
        select(PaymentTransaction)
        .where(PaymentTransaction.entity_id == booking_ref)
        .order_by(desc(PaymentTransaction.created_at))
    )

    gateway_order_id = tx.gateway_payment_id if tx else None
    if not gateway_order_id:
        raise ValueError(f"No gateway order ID found for booking '{booking_ref}'.")

    provider = (tx.gateway_provider if tx else None) or (
        booking.metadata_json.get("payment_gateway") if booking.metadata_json else None
    )
    if not provider:
        if gateway_order_id.startswith(("order_", "plink_")):
            provider = "RAZORPAY"
        else:
            provider = "ICICI"
    provider = str(provider).strip().upper()

    # BRANCH 1: ICICI BANK
    if provider == "ICICI":
        status_res = icici_provider.check_transaction_status(merchant_txn_no=gateway_order_id)
        if not status_res.get("success"):
            raise ValueError(f"Failed to fetch transaction status from ICICI: {status_res.get('error')}")

        if status_res.get("paid"):
            txn_id = str(status_res.get("txnID") or gateway_order_id)
            amount = float(status_res.get("amount") or (tx.amount if tx else booking.total_amount))

            if tx and tx.status in (PaymentStatus.FAILED, PaymentStatus.EXPIRED, PaymentStatus.CANCELLED):
                tx.status = PaymentStatus.PENDING
                db.flush()

            result = cls.handle_verified_payment(
                db,
                event_name="ICICI_STATUS_SUC",
                gateway_provider="ICICI",
                order_id=gateway_order_id,
                payment_id=txn_id,
                booking_ref=booking_ref,
                raw_payload={
                    "icici_status": status_res.get("data"),
                    "reconciled": True,
                },
                channel="web_reconciliation",
                amount=amount,
                currency="INR",
            )
            if not result.get("success"):
                return {
                    "reconciled": False,
                    "gateway": "ICICI",
                    "status": result.get("status", "RECONCILIATION_FAILED"),
                    "details": result,
                }
            return {
                "reconciled": True,
                "gateway": "ICICI",
                "status": result.get("status", "CONFIRMED"),
                "details": result,
            }

        return {
            "reconciled": True,
            "gateway": "ICICI",
            "status": booking.status.value if hasattr(booking.status, "value") else str(booking.status),
            "gateway_status": status_res.get("txnStatus") or "UNKNOWN",
            "message": f"ICICI transaction status is '{status_res.get('txnStatus')}' ({status_res.get('txnRespDescription') or ''})."
        }

    # BRANCH 2: RAZORPAY
    elif provider == "RAZORPAY":
        if tx and tx.status in (PaymentStatus.FAILED, PaymentStatus.EXPIRED, PaymentStatus.CANCELLED):
            tx.status = PaymentStatus.PENDING
            db.flush()

        if gateway_order_id.startswith("plink_"):
            link_res = cls.reconcile_whatsapp_payment_link(db, booking_ref)
            is_confirmed = link_res.get("success") and link_res.get("status") in {"CONFIRMED", "ALREADY_CONFIRMED", "PAID"}
            return {
                "reconciled": is_confirmed,
                "gateway": "RAZORPAY",
                "status": "CONFIRMED" if is_confirmed else link_res.get("status", "PENDING"),
                "details": link_res,
                "message": f"Razorpay Payment Link status is '{link_res.get('status')}'."
            }

        order_res = razorpay_provider.fetch_order(gateway_order_id)
        if not order_res.get("success"):
            raise ValueError(f"Failed to fetch order from Razorpay: {order_res.get('error')}")

        order_data = order_res.get("order") or order_res.get("data") or {}
        order_status = order_data.get("status") or order_res.get("status")
        if order_status == "paid":
            payments_res = razorpay_provider.fetch_order_payments(gateway_order_id)
            if not payments_res.get("success"):
                raise ValueError(
                    f"Failed to fetch captured payment from Razorpay: {payments_res.get('error')}"
                )

            captured_payment = next(
                (
                    payment
                    for payment in payments_res.get("items", [])
                    if str(payment.get("status") or "").lower() == "captured"
                ),
                None,
            )
            if not captured_payment:
                raise ValueError(
                    f"Razorpay order '{gateway_order_id}' is paid but has no captured payment."
                )

            result = cls.handle_verified_payment(
                db,
                event_name="payment.captured",
                gateway_provider="RAZORPAY",
                order_id=gateway_order_id,
                payment_id=captured_payment.get("id"),
                booking_ref=booking_ref,
                channel="web_reconciliation",
                amount=captured_payment.get("amount"),
                currency=captured_payment.get("currency"),
            )
            if not result.get("success"):
                return {
                    "reconciled": False,
                    "gateway": "RAZORPAY",
                    "status": result.get("status", "RECONCILIATION_FAILED"),
                    "details": result,
                }
            return {
                "reconciled": True,
                "gateway": "RAZORPAY",
                "status": result.get("status", "CONFIRMED"),
                "details": result,
            }

        return {
            "reconciled": True,
            "gateway": "RAZORPAY",
            "status": booking.status.value if hasattr(booking.status, "value") else str(booking.status),
            "gateway_status": order_status,
            "message": f"Razorpay order status is '{order_status}'."
        }

    else:
        raise ValueError(f"Unsupported payment gateway provider '{provider}' for booking '{booking_ref}'.")


def expire_stale_transactions(cls, db: Session, max_age_hours: float = 24.0) -> int:
    """
    Finds all PENDING payment transactions created more than max_age_hours ago
    and transitions them to EXPIRED. Also marks stale WhatsApp WAITING_PAYMENT
    conversations so abandoned links do not linger forever.
    """
    from app.providers.razorpay_provider import razorpay_provider

    threshold = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
    stale_txs = list(
        db.scalars(
            select(PaymentTransaction).where(
                PaymentTransaction.status == PaymentStatus.PENDING,
                PaymentTransaction.created_at <= threshold
            )
        ).all()
    )

    count = 0
    for tx in stale_txs:
        gateway_id = tx.gateway_payment_id or ""
        if (
            tx.gateway_provider == "RAZORPAY"
            and gateway_id
            and razorpay_provider.is_configured()
        ):
            if gateway_id.startswith("order_"):
                order_result = razorpay_provider.fetch_order(gateway_id)
                if not order_result.get("success"):
                    logger.warning(
                        "Skipping expiry for %s: gateway order could not be verified.",
                        tx.transaction_ref,
                    )
                    continue
                order_data = order_result.get("order") or order_result.get("data") or {}
                if str(order_data.get("status") or "").lower() == "paid":
                    continue
                payments_result = razorpay_provider.fetch_order_payments(gateway_id)
                if not payments_result.get("success"):
                    continue
                if any(
                    str(item.get("status") or "").lower() in {"authorized", "captured"}
                    for item in payments_result.get("items", [])
                ):
                    continue
            elif gateway_id.startswith("plink_"):
                link_result = razorpay_provider.fetch_payment_link(gateway_id)
                if not link_result.get("success"):
                    continue
                link_data = link_result.get("data") or link_result
                if str(link_data.get("status") or "").lower() == "paid":
                    continue

        tx.status = PaymentStatus.EXPIRED
        tx.updated_at = datetime.now(timezone.utc)
        count += 1

    wa_expired = 0
    try:
        from app.models.whatsapp_models import WhatsAppConversation
        stale_convs = list(
            db.scalars(
                select(WhatsAppConversation).where(
                    WhatsAppConversation.current_state.in_(["WAITING_PAYMENT", "PENDING_PAYMENT"]),
                    WhatsAppConversation.updated_at <= threshold,
                )
            ).all()
        )
        for conv in stale_convs:
            conv.payment_status = "EXPIRED"
            conv.current_state = "CATEGORY_SELECTION"
            conv.updated_at = datetime.now(timezone.utc)
            wa_expired += 1
    except Exception:
        logger.exception("[PaymentService] Stale WhatsApp WAITING_PAYMENT cleanup failed")

    if count > 0 or wa_expired > 0:
        db.commit()
        logger.info(
            "[PaymentService] Expired %s stale PENDING payment transactions and %s WhatsApp payment waits older than %sh.",
            count,
            wa_expired,
            max_age_hours,
        )

    return count


def get_payment_ledger(
    cls,
    db: Session,
    search: Optional[str] = None,
    provider: Optional[str] = None,
    status: Optional[str] = None
) -> List[Dict[str, Any]]:
    """Returns structured enterprise payment ledger entries from PaymentTransaction."""
    from app.models.schema import Booking

    stmt = select(PaymentTransaction).order_by(desc(PaymentTransaction.created_at))
    if status and status != "all":
        try:
            p_status = PaymentStatus(status.upper())
            stmt = stmt.where(PaymentTransaction.status == p_status)
        except ValueError:
            pass

    if provider and provider != "all":
        stmt = stmt.where(PaymentTransaction.gateway_provider.ilike(f"%{provider}%"))

    transactions = list(db.scalars(stmt.limit(500)).all())
    booking_refs = {t.entity_id for t in transactions if t.entity_id}
    bookings_map = {
        b.booking_ref: b for b in db.scalars(select(Booking).where(Booking.booking_ref.in_(booking_refs))).all()
    }

    results = []
    for t in transactions:
        b = bookings_map.get(t.entity_id)
        inv = t.invoices[0] if t.invoices else None
        ref_rec = t.refunds[0] if t.refunds else None

        contact_name = b.passenger_name if b else "N/A"
        contact_email = b.passenger_email if b else "N/A"

        if search:
            s_lower = search.lower()
            matches = (
                s_lower in t.transaction_ref.lower() or
                s_lower in (t.gateway_payment_id or "").lower() or
                s_lower in (t.entity_id or "").lower() or
                s_lower in contact_name.lower() or
                s_lower in contact_email.lower()
            )
            if not matches:
                continue

        results.append({
            "id": str(t.id),
            "payment_id": t.transaction_ref,
            "booking_id": str(b.id) if b else (t.entity_id or "N/A"),
            "booking_ref": b.booking_ref if b else (t.entity_id or "N/A"),
            "contact_name": contact_name,
            "contact_email": contact_email,
            "provider": t.gateway_provider.lower(),
            "provider_payment_id": t.gateway_payment_id or "N/A",
            "amount": float(t.amount),
            "currency": t.currency,
            "status": t.status.value.lower() if hasattr(t.status, "value") else str(t.status).lower(),
            "transaction_time": t.created_at.isoformat() if t.created_at else None,
            "created_at": t.created_at.isoformat() if t.created_at else None,
            "receipt_number": inv.invoice_number if inv else None,
            "receipt_path": inv.pdf_url if inv else None,
            "refund_ref": ref_rec.refund_ref if ref_rec else None,
            "refund_amount": float(ref_rec.amount) if ref_rec else None,
            "is_duplicate": t.is_duplicate
        })

    return results


def get_customer_payment_history(
    cls,
    db: Session,
    user_id: Optional[str] = None,
    email: Optional[str] = None
) -> List[Dict[str, Any]]:
    """Returns customer payment history securely filtered by user ID or email."""
    from app.models.schema import Booking

    conditions = []
    if user_id:
        conditions.append(PaymentTransaction.customer_id == user_id)
    if email:
        bookings_by_email = list(db.scalars(select(Booking.booking_ref).where(Booking.passenger_email == email)).all())
        if bookings_by_email:
            conditions.append(PaymentTransaction.entity_id.in_(bookings_by_email))

    if not conditions:
        return []

    transactions = list(
        db.scalars(
            select(PaymentTransaction)
            .where(or_(*conditions))
            .order_by(desc(PaymentTransaction.created_at))
        ).all()
    )

    results = []
    for t in transactions:
        b = db.scalar(select(Booking).where(Booking.booking_ref == t.entity_id))
        results.append({
            "id": str(t.id),
            "transaction_ref": t.transaction_ref,
            "booking_ref": b.booking_ref if b else t.entity_id,
            "route": f"{b.origin_code} \u2192 {b.dest_code}" if b and b.origin_code and b.dest_code else "Airport Service",
            "service_type": b.service_type if b else "Concierge",
            "provider": t.gateway_provider,
            "amount": float(t.amount),
            "currency": t.currency,
            "status": t.status.value if hasattr(t.status, "value") else str(t.status),
            "transaction_time": t.created_at.isoformat() if t.created_at else None
        })

    return results
