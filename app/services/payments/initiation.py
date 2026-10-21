"""
Payment Initiation Service.
Handles transaction creation for Razorpay and ICICI payment gateways,
payload id extraction, URL and link validation, and server-side amount resolution.
"""

import os
import uuid
import logging
from typing import Dict, Any, Optional, Set
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from sqlalchemy import select, or_

from app.models.payment import (
    PaymentTransaction, PaymentStatus, PaymentMethod
)
from app.schemas.payment import PaymentInitiateRequest
from app.providers.base import PaymentProvider
from app.services.timeline_service import TimelineService
from app.services.admin_service import AdminService
from app.config import settings

logger = logging.getLogger(__name__)


def payment_link_expire_hours() -> int:
    """
    Payment Link lifetime. Default 24h to match expire_stale_transactions.
    Override with RAZORPAY_PAYMENT_LINK_EXPIRE_HOURS.
    """
    raw = (os.getenv("RAZORPAY_PAYMENT_LINK_EXPIRE_HOURS") or "").strip()
    if raw:
        try:
            return max(1, min(int(raw), 24 * 30))
        except ValueError:
            pass
    return 24


def is_https_payment_url(url: Optional[str]) -> bool:
    if not url or not isinstance(url, str):
        return False
    stripped = url.strip()
    lowered = stripped.lower()
    if not lowered.startswith("https://"):
        return False
    if any(bad in lowered for bad in ("localhost", "127.0.0.1", "0.0.0.0", "[::1]", "simulated")):
        return False
    return True


def is_simulated_payment_link(
    link_id: Optional[str] = None,
    url: Optional[str] = None,
    simulated_flag: bool = False,
) -> bool:
    if simulated_flag:
        return True
    lid = link_id or ""
    if lid.startswith("plink_sim_") or "plink_sim" in lid:
        return True
    if url and "simulated" in url.lower():
        return True
    return False


def extract_ids_from_razorpay_payload(raw_payload: Optional[Dict[str, Any]]) -> Dict[str, Optional[str]]:
    result: Dict[str, Optional[str]] = {
        "payment_link_id": None,
        "order_id": None,
        "payment_id": None,
        "booking_ref": None,
        "channel": None,
    }
    if not isinstance(raw_payload, dict):
        return result
    pl = raw_payload.get("payload") if isinstance(raw_payload.get("payload"), dict) else {}
    pay_ent = pl.get("payment", {}).get("entity", {}) if isinstance(pl.get("payment"), dict) else {}
    ord_ent = pl.get("order", {}).get("entity", {}) if isinstance(pl.get("order"), dict) else {}
    plink_ent = pl.get("payment_link", {}).get("entity", {}) if isinstance(pl.get("payment_link"), dict) else {}
    if not isinstance(pay_ent, dict):
        pay_ent = {}
    if not isinstance(ord_ent, dict):
        ord_ent = {}
    if not isinstance(plink_ent, dict):
        plink_ent = {}

    pay_id = pay_ent.get("id")
    if isinstance(pay_id, str) and pay_id.startswith("pay_"):
        result["payment_id"] = pay_id
    order_id = pay_ent.get("order_id") or ord_ent.get("id") or plink_ent.get("order_id")
    if order_id:
        result["order_id"] = str(order_id)
    plink_id = plink_ent.get("id")
    if isinstance(plink_id, str) and plink_id.startswith("plink_"):
        result["payment_link_id"] = plink_id

    notes = {}
    for source in (pay_ent.get("notes"), plink_ent.get("notes"), ord_ent.get("notes")):
        if isinstance(source, dict):
            notes.update(source)
    result["booking_ref"] = (
        notes.get("booking_ref")
        or plink_ent.get("reference_id")
        or ord_ent.get("receipt")
    )
    if result["booking_ref"]:
        result["booking_ref"] = str(result["booking_ref"])
    ch = notes.get("channel")
    if ch:
        result["channel"] = str(ch)
    return result


