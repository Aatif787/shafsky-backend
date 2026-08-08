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
from app.models.payment import PaymentTransaction, Invoice, Refund
from app.schemas.payment import (
    PaymentInitiateRequest,
    PaymentTransactionResponse,
    WebhookPayload,
    RefundRequest,
    RefundResponse,
    InvoiceResponse,
    PaymentApiResponse
)
from app.services.payment_service import PaymentService
from app.security.dependencies import get_required_user, get_required_staff_or_admin

router = APIRouter(prefix="/api/payments", tags=["Payment & Invoicing"])


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
    "/webhook",
    response_model=PaymentApiResponse,
    status_code=status.HTTP_200_OK,
    summary="Payment Gateway Webhook Callback"
)
def payment_webhook_endpoint(
    payload: WebhookPayload,
    db: Session = Depends(get_db)
):
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
    Validates HMAC SHA256 signature, processes server-side payment confirmation (payment.captured, order.paid),
    and updates booking status to PAID/CONFIRMED idempotently.
    """
    body_bytes = await request.body()
    signature = request.headers.get("X-Razorpay-Signature") or request.headers.get("x-razorpay-signature")

    from app.providers.razorpay_provider import razorpay_provider
    from app.integrations.whatsapp.service import WhatsAppBookingStateMachine

    # Verify signature if configured
    if razorpay_provider.is_configured():
        if not razorpay_provider.verify_webhook_signature(body_bytes, signature):
            raise HTTPException(status_code=400, detail="Invalid Razorpay webhook signature header.")

    try:
        import json
        payload = json.loads(body_bytes.decode("utf-8"))
    except Exception:
        return PaymentApiResponse(success=False, error="Invalid JSON body")

    event_name = payload.get("event", "unknown")
    entity = payload.get("payload", {}).get("payment", {}).get("entity", {})
    if not entity:
        entity = payload.get("payload", {}).get("payment_link", {}).get("entity", {})

    notes = entity.get("notes", {})
    booking_ref = notes.get("booking_ref") or entity.get("reference_id") or payload.get("payload", {}).get("order", {}).get("entity", {}).get("receipt")
    payment_id = entity.get("id") or f"pay_{uuid.uuid4().hex[:10]}"

    logger.info(f"[Razorpay Webhook] Received event '{event_name}' for ref '{booking_ref}'")

    if event_name in ["payment.captured", "payment.authorized", "order.paid", "payment_link.paid"]:
        if booking_ref:
            WhatsAppBookingStateMachine.handle_payment_success(db, booking_ref=booking_ref, payment_id=payment_id)
        return PaymentApiResponse(success=True, data={"status": "PAID", "event": event_name, "booking_ref": booking_ref})

    elif event_name in ["payment.failed"]:
        logger.warning(f"[Razorpay Webhook] Payment failed for booking ref '{booking_ref}'")
        return PaymentApiResponse(success=True, data={"status": "FAILED", "event": event_name, "booking_ref": booking_ref})

    return PaymentApiResponse(success=True, data={"status": "ignored", "event": event_name})
