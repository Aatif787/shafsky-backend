"""
REST Router for Payment & Invoicing Operations.
"""

import uuid
from typing import Optional, List
from fastapi import APIRouter, HTTPException, Depends, Header, status, Query
from sqlalchemy.orm import Session
from sqlalchemy import select

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
    if not tx:
        raise HTTPException(status_code=404, detail="Transaction not found.")

    return PaymentApiResponse(
        success=True,
        data={
            "id": str(tx.id),
            "transaction_ref": tx.transaction_ref,
            "entity_type": tx.entity_type,
            "entity_id": tx.entity_id,
            "amount": tx.amount,
            "currency": tx.currency,
            "status": tx.status.value if hasattr(tx.status, "value") else str(tx.status),
            "gateway_provider": tx.gateway_provider,
            "created_at": tx.created_at.isoformat()
        }
    )