def collect_tx_gateway_ids(tx: Optional[PaymentTransaction]) -> Set[str]:
    ids = set()
    if not tx:
        return ids
    if tx.gateway_payment_id:
        ids.add(tx.gateway_payment_id)
    resp = tx.gateway_response if isinstance(tx.gateway_response, dict) else {}
    for key in ("payment_link_id", "order_id", "payment_id"):
        val = resp.get(key)
        if val:
            ids.add(str(val))
    payload = resp.get("payload") if isinstance(resp.get("payload"), dict) else {}
    for entity_key, id_key in (("payment_link", "id"), ("order", "id"), ("payment", "id")):
        ent = payload.get(entity_key, {})
        if isinstance(ent, dict):
            inner = ent.get("entity", ent)
            if isinstance(inner, dict) and inner.get(id_key):
                ids.add(str(inner.get(id_key)))
        pay_ent = payload.get("payment", {})
        if isinstance(pay_ent, dict):
            inner = pay_ent.get("entity", pay_ent)
            if isinstance(inner, dict) and inner.get("order_id"):
                ids.add(str(inner.get("order_id")))
    for prev in resp.get("previous_payment_links") or []:
        if isinstance(prev, dict) and prev.get("payment_link_id"):
            ids.add(str(prev["payment_link_id"]))
        elif isinstance(prev, str):
            ids.add(prev)
    return {i for i in ids if i}


