"""
REST Router for Payment & Invoicing Operations.
"""

import uuid
from typing import Optional, List
import logging
from fastapi import APIRouter, HTTPException, Depends, Header, status, Query, Request
from sqlalchemy.orm import Session
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

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
    summary="Create Razorpay Order (Alias)",
    deprecated=True,
)
async def create_order_endpoint(
    payload: RazorpayCreateOrderRequest,
    db: Session = Depends(get_db)
):
    """
    Creates an official Razorpay Order for Standard Checkout.
    Resolves authoritative amount from the persisted Booking and validates amounts.
    """
    from app.providers.razorpay_provider import razorpay_provider
    from app.models.schema import Booking, BookingStatus
    from app.models.airport import AirportBooking
    from app.models.payment import PaymentTransaction, PaymentStatus, PaymentMethod

    booking_ref = (payload.receipt or (payload.notes.get("booking_ref") if payload.notes else None) or "").strip()
    if not booking_ref:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A valid booking reference is required to create a payment order."
        )

    # 1. Resolve authoritative booking & amount
    booking = db.scalar(select(Booking).where(Booking.booking_ref == booking_ref))
    authoritative_amount_rupees = None
    authoritative_currency = "INR"
    customer_id = None

    if booking:
        if booking.status == BookingStatus.CONFIRMED:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Booking '{booking_ref}' is already confirmed and paid.",
            )
        if booking.status in (BookingStatus.CANCELLED, BookingStatus.REJECTED):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Booking '{booking_ref}' cannot accept payment in its current state.",
            )
        if booking.total_amount is None or float(booking.total_amount) <= 0:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Booking has no payable total amount.")
        authoritative_amount_rupees = float(booking.total_amount)
        authoritative_currency = booking.currency or "INR"
        customer_id = str(booking.user_id) if booking.user_id else None
    else:
        apt_booking = db.scalar(select(AirportBooking).where(AirportBooking.booking_reference == booking_ref))
        if apt_booking:
            if str(apt_booking.status or "").upper() in {
                "CONFIRMED",
                "CANCELLED",
                "REJECTED",
                "COMPLETED",
            }:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"Airport booking '{booking_ref}' cannot accept a new payment order.",
                )
            if apt_booking.total_price is None or float(apt_booking.total_price) <= 0:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Airport booking has no payable total amount.")
            authoritative_amount_rupees = float(apt_booking.total_price)
            authoritative_currency = apt_booking.currency or "INR"
            customer_id = str(apt_booking.customer_id) if apt_booking.customer_id else None
        else:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Booking '{booking_ref}' not found.")

    authoritative_paise = int(round(authoritative_amount_rupees * 100))
    if authoritative_paise < 100:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Amount must be at least 100 paise (INR 1.00)."
        )

    # Reuse an active order for the booking. This makes repeated browser calls
    # idempotent and prevents unauthenticated order-spam against a known ref.
    existing_tx = db.scalar(
        select(PaymentTransaction)
        .where(
            PaymentTransaction.entity_id == booking_ref,
            PaymentTransaction.status.in_(
                [PaymentStatus.PENDING, PaymentStatus.PROCESSING]
            ),
            PaymentTransaction.gateway_provider == "RAZORPAY",
        )
        .order_by(PaymentTransaction.created_at.desc())
    )
    existing_order_id = str(existing_tx.gateway_payment_id or "") if existing_tx else ""
    if existing_order_id.startswith("order_") and not existing_order_id.startswith("order_sim_"):
        return RazorpayCreateOrderResponse(
            order_id=existing_order_id,
            amount=authoritative_paise,
            currency=authoritative_currency,
            key_id=razorpay_provider.key_id,
            receipt=booking_ref,
        )

    # Ensure client payload amount matches authoritative amount
    if payload.amount and abs(payload.amount - authoritative_paise) > 1:
        logger.warning(
            f"[create_order] Security rejection: Client amount {payload.amount} != authoritative amount {authoritative_paise} for {booking_ref}"
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Amount mismatch. Authoritative booking amount is {authoritative_amount_rupees} {authoritative_currency}."
        )

    order_notes = dict(payload.notes or {})
    order_notes["booking_ref"] = booking_ref
    order_notes["channel"] = order_notes.get("channel", "web")

    res = razorpay_provider.create_order(
        amount=authoritative_paise,
        currency=authoritative_currency,
        receipt=booking_ref,
        notes=order_notes,
        amount_is_paise=True
    )

    if not res.get("success"):
        err_msg = res.get("error", "Razorpay order creation failed.")
        if "401" in err_msg or "auth" in err_msg.lower():
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=err_msg)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=err_msg)

    order_id = res["order_id"]

    # Check if a pending transaction already exists for this order/booking
    tx = db.scalar(
        select(PaymentTransaction).where(
            PaymentTransaction.entity_id == booking_ref,
            PaymentTransaction.status == PaymentStatus.PENDING,
        ).order_by(PaymentTransaction.created_at.desc())
    )
    if not tx:
        ref = f"PAY-{uuid.uuid4().hex[:8].upper()}"
        tx = PaymentTransaction(
            transaction_ref=ref,
            entity_type="AIRPORT_BOOKING",
            entity_id=booking_ref,
            customer_id=customer_id,
            amount=authoritative_amount_rupees,
            currency=authoritative_currency,
            payment_method=PaymentMethod.CREDIT_CARD,
            status=PaymentStatus.PENDING,
            gateway_provider="RAZORPAY",
            gateway_payment_id=order_id,
            gateway_response=res
        )
        db.add(tx)
    else:
        tx.gateway_payment_id = order_id
        tx.gateway_response = res

    try:
        db.commit()
    except Exception as db_err:
        db.rollback()
        logger.error(f"Failed to persist payment transaction for {booking_ref}: {db_err}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to record payment order in database."
        ) from db_err

    return RazorpayCreateOrderResponse(
        order_id=order_id,
        amount=res["amount"],
        currency=res["currency"],
        key_id=res.get("key_id", razorpay_provider.key_id),
        receipt=booking_ref
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
    summary="Verify Frontend Razorpay Checkout Payment Signature (Alias)",
    deprecated=True,
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
    try:
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
    except Exception as exc:
        logger.exception("[verify] Booking confirmation failed after a valid Razorpay signature")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Payment processing failed: {str(exc)}"
        )

    if not result or not result.get("success"):
        error_status = (result or {}).get("status", "VERIFICATION_FAILED")
        error_reason = (result or {}).get("reason", "Payment verification processing failed.")
        logger.error(f"[verify] Payment verification rejected: {error_status} - {error_reason}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Payment verification failed: {error_reason}"
        )

    return PaymentApiResponse(
        success=True,
        data={
            "status": result.get("status", "CONFIRMED"),
            "order_id": payload.razorpay_order_id,
            "payment_id": payload.razorpay_payment_id,
            "booking_ref": result.get("booking_ref") or payload.booking_ref,
            "already_confirmed": result.get("already_confirmed", False)
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
        raise HTTPException(status_code=400, detail="Invalid JSON body.")

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
    if not isinstance(notes, dict):
        notes = {}
    plink_entity = payload.get("payload", {}).get("payment_link", {}).get("entity", {})
    plink_notes = plink_entity.get("notes", {}) if isinstance(plink_entity, dict) else {}
    if not isinstance(plink_notes, dict):
        plink_notes = {}
    order_entity_id = payload.get("payload", {}).get("order", {}).get("entity", {}).get("id")
    entity_id = str((entity.get("id") if isinstance(entity, dict) else None) or "")
    order_id = (entity.get("order_id") if isinstance(entity, dict) else None) or (plink_entity.get("order_id") if isinstance(plink_entity, dict) else None) or order_entity_id
    payment_id = entity_id if entity_id.startswith("pay_") else payload.get("payload", {}).get("payment", {}).get("entity", {}).get("id")
    if payment_id is not None:
        payment_id = str(payment_id)
    booking_ref = (
        notes.get("booking_ref")
        or plink_notes.get("booking_ref")
        or (entity.get("reference_id") if isinstance(entity, dict) else None)
        or (plink_entity.get("reference_id") if isinstance(plink_entity, dict) else None)
        or payload.get("payload", {}).get("order", {}).get("entity", {}).get("receipt")
    )
    channel = notes.get("channel") or plink_notes.get("channel") or "web"

    plink_id = plink_entity.get("id") if isinstance(plink_entity, dict) else None

    # Construct canonical persistent event identifier
    unique_event_id = event_hdr_id or payload.get("event_id") or f"{event_name}:{payment_id or order_id or plink_id or booking_ref}"

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
    try:
        db.flush()
    except IntegrityError:
        # A concurrent delivery may win the unique event_id insert after our
        # initial lookup. Treat that race exactly like a normal duplicate.
        db.rollback()
        return PaymentApiResponse(
            success=True,
            data={
                "status": "DUPLICATE_IGNORED",
                "event": event_name,
                "event_id": unique_event_id,
            },
        )

    logger.info(f"[Razorpay Webhook] Processing verified event '{event_name}' (ID: {unique_event_id}, ref: '{booking_ref}')")

    amount_raw = entity.get("amount") if isinstance(entity, dict) and entity.get("amount") is not None else (entity.get("amount_paid") if isinstance(entity, dict) else None)
    currency_raw = entity.get("currency") if isinstance(entity, dict) else None

    try:
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
    except Exception:
        logger.exception("[Razorpay Webhook] Canonical payment processing failed after HMAC verification")
        try:
            db.rollback()
        except Exception:
            pass
        raise HTTPException(status_code=500, detail="Payment processing failed.") from None

    if not result.get("success"):
        result_status = str(result.get("status") or "PROCESSING_FAILED")
        # These failures can be caused by delivery ordering or database
        # availability. Roll back the event claim and ask Razorpay to retry.
        if result_status in {
            "COMMIT_FAILED",
            "NOT_FOUND",
            "REFUND_TRANSACTION_NOT_FOUND",
        }:
            db.rollback()
            logger.error(
                "[Razorpay Webhook] Retryable processing failure for event %s: %s",
                unique_event_id,
                result_status,
            )
            raise HTTPException(
                status_code=503,
                detail="Webhook processing is temporarily incomplete.",
            )

        # Permanent validation failures are retained for investigation and
        # acknowledged to avoid an endless gateway retry storm.
        webhook_log.status = "FAILED"
        db.commit()
        return PaymentApiResponse(
            success=False,
            data={
                "status": result_status,
                "event": event_name,
                "booking_ref": result.get("booking_ref"),
                "event_id": unique_event_id,
            },
            error=result.get("reason") or "Webhook event was rejected.",
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
    user_id = current_user.get("user_id") or current_user.get("userId")
    email = current_user.get("email") or current_user.get("sub")

    if payload.userId and str(payload.userId) != str(user_id or ""):
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


