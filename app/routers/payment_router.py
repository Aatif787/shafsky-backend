"""
REST Router for Payment & Invoicing Operations.
"""

import uuid
from typing import Optional, List
import logging
from fastapi import APIRouter, HTTPException, Depends, Header, status, Query, Request
from sqlalchemy.orm import Session
from sqlalchemy import select

logger = logging.getLogger(__name__)

from app.database import get_db
from app.models.payment import PaymentTransaction, Invoice, Refund, PaymentWebhookEvent
from app.schemas.payment import (
    PaymentInitiateRequest,
    PaymentTransactionResponse,
    WebhookPayload,
    RefundRequest,
    RefundResponse,
    InvoiceResponse,
    PaymentApiResponse,
    PaymentVerifyRequest,
    PaymentRetryRequest,
    PaymentLedgerFilterRequest,
    PaymentCustomerHistoryRequest,
    RazorpayCreateOrderRequest,
    RazorpayCreateOrderResponse,
)
from app.services.payment_service import PaymentService
from app.security.dependencies import get_required_user, get_required_staff_or_admin, get_required_admin

router = APIRouter(prefix="/api/payments", tags=["Payment & Invoicing"])


@router.post(
    "/create-order",
    response_model=RazorpayCreateOrderResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create Razorpay Standard Checkout Order"
)
@router.post(
    "/orders",
    response_model=RazorpayCreateOrderResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create Razorpay Order"
)
async def create_order_endpoint(
    payload: RazorpayCreateOrderRequest,
    db: Session = Depends(get_db)
):
    """
    Creates an official Razorpay Order for Standard Checkout.
    Validates amount >= 100 paise, calls Razorpay API, and returns order details.
    """
    from app.providers.razorpay_provider import razorpay_provider

    if payload.amount < 100:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Amount must be at least 100 paise (INR 1.00)."
        )

    res = razorpay_provider.create_order(
        amount=payload.amount,
        currency=payload.currency or "INR",
        receipt=payload.receipt,
        notes=payload.notes,
        amount_is_paise=True
    )

    if not res.get("success"):
        err_msg = res.get("error", "Razorpay order creation failed.")
        if "401" in err_msg or "auth" in err_msg.lower():
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=err_msg)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=err_msg)

    return RazorpayCreateOrderResponse(
        order_id=res["order_id"],
        amount=res["amount"],
        currency=res["currency"],
        key_id=res.get("key_id", razorpay_provider.key_id),
        receipt=payload.receipt
    )


@router.post(
    "/initiate",
    response_model=PaymentApiResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Initiate Payment Transaction"
)
def initiate_payment_endpoint(
    payload: PaymentInitiateRequest,
    db: Session = Depends(get_db),
    current_user = Depends(get_required_user)
):
    try:
        tx = PaymentService.initiate_payment(db, payload)
        return PaymentApiResponse(
            success=True,
            data={
                "id": str(tx.id),
                "transaction_ref": tx.transaction_ref,
                "amount": tx.amount,
                "currency": tx.currency,
                "status": tx.status.value if hasattr(tx.status, "value") else str(tx.status),
                "gateway_payment_id": tx.gateway_payment_id,
                "gateway_response": tx.gateway_response
            }
        )
    except ValueError as err:
        raise HTTPException(status_code=400, detail=str(err)) from err


@router.post(
    "/verify-payment",
    response_model=PaymentApiResponse,
    status_code=status.HTTP_200_OK,
    summary="Verify Razorpay Payment Signature"
)
@router.post(
    "/verify",
    response_model=PaymentApiResponse,
    status_code=status.HTTP_200_OK,
    summary="Verify Frontend Razorpay Checkout Payment Signature"
)
async def verify_payment_endpoint(
    payload: PaymentVerifyRequest,
    db: Session = Depends(get_db)
):
    """
    Validates Razorpay Checkout HMAC SHA256 signature:
    Algorithm: HMAC-SHA256(order_id + "|" + payment_id, KEY_SECRET)
    """
    if not payload.razorpay_order_id or not payload.razorpay_payment_id or not payload.razorpay_signature:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing required fields: razorpay_order_id, razorpay_payment_id, razorpay_signature."
        )

    from app.providers.razorpay_provider import razorpay_provider

    is_valid = razorpay_provider.verify_payment_signature(
        razorpay_order_id=payload.razorpay_order_id,
        razorpay_payment_id=payload.razorpay_payment_id,
        razorpay_signature=payload.razorpay_signature
    )
    if not is_valid:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid Razorpay payment signature.")

    result = None
    if payload.booking_ref:
        result = PaymentService.handle_verified_payment(
            db,
            event_name="VERIFY_ENDPOINT",
            gateway_provider="RAZORPAY",
            order_id=payload.razorpay_order_id,
            payment_id=payload.razorpay_payment_id,
            booking_ref=payload.booking_ref,
            signature=payload.razorpay_signature,
            channel="web"
        )
        if not result.get("success"):
            raise HTTPException(status_code=400, detail=result.get("reason", "Payment verification processing failed."))

    return PaymentApiResponse(
        success=True,
        data={
            "status": "CONFIRMED" if (not result or result.get("success")) else result.get("status", "CONFIRMED"),
            "order_id": payload.razorpay_order_id,
            "payment_id": payload.razorpay_payment_id,
            "booking_ref": payload.booking_ref or (result.get("booking_ref") if result else None)
        }
    )