def merge_gateway_response(
    existing: Optional[dict],
    *,
    raw_payload: Optional[Dict[str, Any]] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> dict:
    merged = dict(existing) if isinstance(existing, dict) else {}
    if extra:
        for k, v in extra.items():
            if v is not None:
                merged[k] = v
    extracted = extract_ids_from_razorpay_payload(raw_payload)
    for k, v in extracted.items():
        if v:
            merged[k] = v
    if isinstance(raw_payload, dict):
        if isinstance(raw_payload.get("payload"), dict):
            merged["payload"] = raw_payload.get("payload")
        if raw_payload.get("event"):
            merged["event"] = raw_payload.get("event")
    return merged


def unknown_create_result(result: Optional[Dict[str, Any]]) -> bool:
    err = str((result or {}).get("error") or "").lower()
    return any(token in err for token in ("timeout", "timed out", "network exception", "connect"))


def link_from_gateway_item(item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not isinstance(item, dict):
        return None
    status = str(item.get("status") or "").lower()
    if status in ("expired", "cancelled", "paid"):
        return None
    url = item.get("short_url")
    plink_id = item.get("id")
    if is_simulated_payment_link(plink_id, url) or not is_https_payment_url(url):
        return None
    expire_by = item.get("expire_by")
    if expire_by:
        try:
            if int(expire_by) <= int(datetime.now(timezone.utc).timestamp()):
                return None
        except (TypeError, ValueError):
            pass
    return {
        "payment_link_id": plink_id,
        "short_url": url,
        "order_id": item.get("order_id"),
        "expire_by": expire_by,
        "status": status or "created",
        "notes": item.get("notes") or {},
        "raw_response": item,
        "simulated": False,
        "success": True,
    }


def resolve_booking_amount(db: Session, payload: PaymentInitiateRequest):
    """Server-controlled amount. Never trust client-provided amount for bookings."""
    entity_type = payload.entity_type.upper()
    if entity_type not in ("BOOKING", "AIRPORT_BOOKING", "TICKET_BOOKING") or not payload.entity_id:
        raise ValueError("Payment amount must be resolved from a persisted booking.")

    from app.models.schema import Booking

    try:
        entity_uuid = uuid.UUID(payload.entity_id)
        booking = db.scalar(
            select(Booking).where(
                or_(Booking.id == entity_uuid, Booking.booking_ref == payload.entity_id)
            )
        )
    except ValueError:
        booking = db.scalar(select(Booking).where(Booking.booking_ref == payload.entity_id))

    if not booking or booking.total_amount is None:
        raise ValueError("Booking not found or has no authoritative amount.")
    return booking, float(booking.total_amount), booking.booking_ref


def initiate_razorpay_payment(
    cls,
    db: Session,
    payload: PaymentInitiateRequest,
) -> PaymentTransaction:
    from app.providers.razorpay_provider import razorpay_provider

    ref = f"PAY-{uuid.uuid4().hex[:8].upper()}"
    booking, authoritative_amount, booking_ref_for_order = cls._resolve_booking_amount(db, payload)

    intent = razorpay_provider.create_order(
        amount=authoritative_amount,
        currency=payload.currency,
        receipt=booking_ref_for_order or ref,
        notes={
            "transaction_ref": ref,
            "booking_ref": booking_ref_for_order or "",
            "customer_id": payload.customer_id if payload.customer_id else "",
            "channel": "web"
        }
    )
    if not intent.get("success") or not intent.get("order_id"):
        raise ValueError(intent.get("error") or "Razorpay order could not be created.")
    if str(intent.get("order_id", "")).startswith("order_sim_") and settings.is_production:
        raise ValueError("Payment gateway is not configured for live checkout.")
    order_id = intent.get("order_id")

    transaction = PaymentTransaction(
        transaction_ref=ref,
        entity_type=payload.entity_type.strip().upper(),
        entity_id=booking_ref_for_order or payload.entity_id,
        customer_id=payload.customer_id,
        amount=authoritative_amount,
        currency=payload.currency,
        payment_method=payload.payment_method,
        status=PaymentStatus.PENDING,
        gateway_provider="RAZORPAY",
        gateway_payment_id=order_id,
        gateway_response=intent
    )
    db.add(transaction)
    db.flush()

    if booking:
        b_meta = dict(booking.metadata_json or {})
        b_meta["payment_gateway"] = "RAZORPAY"
        b_meta["channel"] = b_meta.get("channel") or "web"
        b_meta["order_id"] = order_id
        b_meta["razorpay_order_id"] = order_id
        b_meta["transaction_id"] = ref
        b_meta["payment_status"] = "PENDING"
        booking.metadata_json = b_meta
        booking.updated_at = datetime.now(timezone.utc)

    TimelineService.add_entry(
        db,
        entity_type=payload.entity_type,
        entity_id=booking_ref_for_order or payload.entity_id,
        event_type="PAYMENT_INITIATED",
        title=f"Payment Initiated ({ref})",
        details={
            "amount": authoritative_amount,
            "currency": payload.currency,
            "transactionRef": ref,
            "gatewayOrderId": order_id,
            "gateway": "RAZORPAY",
        }
    )

    AdminService.log_audit_action(
        db,
        actor_email=payload.customer_email,
        action="PAYMENT_INITIATED",
        resource_type="PAYMENT",
        resource_id=str(transaction.id),
        details={"transactionRef": ref, "amount": authoritative_amount, "gateway": "RAZORPAY"}
    )

    db.commit()
    db.refresh(transaction)
    return transaction


def initiate_icici_payment(
    cls,
    db: Session,
    payload: PaymentInitiateRequest,
) -> PaymentTransaction:
    from app.providers.icici_provider import (
        generate_merchant_txn_no,
        icici_provider,
    )

    ref = f"PAY-{uuid.uuid4().hex[:8].upper()}"
    booking, authoritative_amount, booking_ref_for_order = cls._resolve_booking_amount(db, payload)
    merchant_txn_no = generate_merchant_txn_no("SF")

    request_body = icici_provider.build_initiate_sale_request(
        merchant_txn_no=merchant_txn_no,
        amount=authoritative_amount,
        customer_email=payload.customer_email,
        customer_mobile=getattr(booking, "passenger_phone", None) or "",
        customer_name=payload.customer_name,
        addl_param1=booking_ref_for_order or "",
        addl_param2=ref,
    )
    # Never persist ICICI_KEY; strip secureHash from stored request copy
    stored_request = {k: v for k, v in request_body.items() if k.lower() != "securehash"}

    intent = icici_provider.initiate_sale(request_body)
    if not intent.get("success") or not intent.get("payment_url"):
        raise ValueError(intent.get("error") or "ICICI initiateSale could not be completed.")

    gateway_response = {
        "gateway": "ICICI",
        "environment": getattr(settings, "ICICI_ENV", "UAT"),
        "merchantTxnNo": merchant_txn_no,
        "request": stored_request,
        "responseCode": intent.get("responseCode"),
        "redirectURI": intent.get("redirectURI"),
        "tranCtx": intent.get("tranCtx"),
        "payment_url": intent.get("payment_url"),
        "amount": stored_request.get("amount"),
        "sanitized_response": intent.get("raw"),
    }

    transaction = PaymentTransaction(
        transaction_ref=ref,
        entity_type=payload.entity_type.strip().upper(),
        entity_id=booking_ref_for_order or payload.entity_id,
        customer_id=payload.customer_id,
        amount=authoritative_amount,
        currency=payload.currency,
        payment_method=payload.payment_method,
        status=PaymentStatus.PENDING,
        gateway_provider="ICICI",
        gateway_payment_id=merchant_txn_no,
        gateway_response=gateway_response,
    )
    db.add(transaction)
    db.flush()

    if booking:
        b_meta = dict(booking.metadata_json or {})
        b_meta["payment_gateway"] = "ICICI"
        b_meta["channel"] = b_meta.get("channel") or "web"
        b_meta["order_id"] = merchant_txn_no
        b_meta["merchant_txn_no"] = merchant_txn_no
        b_meta["transaction_id"] = ref
        b_meta["payment_status"] = "PENDING"
        booking.metadata_json = b_meta
        booking.updated_at = datetime.now(timezone.utc)

    TimelineService.add_entry(
        db,
        entity_type=payload.entity_type,
        entity_id=booking_ref_for_order or payload.entity_id,
        event_type="PAYMENT_INITIATED",
        title=f"Payment Initiated ({ref})",
        details={
            "amount": authoritative_amount,
            "currency": payload.currency,
            "transactionRef": ref,
            "merchantTxnNo": merchant_txn_no,
            "gateway": "ICICI",
        }
    )

    AdminService.log_audit_action(
        db,
        actor_email=payload.customer_email,
        action="PAYMENT_INITIATED",
        resource_type="PAYMENT",
        resource_id=str(transaction.id),
        details={
            "transactionRef": ref,
            "amount": authoritative_amount,
            "gateway": "ICICI",
            "merchantTxnNo": merchant_txn_no,
        }
    )

    db.commit()
    db.refresh(transaction)
    return transaction


def initiate_payment(
    cls,
    db: Session,
    payload: PaymentInitiateRequest,
    provider: Optional[PaymentProvider] = None
) -> PaymentTransaction:
    """Initiates a payment transaction with the requested/active gateway (Razorpay as primary or ICICI)."""
    from app.models.schema import Booking

    preferred = getattr(payload, "preferred_gateway", None)
    if not preferred and payload.metadata:
        preferred = payload.metadata.get("preferred_gateway") or payload.metadata.get("payment_gateway")
    if not preferred and provider:
        preferred = provider.value if hasattr(provider, "value") else str(provider)

    gateway = cls.active_payment_gateway(preferred=preferred)
    booking = db.scalar(select(Booking).where(Booking.booking_ref == payload.entity_id))
    if booking:
        cls.assert_gateway_exclusive(db, booking, gateway)
    if gateway == "ICICI":
        return cls._initiate_icici_payment(db, payload)
    return cls._initiate_razorpay_payment(db, payload)
