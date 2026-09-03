"""
Payment Reconciliation Service.
Safeguards all pending transactions by querying Razorpay API and promoting
captured orders/payment links to CONFIRMED state automatically.
"""

import logging
from typing import Dict, Any, List
from datetime import datetime, timezone, timedelta
from sqlalchemy.orm import Session
from sqlalchemy import select, and_

from app.models.payment import PaymentTransaction, PaymentStatus
from app.models.schema import Booking, BookingStatus
from app.providers.razorpay_provider import razorpay_provider
from app.services.payment_service import PaymentService

logger = logging.getLogger("shafsky.payment.reconciliation")


class PaymentReconciliationService:
    @classmethod
    def reconcile_pending_payments(
        cls,
        db: Session,
        max_lookback_hours: int = 24,
        min_age_minutes: int = 3
    ) -> Dict[str, Any]:
        """
        Scans all pending and processing transactions in the lookback window,
        verifies them against Razorpay, and confirms any captured bookings.
        """
        now = datetime.now(timezone.utc)
        cutoff_start = now - timedelta(hours=max_lookback_hours)
        cutoff_end = now - timedelta(minutes=min_age_minutes)

        stmt = (
            select(PaymentTransaction)
            .where(
                and_(
                    PaymentTransaction.status.in_([PaymentStatus.PENDING, PaymentStatus.PROCESSING]),
                    PaymentTransaction.created_at >= cutoff_start,
                    PaymentTransaction.created_at <= cutoff_end,
                    PaymentTransaction.is_duplicate.isnot(True),
                    PaymentTransaction.gateway_provider == "RAZORPAY"
                )
            )
            .order_by(PaymentTransaction.created_at.desc())
        )

        transactions = db.scalars(stmt).all()
        scanned_count = len(transactions)
        reconciled_count = 0
        reconciled_details: List[Dict[str, Any]] = []

        logger.info(f"[PaymentReconciliation] Scanning {scanned_count} pending transactions...")

        for tx in transactions:
            target_id = tx.gateway_payment_id or ""
            booking_ref = tx.entity_id

            try:
                # 1. Order-based reconciliation (Web Checkout)
                if target_id.startswith("order_"):
                    payments_res = razorpay_provider.fetch_order_payments(target_id)
                    if payments_res.get("success"):
                        items = payments_res.get("items", [])
                        captured_payment = next(
                            (p for p in items if str(p.get("status", "")).lower() == "captured"),
                            None
                        )

                        if captured_payment:
                            pay_id = captured_payment.get("id")
                            amt = float(captured_payment.get("amount", 0)) / 100.0
                            curr = captured_payment.get("currency", "INR")

                            result = PaymentService.handle_verified_payment(
                                db,
                                event_name="ORDER_RECONCILED",
                                gateway_provider="RAZORPAY",
                                order_id=target_id,
                                payment_id=pay_id,
                                booking_ref=booking_ref,
                                amount=amt,
                                currency=curr,
                                channel="web_reconciliation"
                            )

                            if result.get("success"):
                                reconciled_count += 1
                                reconciled_details.append({
                                    "booking_ref": booking_ref,
                                    "order_id": target_id,
                                    "payment_id": pay_id,
                                    "amount": amt,
                                    "status": "CONFIRMED"
                                })
                                logger.info(f"[PaymentReconciliation] Successfully reconciled booking {booking_ref} (order: {target_id}, payment: {pay_id})")

                # 2. Payment Link-based reconciliation (WhatsApp)
                elif target_id.startswith("plink_") and booking_ref:
                    link_res = PaymentService.reconcile_whatsapp_payment_link(db, booking_ref)
                    if link_res.get("success") and link_res.get("status") == "PAID":
                        reconciled_count += 1
                        reconciled_details.append({
                            "booking_ref": booking_ref,
                            "payment_link_id": target_id,
                            "status": "CONFIRMED"
                        })
                        logger.info(f"[PaymentReconciliation] Successfully reconciled WhatsApp link for booking {booking_ref}")

            except Exception as ex:
                logger.warning(f"[PaymentReconciliation] Error reconciling tx {tx.transaction_ref} (booking {booking_ref}): {ex}")

        return {
            "success": True,
            "scanned": scanned_count,
            "reconciled": reconciled_count,
            "details": reconciled_details,
            "timestamp": now.isoformat()
        }
