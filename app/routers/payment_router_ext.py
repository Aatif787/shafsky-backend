"""
Admin Reconciliation, Ledger & Customer History Sub-Router for Payment Operations.
"""

import logging
from fastapi import APIRouter, HTTPException, Depends, status, Query
from sqlalchemy.orm import Session
from sqlalchemy import select

logger = logging.getLogger(__name__)

from app.database import get_db
from app.schemas.payment import (
    PaymentApiResponse,
    PaymentLedgerFilterRequest,
    PaymentCustomerHistoryRequest,
)
from app.services.payment_service import PaymentService
from app.security.dependencies import (
    get_required_user,
    get_required_staff_or_admin,
    get_required_admin,
)

admin_payment_router = APIRouter()


@admin_payment_router.get(
    "/admin/reconciliation",
    response_model=PaymentApiResponse,
    status_code=status.HTTP_200_OK,
    summary="Admin Payment Reconciliation Report"
)
def get_reconciliation_report_endpoint(
    db: Session = Depends(get_db),
    _admin = Depends(get_required_admin)
):
    """Scans all payment records, bookings, and invoices to detect discrepancies and anomalies."""
    report = PaymentService.get_reconciliation_report(db)
    return PaymentApiResponse(success=True, data=report)


@admin_payment_router.post(
    "/admin/reconcile-sync/{booking_ref}",
    response_model=PaymentApiResponse,
    status_code=status.HTTP_200_OK,
    summary="Admin Reconcile & Synchronize Booking with Gateway"
)
def reconcile_sync_endpoint(
    booking_ref: str,
    db: Session = Depends(get_db),
    _admin = Depends(get_required_admin)
):
    """Fetches live Razorpay order status and synchronizes the local database state."""
    try:
        result = PaymentService.reconcile_booking_payment(db, booking_ref=booking_ref)
        return PaymentApiResponse(success=True, data=result)
    except ValueError as err:
        raise HTTPException(status_code=400, detail=str(err)) from err


@admin_payment_router.post(
    "/reconcile-pending",
    response_model=PaymentApiResponse,
    status_code=status.HTTP_200_OK,
    summary="Reconcile and promote pending captured payments"
)
def reconcile_pending_endpoint(
    max_lookback_hours: int = 24,
    db: Session = Depends(get_db),
    _admin = Depends(get_required_admin),
):
    """
    Background-safe reconciliation worker. Scans pending transactions, queries Razorpay,
    and automatically confirms any captured orders or paid links.
    """
    from app.services.payment_reconciliation_service import PaymentReconciliationService
    result = PaymentReconciliationService.reconcile_pending_payments(db, max_lookback_hours=max_lookback_hours)
    return PaymentApiResponse(success=True, data=result)


@admin_payment_router.post(
    "/admin/expire-stale",
    response_model=PaymentApiResponse,
    status_code=status.HTTP_200_OK,
    summary="Admin Trigger Expire Stale Pending Orders"
)
def expire_stale_orders_endpoint(
    max_age_hours: float = Query(24.0, ge=1.0),
    db: Session = Depends(get_db),
    _admin = Depends(get_required_admin)
):
    """Marks pending payment transactions older than max_age_hours as EXPIRED."""
    count = PaymentService.expire_stale_transactions(db, max_age_hours=max_age_hours)
    return PaymentApiResponse(success=True, data={"expired_count": count, "max_age_hours": max_age_hours})


@admin_payment_router.post(
    "/admin/notifications/retry/{booking_ref}",
    response_model=PaymentApiResponse,
    status_code=status.HTTP_200_OK,
    summary="Admin Retry Booking Notifications"
)
def retry_booking_notifications_endpoint(
    booking_ref: str,
    db: Session = Depends(get_db),
    _admin = Depends(get_required_admin)
):
    """Re-dispatches booking confirmation notifications (Email + WhatsApp) for an already confirmed booking."""
    from app.models.schema import Booking, BookingStatus
    from app.services.notification_service import NotificationService

    booking = db.scalar(select(Booking).where(Booking.booking_ref == booking_ref))
    if not booking:
        raise HTTPException(status_code=404, detail="Booking not found.")

    if booking.status != BookingStatus.CONFIRMED:
        raise HTTPException(status_code=400, detail=f"Booking is in '{booking.status.value}' state; only CONFIRMED bookings can receive confirmation notices.")

    meta = booking.metadata_json or {}
    try:
        NotificationService.notify_booking_confirmed(db, {
            "booking_ref": booking.booking_ref,
            "passenger_name": booking.passenger_name,
            "passenger_email": booking.passenger_email,
            "passenger_phone": booking.passenger_phone,
            "flight_num": booking.flight_num,
            "origin_code": booking.origin_code,
            "dest_code": booking.dest_code,
            "airport_code": meta.get("service_airport") or booking.origin_code or booking.dest_code,
            "journey_type": meta.get("journey_type") or booking.service_type,
            "service_type": booking.service_type,
            "service_name": meta.get("package") or booking.service_type,
            "departure_time": booking.departure_time.isoformat() if booking.departure_time else None,
            "terminal": meta.get("terminal"),
            "total_amount": float(booking.total_amount) if booking.total_amount is not None else 0.0,
            "currency": booking.currency,
        })
        return PaymentApiResponse(success=True, data={"message": f"Notifications queued for {booking_ref}."})
    except Exception as err:
        raise HTTPException(status_code=500, detail=f"Failed to dispatch notifications: {str(err)}") from err


@admin_payment_router.post(
    "/ledger",
    response_model=PaymentApiResponse,
    status_code=status.HTTP_200_OK,
    summary="Super Admin Payment Ledger List"
)
def list_payment_ledger_endpoint(
    payload: PaymentLedgerFilterRequest = PaymentLedgerFilterRequest(),
    db: Session = Depends(get_db),
    _admin = Depends(get_required_staff_or_admin)
):
    """Returns all PaymentTransaction records formatted for the Super Admin payment dashboard."""
    ledger = PaymentService.get_payment_ledger(
        db,
        search=payload.search,
        provider=payload.provider,
        status=payload.status
    )
    return PaymentApiResponse(success=True, data=ledger)


@admin_payment_router.post(
    "/customer-history",
    response_model=PaymentApiResponse,
    status_code=status.HTTP_200_OK,
    summary="Customer Payment History"
)
def customer_payment_history_endpoint(
    payload: PaymentCustomerHistoryRequest = PaymentCustomerHistoryRequest(),
    db: Session = Depends(get_db),
    current_user = Depends(get_required_user)
):
    """Returns payment history for the authenticated customer."""
    user_id = current_user.get("user_id") or current_user.get("userId")
    email = current_user.get("email") or current_user.get("sub")

    if payload.userId and payload.userId != str(user_id or ""):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You may only view your own payment history.",
        )

    history = PaymentService.get_customer_payment_history(
        db,
        user_id=str(user_id) if user_id else None,
        email=email
    )
    return PaymentApiResponse(success=True, data=history)