@router.post(
    "/webhook",
    response_model=PaymentApiResponse,
    status_code=status.HTTP_200_OK,
    summary="Payment Gateway Webhook Callback"
)
def payment_webhook_endpoint(
    payload: WebhookPayload,
    request: Request,
    db: Session = Depends(get_db)
):
    signature = payload.signature or request.headers.get("X-Webhook-Signature") or request.headers.get("x-webhook-signature")
    if not PaymentService.verify_internal_webhook_signature(payload, signature):
        raise HTTPException(status_code=401, detail="Invalid webhook signature.")
    try:
        tx = PaymentService.process_webhook(db, payload)
        return PaymentApiResponse(
            success=True,
            data={
                "transaction_ref": tx.transaction_ref,
                "status": tx.status.value if hasattr(tx.status, "value") else str(tx.status)
            }
        )
    except ValueError as err:
        raise HTTPException(status_code=400, detail=str(err)) from err


@router.post(
    "/refund",
    response_model=PaymentApiResponse,
    status_code=status.HTTP_200_OK,
    summary="Process Refund"
)
def process_refund_endpoint(
    payload: RefundRequest,
    db: Session = Depends(get_db),
    current_user = Depends(get_required_staff_or_admin)
):
    try:
        ref = PaymentService.process_refund(db, payload)
        return PaymentApiResponse(
            success=True,
            data={
                "id": str(ref.id),
                "refund_ref": ref.refund_ref,
                "amount": ref.amount,
                "currency": ref.currency,
                "status": ref.status.value if hasattr(ref.status, "value") else str(ref.status)
            }
        )
    except ValueError as err:
        raise HTTPException(status_code=400, detail=str(err)) from err


@router.get(
    "/transactions/{transaction_id}",
    response_model=PaymentApiResponse,
    status_code=status.HTTP_200_OK,
    summary="Get Payment Transaction Details"
)
def get_transaction_endpoint(
    transaction_id: str,
    db: Session = Depends(get_db),
    current_user = Depends(get_required_user)
):
    try:
        tx_uuid = uuid.UUID(transaction_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid transaction UUID format.")

    tx = db.scalar(select(PaymentTransaction).where(PaymentTransaction.id == tx_uuid))
    if not tx:
        raise HTTPException(status_code=404, detail="Transaction not found.")

    role = current_user.get("role")
    email = (current_user.get("sub") or current_user.get("email") or "").lower()
    user_id = str(current_user.get("user_id") or current_user.get("userId") or "")
    is_staff = role in (
        "SUPER_ADMIN", "ADMIN", "OPERATIONS_MANAGER", "DUTY_OFFICER",
        "MEET_AND_ASSIST_STAFF", "CONCIERGE_TEAM", "CUSTOMER_SUPPORT",
    )
    owns = False
    if user_id and tx.customer_id and str(tx.customer_id) == user_id:
        owns = True
    gateway = tx.gateway_response or {}
    if email and str(gateway.get("customer_email", "")).lower() == email:
        owns = True
    if not is_staff and not owns:
        raise HTTPException(status_code=403, detail="Access denied.")

    return PaymentApiResponse(
        success=True,
        data={
            "id": str(tx.id),
            "transaction_ref": tx.transaction_ref,
            "amount": tx.amount,
            "currency": tx.currency,
            "status": tx.status.value if hasattr(tx.status, "value") else str(tx.status),
            "gateway_payment_id": tx.gateway_payment_id,
        }
    )


@router.post("/confirm", status_code=status.HTTP_403_FORBIDDEN, summary="Legacy Payment Confirm (Disabled)")
def legacy_confirm_payment_disabled():
    """Explicitly neutralizes legacy direct client payment confirmation endpoint."""
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Direct client payment confirmation is disabled. Payments must be verified via gateway signatures (/api/payments/verify) or webhooks."
    )


