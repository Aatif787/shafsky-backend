"""
Pydantic Schemas for Payment & Invoicing API.
"""

from typing import Optional, Dict, Any, List
from datetime import datetime
from pydantic import BaseModel, Field, EmailStr
from app.models.payment import PaymentStatus, PaymentMethod, InvoiceStatus


class PaymentInitiateRequest(BaseModel):
    entity_type: str = Field(..., description="Target domain code (e.g. AIRPORT_BOOKING, TICKET_BOOKING)")
    entity_id: str = Field(..., description="Target booking or order ID")
    customer_id: Optional[str] = None
    customer_name: str = Field(..., min_length=1)
    customer_email: EmailStr
    amount: float = Field(..., gt=0)
    currency: str = Field("INR", min_length=3, max_length=3)
    payment_method: PaymentMethod = PaymentMethod.CREDIT_CARD
    metadata: Optional[Dict[str, Any]] = None


class PaymentTransactionResponse(BaseModel):
    id: str
    transaction_ref: str
    entity_type: str
    entity_id: str
    customer_id: Optional[str] = None
    amount: float
    currency: str
    payment_method: str
    status: str
    gateway_provider: str
    gateway_payment_id: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class WebhookPayload(BaseModel):
    provider: str = Field("MOCK_PAYMENT")
    event_type: str = Field(..., description="e.g. payment.succeeded, payment.failed")
    transaction_ref: str
    gateway_payment_id: str
    signature: Optional[str] = None
    data: Optional[Dict[str, Any]] = None


class RefundRequest(BaseModel):
    transaction_id: str
    amount: float = Field(..., gt=0)
    reason: Optional[str] = None


class RefundResponse(BaseModel):
    id: str
    refund_ref: str
    transaction_id: str
    amount: float
    currency: str
    status: str
    reason: Optional[str] = None
    processed_at: datetime

    class Config:
        from_attributes = True


class InvoiceResponse(BaseModel):
    id: str
    invoice_number: str
    transaction_id: str
    customer_name: str
    customer_email: str
    subtotal_amount: float
    tax_amount: float
    total_amount: float
    currency: str
    status: str
    issued_at: datetime

    class Config:
        from_attributes = True


class PaymentVerifyRequest(BaseModel):
    razorpay_order_id: str = Field(..., description="Razorpay Order ID")
    razorpay_payment_id: str = Field(..., description="Razorpay Payment ID")
    razorpay_signature: str = Field(..., description="HMAC SHA256 Signature")
    booking_ref: Optional[str] = Field(None, description="Optional booking reference")


class PaymentRetryRequest(BaseModel):
    booking_ref: str = Field(..., description="Booking reference to retry payment for")


class PaymentLedgerFilterRequest(BaseModel):
    search: Optional[str] = None
    provider: Optional[str] = None
    status: Optional[str] = None


class PaymentCustomerHistoryRequest(BaseModel):
    userId: Optional[str] = None


class PaymentApiResponse(BaseModel):
    success: bool
    data: Optional[Any] = None
    error: Optional[str] = None


class RazorpayCreateOrderRequest(BaseModel):
    amount: float = Field(..., description="Amount in paise (minimum 100 paise = INR 1.00)")
    currency: str = Field("INR", description="Currency code (e.g. INR)")
    receipt: Optional[str] = Field(None, description="Receipt identifier")
    notes: Optional[Dict[str, Any]] = None


class RazorpayCreateOrderResponse(BaseModel):
    order_id: str
    amount: int
    currency: str
    key_id: Optional[str] = None
    receipt: Optional[str] = None


