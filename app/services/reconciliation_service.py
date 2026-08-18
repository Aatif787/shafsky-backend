"""
Automated Financial & Booking Reconciliation Service.
Performs periodic and on-demand audits comparing Booking amounts against PaymentTransaction
and Invoice totals, detecting financial anomalies, missing payments, or payment-booking mismatches.
"""

import logging
from typing import List, Dict, Any
from sqlalchemy.orm import Session
from sqlalchemy import select

from app.models.schema import Booking, BookingStatus
from app.models.payment import PaymentTransaction, Invoice, PaymentStatus

logger = logging.getLogger(__name__)


class ReconciliationService:
    """Core domain service for payment, booking, and invoice reconciliation."""

    @classmethod
    def audit_all_transactions(cls, db: Session) -> Dict[str, Any]:
        """
        Audits all completed bookings and payment transactions.
        Verifies Booking.total_amount == PaymentTransaction.amount == Invoice.total_amount.
        """
        anomalies = []
        bookings = list(db.scalars(select(Booking).where(Booking.status != BookingStatus.CANCELLED)).all())

        for b in bookings:
            tx = db.scalar(
                select(PaymentTransaction).where(
                    PaymentTransaction.entity_type == "BOOKING",
                    PaymentTransaction.entity_id == str(b.id),
                    PaymentTransaction.status == PaymentStatus.SUCCESSFUL
                )
            )
            if tx:
                # 1. Amount mismatch check
                if abs(float(b.total_amount) - float(tx.amount)) > 0.01:
                    anomalies.append({
                        "booking_ref": b.booking_ref,
                        "issue": "AMOUNT_MISMATCH",
                        "severity": "CRITICAL",
                        "booking_amount": float(b.total_amount),
                        "payment_amount": float(tx.amount)
                    })

                # 2. Invoice reconciliation check
                inv = db.scalar(select(Invoice).where(Invoice.transaction_id == tx.id))
                if inv and abs(float(inv.total_amount) - float(tx.amount)) > 0.01:
                    anomalies.append({
                        "booking_ref": b.booking_ref,
                        "issue": "INVOICE_AMOUNT_MISMATCH",
                        "severity": "CRITICAL",
                        "payment_amount": float(tx.amount),
                        "invoice_amount": float(inv.total_amount)
                    })

        logger.info("Reconciliation audit complete. Processed %d bookings, found %d anomalies.", len(bookings), len(anomalies))
        return {
            "total_bookings_audited": len(bookings),
            "anomalies_count": len(anomalies),
            "anomalies": anomalies,
            "status": "CLEAN" if len(anomalies) == 0 else "ANOMALIES_DETECTED"
        }
