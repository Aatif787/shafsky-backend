"""
Payment & Invoicing Service Layer (Unified Facade).
Preserves 100% backward compatibility for all external callers and test patch fixtures.
Modular domain sub-services are located in app.services.payments.
"""

import logging
from typing import Dict, Any, Optional, List, Set
from sqlalchemy.orm import Session

from app.models.payment import (
    PaymentTransaction, Invoice, Refund, PaymentStatus,
)
from app.schemas.payment import PaymentInitiateRequest, RefundRequest, WebhookPayload
from app.providers.base import PaymentProvider

import app.services.payments.gateway_selector as _gw
import app.services.payments.initiation as _init
import app.services.payments.whatsapp_links as _wa
import app.services.payments.verification as _ver
import app.services.payments.icici_handler as _icici
import app.services.payments.invoicing_refunds as _inv
import app.services.payments.reconciliation as _rec

logger = logging.getLogger(__name__)


class PaymentService:
    """
    Core payment domain service facade.
    Delegates to specialized sub-modules in app.services.payments with zero logic change.
    """

    # -------------------------------------------------------------------------
    # 1. Gateway Selection & Exclusivity
    # -------------------------------------------------------------------------

    @classmethod
    def active_payment_gateway(cls, preferred: Optional[str] = None) -> str:
        """RAZORPAY (primary/default) or ICICI when selected and credentials are present."""
        return _gw.active_payment_gateway(preferred=preferred)

    @classmethod
    def _public_checkout_payload(
        cls,
        *,
        booking_ref: str,
        gateway: str,
        transaction_ref: Optional[str] = None,
        total_amount: Optional[float] = None,
        currency: str = "INR",
        razorpay_order_id: Optional[str] = None,
        razorpay_key_id: Optional[str] = None,
        razorpay_amount_paise: Optional[int] = None,
        icici_redirect_url: Optional[str] = None,
        icici_merchant_txn_no: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Checkout fields only – never passenger PII or raw gateway payloads."""
        return _gw.public_checkout_payload(
            booking_ref=booking_ref,
            gateway=gateway,
            transaction_ref=transaction_ref,
            total_amount=total_amount,
            currency=currency,
            razorpay_order_id=razorpay_order_id,
            razorpay_key_id=razorpay_key_id,
            razorpay_amount_paise=razorpay_amount_paise,
            icici_redirect_url=icici_redirect_url,
            icici_merchant_txn_no=icici_merchant_txn_no,
        )

    @classmethod
    def frozen_gateway_for_booking(cls, db: Session, booking) -> Optional[str]:
        """Return gateway already bound to an open/paid session for this booking, if any."""
        return _gw.frozen_gateway_for_booking(db, booking)

    @classmethod
    def assert_gateway_exclusive(cls, db: Session, booking, requested_gateway: str) -> None:
        """Reject dual Razorpay+ICICI sessions for new initiates on the same pending booking."""
        return _gw.assert_gateway_exclusive(db, booking, requested_gateway)

    @classmethod
    def sanitize_gateway_response(cls, gateway_response: Any) -> Optional[Dict[str, Any]]:
        """Strip secrets and bulky raw payloads before returning to API clients."""
        return _gw.sanitize_gateway_response(gateway_response)

    # -------------------------------------------------------------------------
    # 2. Payment Initiation & Helpers
    # -------------------------------------------------------------------------

    @classmethod
    def initiate_payment(
        cls,
        db: Session,
        payload: PaymentInitiateRequest,
        provider: Optional[PaymentProvider] = None
    ) -> PaymentTransaction:
        """Initiates a payment transaction with the requested/active gateway (Razorpay as primary or ICICI)."""
        return _init.initiate_payment(cls, db, payload, provider=provider)

    @classmethod
    def _resolve_booking_amount(cls, db: Session, payload: PaymentInitiateRequest):
        """Server-controlled amount. Never trust client-provided amount for bookings."""
        return _init.resolve_booking_amount(db, payload)

    @classmethod
    def _initiate_razorpay_payment(
        cls,
        db: Session,
        payload: PaymentInitiateRequest,
    ) -> PaymentTransaction:
        return _init.initiate_razorpay_payment(cls, db, payload)

    @classmethod
    def _initiate_icici_payment(
        cls,
        db: Session,
        payload: PaymentInitiateRequest,
    ) -> PaymentTransaction:
        return _init.initiate_icici_payment(cls, db, payload)

    @classmethod
    def _payment_link_expire_hours(cls) -> int:
        return _init.payment_link_expire_hours()

    @classmethod
    def _is_https_payment_url(cls, url: Optional[str]) -> bool:
        return _init.is_https_payment_url(url)

    @classmethod
    def _is_simulated_payment_link(
        cls,
        link_id: Optional[str] = None,
        url: Optional[str] = None,
        simulated_flag: bool = False,
    ) -> bool:
        return _init.is_simulated_payment_link(link_id=link_id, url=url, simulated_flag=simulated_flag)

    @classmethod
    def _extract_ids_from_razorpay_payload(cls, raw_payload: Optional[Dict[str, Any]]) -> Dict[str, Optional[str]]:
        return _init.extract_ids_from_razorpay_payload(raw_payload)

    @classmethod
    def _collect_tx_gateway_ids(cls, tx: Optional[PaymentTransaction]) -> Set[str]:
        return _init.collect_tx_gateway_ids(tx)

    @classmethod
    def _merge_gateway_response(
        cls,
        existing: Optional[dict],
        *,
        raw_payload: Optional[Dict[str, Any]] = None,
        extra: Optional[Dict[str, Any]] = None,
    ) -> dict:
        return _init.merge_gateway_response(existing, raw_payload=raw_payload, extra=extra)

    @classmethod
    def _unknown_create_result(cls, result: Optional[Dict[str, Any]]) -> bool:
        return _init.unknown_create_result(result)

    @classmethod
    def _link_from_gateway_item(cls, item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        return _init.link_from_gateway_item(item)

    # -------------------------------------------------------------------------
    # 3. WhatsApp Payment Links
    # -------------------------------------------------------------------------

    @classmethod
    def initiate_whatsapp_payment_link(cls, db: Session, booking_ref: str) -> Dict[str, Any]:
        """Create or reuse a Razorpay Payment Link for a PENDING WhatsApp booking."""
        return _wa.initiate_whatsapp_payment_link(cls, db, booking_ref)

    @classmethod
    def _recover_gateway_link_by_reference(cls, booking_ref: str) -> Optional[Dict[str, Any]]:
        return _wa.recover_gateway_link_by_reference(booking_ref)

    @classmethod
    def _find_reusable_whatsapp_payment_link(cls, db: Session, booking_ref: str) -> Optional[Dict[str, Any]]:
        return _wa.find_reusable_whatsapp_payment_link(db, booking_ref)

    @classmethod
    def _mark_payment_link_terminal(cls, tx: PaymentTransaction, status: PaymentStatus, link_status: str) -> None:
        return _wa.mark_payment_link_terminal(tx, status, link_status)

    @classmethod
    def _persist_whatsapp_payment_link_tx(
        cls,
        db: Session,
        *,
        booking,
        link: Dict[str, Any],
        previous_link_id: Optional[str],
    ) -> PaymentTransaction:
        return _wa.persist_whatsapp_payment_link_tx(
            cls, db, booking=booking, link=link, previous_link_id=previous_link_id
        )

    @classmethod
    def replace_whatsapp_payment_link(cls, db: Session, booking_ref: str) -> Dict[str, Any]:
        """Force a new Payment Link for a still-PENDING booking after expiry/cancellation."""
        return _wa.replace_whatsapp_payment_link(cls, db, booking_ref)

    @classmethod
    def reconcile_whatsapp_payment_link(cls, db: Session, booking_ref: str) -> Dict[str, Any]:
        """Fetch live Payment Link status and reconcile against Razorpay."""
        return _wa.reconcile_whatsapp_payment_link(cls, db, booking_ref)

    # -------------------------------------------------------------------------
    # 4. Verification & Confirmation Orchestration
    # -------------------------------------------------------------------------

    @classmethod
    def verify_internal_webhook_signature(cls, payload: WebhookPayload, signature: Optional[str]) -> bool:
        return _ver.verify_internal_webhook_signature(payload, signature)

    @classmethod
    def _confirmed_booking_payment_result(
        cls,
        db: Session,
        *,
        booking,
        transaction: Optional[PaymentTransaction],
        payment_id: Optional[str],
        order_id: Optional[str],
        payment_link_id: Optional[str],
        resolved_booking_ref: Optional[str],
        gateway_provider: str,
        signature: Optional[str],
        raw_payload: Optional[Dict[str, Any]],
    ) -> Optional[Dict[str, Any]]:
        return _ver.confirmed_booking_payment_result(
            cls,
            db,
            booking=booking,
            transaction=transaction,
            payment_id=payment_id,
            order_id=order_id,
            payment_link_id=payment_link_id,
            resolved_booking_ref=resolved_booking_ref,
            gateway_provider=gateway_provider,
            signature=signature,
            raw_payload=raw_payload,
        )

    @classmethod
    def handle_verified_payment(
        cls,
        db: Session,
        *,
        event_name: str,
        gateway_provider: str = "RAZORPAY",
        order_id: Optional[str] = None,
        payment_id: Optional[str] = None,
        booking_ref: Optional[str] = None,
        signature: Optional[str] = None,
        raw_payload: Optional[Dict[str, Any]] = None,
        channel: Optional[str] = None,
        amount: Optional[float] = None,
        currency: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Canonical, idempotent processor for all verified payment events."""
        return _ver.handle_verified_payment(
            cls,
            db,
            event_name=event_name,
            gateway_provider=gateway_provider,
            order_id=order_id,
            payment_id=payment_id,
            booking_ref=booking_ref,
            signature=signature,
            raw_payload=raw_payload,
            channel=channel,
            amount=amount,
            currency=currency,
        )

    # -------------------------------------------------------------------------
    # 5. ICICI Handling & Retry Flow
    # -------------------------------------------------------------------------

    @classmethod
    def retry_booking_payment(
        cls,
        db: Session,
        booking_ref: str,
        customer_email: Optional[str] = None,
        user_id: Optional[str] = None,
        preferred_gateway: Optional[str] = None,
    ) -> Dict[str, Any]:
        return _icici.retry_booking_payment(
            cls,
            db,
            booking_ref,
            customer_email=customer_email,
            user_id=user_id,
            preferred_gateway=preferred_gateway,
        )

    @classmethod
    def _retry_icici_payment(cls, db: Session, booking, user_id: Optional[str] = None) -> Dict[str, Any]:
        return _icici.retry_icici_payment(cls, db, booking, user_id=user_id)

    @classmethod
    def handle_icici_browser_cancel(
        cls,
        db: Session,
        query_params: Dict[str, Any],
    ) -> Dict[str, Any]:
        return _icici.handle_icici_browser_cancel(cls, db, query_params)

    @classmethod
    def handle_icici_browser_back(
        cls,
        db: Session,
        query_params: Dict[str, Any],
    ) -> Dict[str, Any]:
        return _icici.handle_icici_browser_back(cls, db, query_params)

    @classmethod
    def process_icici_callback(cls, db: Session, raw_params: Dict[str, Any]) -> Dict[str, Any]:
        return _icici.process_icici_callback(cls, db, raw_params)

    # -------------------------------------------------------------------------
    # 6. Invoicing & Refunds
    # -------------------------------------------------------------------------

    @classmethod
    def process_webhook(
        cls,
        db: Session,
        payload: WebhookPayload,
        provider: Optional[PaymentProvider] = None
    ) -> PaymentTransaction:
        return _inv.process_webhook(cls, db, payload, provider=provider)

    @classmethod
    def generate_invoice(
        cls,
        db: Session,
        transaction: PaymentTransaction,
        customer_name: str,
        customer_email: str
    ) -> Optional[Invoice]:
        return _inv.generate_invoice(
            cls, db, transaction, customer_name, customer_email
        )

    @classmethod
    def process_refund(
        cls,
        db: Session,
        payload: RefundRequest,
        actor_email: str = "admin@shafsky.com",
        provider: Optional[PaymentProvider] = None
    ) -> Refund:
        return _inv.process_refund(
            cls, db, payload, actor_email=actor_email, provider=provider
        )

    # -------------------------------------------------------------------------
    # 7. Reconciliation & Ledger
    # -------------------------------------------------------------------------

    @classmethod
    def get_reconciliation_report(cls, db: Session) -> Dict[str, Any]:
        return _rec.get_reconciliation_report(cls, db)

    @classmethod
    def reconcile_booking_payment(cls, db: Session, booking_ref: str) -> Dict[str, Any]:
        return _rec.reconcile_booking_payment(cls, db, booking_ref)

    @classmethod
    def expire_stale_transactions(cls, db: Session, max_age_hours: float = 24.0) -> int:
        return _rec.expire_stale_transactions(cls, db, max_age_hours=max_age_hours)

    @classmethod
    def get_payment_ledger(
        cls,
        db: Session,
        search: Optional[str] = None,
        provider: Optional[str] = None,
        status: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        return _rec.get_payment_ledger(
            cls, db, search=search, provider=provider, status=status
        )

    @classmethod
    def get_customer_payment_history(
        cls,
        db: Session,
        user_id: Optional[str] = None,
        email: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        return _rec.get_customer_payment_history(
            cls, db, user_id=user_id, email=email
        )
