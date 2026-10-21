"""
Payments Module Package.
Exposes modular payment domain services (gateway selection, initiation, WhatsApp links,
verification, ICICI handling, invoicing & refunds, reconciliation & ledger).
"""

from app.services.payments.gateway_selector import (
    active_payment_gateway,
    public_checkout_payload,
    frozen_gateway_for_booking,
    assert_gateway_exclusive,
    sanitize_gateway_response,
)
from app.services.payments.initiation import (
    initiate_payment,
    resolve_booking_amount,
    initiate_razorpay_payment,
    initiate_icici_payment,
    payment_link_expire_hours,
    is_https_payment_url,
    is_simulated_payment_link,
    extract_ids_from_razorpay_payload,
    collect_tx_gateway_ids,
    merge_gateway_response,
    unknown_create_result,
    link_from_gateway_item,
)
from app.services.payments.whatsapp_links import (
    initiate_whatsapp_payment_link,
    replace_whatsapp_payment_link,
    reconcile_whatsapp_payment_link,
    mark_payment_link_terminal,
    recover_gateway_link_by_reference,
    find_reusable_whatsapp_payment_link,
    persist_whatsapp_payment_link_tx,
)
from app.services.payments.verification_helpers import (
    validate_amount_and_currency,
    dispatch_payment_fulfillment_side_effects,
    sync_whatsapp_conversation_on_payment,
    handle_payment_authorized,
    handle_payment_link_expired_cancelled,
    handle_payment_failed,
    handle_payment_refund,
)
from app.services.payments.verification import (
    verify_internal_webhook_signature,
    confirmed_booking_payment_result,
    handle_verified_payment,
)
from app.services.payments.icici_handler import (
    retry_booking_payment,
    retry_icici_payment,
    handle_icici_browser_cancel,
    handle_icici_browser_back,
    process_icici_callback,
)
from app.services.payments.invoicing_refunds import (
    process_webhook,
    generate_invoice,
    process_refund,
)
from app.services.payments.reconciliation import (
    get_reconciliation_report,
    reconcile_booking_payment,
    expire_stale_transactions,
    get_payment_ledger,
    get_customer_payment_history,
)

__all__ = [
    "active_payment_gateway",
    "public_checkout_payload",
    "frozen_gateway_for_booking",
    "assert_gateway_exclusive",
    "sanitize_gateway_response",
    "initiate_payment",
    "resolve_booking_amount",
    "initiate_razorpay_payment",
    "initiate_icici_payment",
    "payment_link_expire_hours",
    "is_https_payment_url",
    "is_simulated_payment_link",
    "extract_ids_from_razorpay_payload",
    "collect_tx_gateway_ids",
    "merge_gateway_response",
    "unknown_create_result",
    "link_from_gateway_item",
    "initiate_whatsapp_payment_link",
    "replace_whatsapp_payment_link",
    "reconcile_whatsapp_payment_link",
    "mark_payment_link_terminal",
    "recover_gateway_link_by_reference",
    "find_reusable_whatsapp_payment_link",
    "persist_whatsapp_payment_link_tx",
    "validate_amount_and_currency",
    "dispatch_payment_fulfillment_side_effects",
    "sync_whatsapp_conversation_on_payment",
    "handle_payment_authorized",
    "handle_payment_link_expired_cancelled",
    "handle_payment_failed",
    "handle_payment_refund",
    "verify_internal_webhook_signature",
    "confirmed_booking_payment_result",
    "handle_verified_payment",
    "retry_booking_payment",
    "retry_icici_payment",
    "handle_icici_browser_cancel",
    "handle_icici_browser_back",
    "process_icici_callback",
    "process_webhook",
    "generate_invoice",
    "process_refund",
    "get_reconciliation_report",
    "reconcile_booking_payment",
    "expire_stale_transactions",
    "get_payment_ledger",
    "get_customer_payment_history",
]