@router.post(
    "/razorpay/webhook",
    response_model=PaymentApiResponse,
    status_code=status.HTTP_200_OK,
    summary="Official Razorpay Webhook Callback Endpoint"
)
async def razorpay_webhook_endpoint(
    request: Request,
    db: Session = Depends(get_db)
):
    """
    Official Razorpay Webhook Endpoint.
    1. Validates HMAC SHA256 signature header.
    2. Performs persistent database-level event deduplication via PaymentWebhookEvent.
    3. Executes canonical, idempotent payment state transitions (PaymentTransaction + Booking + Invoice).
    4. Guarantees safe responses to prevent gateway retry storms.
    """
    body_bytes = await request.body()
    signature = request.headers.get("X-Razorpay-Signature") or request.headers.get("x-razorpay-signature")

    from app.providers.razorpay_provider import razorpay_provider

    if not razorpay_provider.verify_webhook_signature(body_bytes, signature):
        logger.warning("[Razorpay Webhook] Rejected: Invalid HMAC signature")
        raise HTTPException(status_code=400, detail="Invalid Razorpay webhook signature header.")

    try:
        import json
        payload = json.loads(body_bytes.decode("utf-8"))
    except Exception:
        return PaymentApiResponse(success=False, error="Invalid JSON body")

    event_name = payload.get("event", "unknown")
    event_hdr_id = request.headers.get("X-Razorpay-Event-Id") or request.headers.get("x-razorpay-event-id")
    
    entity = payload.get("payload", {}).get("payment", {}).get("entity", {})
    if not entity:
        entity = payload.get("payload", {}).get("payment_link", {}).get("entity", {})
    if not entity:
        entity = payload.get("payload", {}).get("order", {}).get("entity", {})
    if not entity:
        entity = payload.get("payload", {}).get("refund", {}).get("entity", {})

    notes = entity.get("notes", {}) if isinstance(entity, dict) else {}
    order_id = entity.get("order_id") or payload.get("payload", {}).get("order", {}).get("entity", {}).get("id")
    payment_id = entity.get("id") if entity.get("id", "").startswith("pay_") else payload.get("payload", {}).get("payment", {}).get("entity", {}).get("id")
    booking_ref = notes.get("booking_ref") or entity.get("reference_id") or payload.get("payload", {}).get("order", {}).get("entity", {}).get("receipt")
    channel = notes.get("channel", "web")

    # Construct canonical persistent event identifier
    unique_event_id = event_hdr_id or payload.get("event_id") or f"{event_name}:{order_id or payment_id or booking_ref}"

    # Persistent Webhook Idempotency Check
    existing_event = db.scalar(select(PaymentWebhookEvent).where(PaymentWebhookEvent.event_id == unique_event_id))
    if existing_event:
        logger.info(f"[Razorpay Webhook] Duplicate event '{unique_event_id}' received (already processed as {existing_event.status}). Returning 200 OK.")
        return PaymentApiResponse(
            success=True,
            data={"status": "DUPLICATE_IGNORED", "event": event_name, "event_id": unique_event_id}
        )

    # Record Webhook Event
    webhook_log = PaymentWebhookEvent(
        id=uuid.uuid4(),
        event_id=unique_event_id,
        event_type=event_name,
        gateway_provider="RAZORPAY",
        payload=payload,
        status="PROCESSED"
    )
    db.add(webhook_log)
    db.flush()

    logger.info(f"[Razorpay Webhook] Processing verified event '{event_name}' (ID: {unique_event_id}, ref: '{booking_ref}')")

    amount_raw = entity.get("amount") if entity.get("amount") is not None else entity.get("amount_paid")
    currency_raw = entity.get("currency")

    # Delegate to canonical payment handler
    result = PaymentService.handle_verified_payment(
        db,
        event_name=event_name,
        gateway_provider="RAZORPAY",
        order_id=order_id,
        payment_id=payment_id,
        booking_ref=booking_ref,
        signature=signature,
        raw_payload=payload,
        channel=channel,
        amount=amount_raw,
        currency=currency_raw
    )

    return PaymentApiResponse(
        success=True,
        data={
            "status": result.get("status", "PROCESSED"),
            "event": event_name,
            "booking_ref": result.get("booking_ref"),
            "event_id": unique_event_id
        }
    )


@router.post(
    "/retry",
    response_model=PaymentApiResponse,
    status_code=status.HTTP_200_OK,
    summary="Retry Payment for Pending Booking"
)
def retry_payment_endpoint(
    payload: PaymentRetryRequest,
    db: Session = Depends(get_db)
):
    """Generates a fresh Razorpay order for an existing PENDING booking without duplicating the booking."""
    try:
        data = PaymentService.retry_booking_payment(db, booking_ref=payload.booking_ref)
        return PaymentApiResponse(success=True, data=data)
    except ValueError as err:
        message = str(err)
        lowered = message.lower()
        status_code = (
            status.HTTP_502_BAD_GATEWAY
            if "razorpay authentication" in lowered or "authentication failed" in lowered
            else status.HTTP_400_BAD_REQUEST
        )
        raise HTTPException(status_code=status_code, detail=message) from err


@router.get(
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


@router.post(
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


@router.post(
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


@router.post(
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


@router.post(
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


@router.post(
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
    user_id = payload.userId or current_user.get("user_id") or current_user.get("userId")
    email = current_user.get("email") or current_user.get("sub")

    history = PaymentService.get_customer_payment_history(
        db,
        user_id=str(user_id) if user_id else None,
        email=email
    )
    return PaymentApiResponse(success=True, data=history)


