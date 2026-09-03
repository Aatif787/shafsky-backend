"""
Payment & Invoicing Service Layer.
Encapsulates transaction initiation, invoice generation, webhook processing,
refund handling, timeline tracking, and audit logging.
"""

import uuid
import logging
from typing import Dict, Any, Optional, List
from decimal import Decimal
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from sqlalchemy import select, desc, or_
from sqlalchemy.exc import IntegrityError

logger = logging.getLogger(__name__)

from app.models.payment import (
    PaymentTransaction, Invoice, Refund, PaymentStatus, InvoiceStatus, PaymentMethod, PaymentStateMachine,
)
from app.schemas.payment import PaymentInitiateRequest, RefundRequest, WebhookPayload
from app.providers.base import PaymentProvider, MockPaymentProvider
from app.services.timeline_service import TimelineService
from app.services.admin_service import AdminService
from app.config import settings
import hmac
import hashlib
import os


class PaymentService:
    """Core payment domain service."""

    @classmethod
    def initiate_payment(
        cls,
        db: Session,
        payload: PaymentInitiateRequest,
        provider: Optional[PaymentProvider] = None
    ) -> PaymentTransaction:
        """Initiates a payment transaction and registers intent with Razorpay."""
        from app.providers.razorpay_provider import razorpay_provider

        ref = f"PAY-{uuid.uuid4().hex[:8].upper()}"

        # Server-controlled amount. Never trust client-provided amount for bookings.
        authoritative_amount = None
        booking_ref_for_order = None
        entity_type = str(payload.entity_type).upper()
        if entity_type in ("BOOKING", "AIRPORT_BOOKING", "TICKET_BOOKING") and payload.entity_id:
            from app.models.schema import Booking
            from sqlalchemy import or_
            try:
                entity_uuid = uuid.UUID(str(payload.entity_id))
                booking = db.scalar(select(Booking).where(or_(Booking.id == entity_uuid, Booking.booking_ref == str(payload.entity_id))))
            except ValueError:
                booking = db.scalar(select(Booking).where(Booking.booking_ref == str(payload.entity_id)))

            if not booking or booking.total_amount is None:
                raise ValueError("Booking not found or has no authoritative amount.")
            authoritative_amount = float(booking.total_amount)
            booking_ref_for_order = booking.booking_ref
        else:
            raise ValueError("Payment amount must be resolved from a persisted booking.")

        # Create Razorpay Order
        intent = razorpay_provider.create_order(
            amount=authoritative_amount,
            currency=payload.currency,
            receipt=booking_ref_for_order or ref,
            notes={
                "transaction_ref": ref,
                "booking_ref": booking_ref_for_order or "",
                "customer_id": str(payload.customer_id) if payload.customer_id else "",
                "channel": "web"
            }
        )
        if not intent.get("success") or not intent.get("order_id"):
            raise ValueError(intent.get("error") or "Razorpay order could not be created.")
        if str(intent.get("order_id", "")).startswith("order_sim_"):
            raise ValueError("Payment gateway is not configured for live checkout.")
        order_id = intent.get("order_id")

        transaction = PaymentTransaction(
            transaction_ref=ref,
            entity_type=payload.entity_type.strip().upper(),
            entity_id=str(payload.entity_id),
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

        # Log timeline event
        TimelineService.add_entry(
            db,
            entity_type=payload.entity_type,
            entity_id=payload.entity_id,
            event_type="PAYMENT_INITIATED",
            title=f"Payment Initiated ({ref})",
            details={
                "amount": authoritative_amount,
                "currency": payload.currency,
                "transactionRef": ref,
                "gatewayOrderId": order_id
            }
        )

        # Audit log
        AdminService.log_audit_action(
            db,
            actor_email=payload.customer_email,
            action="PAYMENT_INITIATED",
            resource_type="PAYMENT",
            resource_id=str(transaction.id),
            details={"transactionRef": ref, "amount": authoritative_amount}
        )

        db.commit()
        db.refresh(transaction)
        return transaction

    @classmethod
    def _payment_link_expire_hours(cls) -> int:
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

    @classmethod
    def _is_https_payment_url(cls, url: Optional[str]) -> bool:
        if not url or not isinstance(url, str):
            return False
        stripped = url.strip()
        lowered = stripped.lower()
        if not lowered.startswith("https://"):
            return False
        if any(bad in lowered for bad in ("localhost", "127.0.0.1", "0.0.0.0", "[::1]", "simulated")):
            return False
        return True

    @classmethod
    def _is_simulated_payment_link(
        cls,
        link_id: Optional[str] = None,
        url: Optional[str] = None,
        simulated_flag: bool = False,
    ) -> bool:
        if simulated_flag:
            return True
        lid = str(link_id or "")
        if lid.startswith("plink_sim_") or "plink_sim" in lid:
            return True
        if url and "simulated" in str(url).lower():
            return True
        return False

    @classmethod
    def _extract_ids_from_razorpay_payload(cls, raw_payload: Optional[Dict[str, Any]]) -> Dict[str, Optional[str]]:
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

    @classmethod
    def _collect_tx_gateway_ids(cls, tx: Optional[PaymentTransaction]) -> set:
        ids = set()
        if not tx:
            return ids
        if tx.gateway_payment_id:
            ids.add(str(tx.gateway_payment_id))
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

    @classmethod
    def _merge_gateway_response(
        cls,
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
        extracted = cls._extract_ids_from_razorpay_payload(raw_payload)
        for k, v in extracted.items():
            if v:
                merged[k] = v
        if isinstance(raw_payload, dict):
            if isinstance(raw_payload.get("payload"), dict):
                merged["payload"] = raw_payload.get("payload")
            if raw_payload.get("event"):
                merged["event"] = raw_payload.get("event")
        return merged

    @classmethod
    def _unknown_create_result(cls, result: Optional[Dict[str, Any]]) -> bool:
        err = str((result or {}).get("error") or "").lower()
        return any(token in err for token in ("timeout", "timed out", "network exception", "connect"))

    @classmethod
    def _link_from_gateway_item(cls, item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if not isinstance(item, dict):
            return None
        status = str(item.get("status") or "").lower()
        if status in ("expired", "cancelled", "paid"):
            return None
        url = item.get("short_url")
        plink_id = item.get("id")
        if cls._is_simulated_payment_link(plink_id, url) or not cls._is_https_payment_url(url):
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

    @classmethod
    def initiate_whatsapp_payment_link(cls, db: Session, booking_ref: str) -> Dict[str, Any]:
        """
        Create or reuse a Razorpay Payment Link for a PENDING WhatsApp booking.
        Always persists/reuses a local PaymentTransaction BEFORE the customer is sent a URL.
        Never confirms the booking. Never sends simulated/localhost/http URLs.
        """
        from app.models.schema import Booking, BookingStatus
        from app.providers.razorpay_provider import razorpay_provider

        booking = db.scalar(select(Booking).where(Booking.booking_ref == booking_ref))
        if not booking:
            return {"success": False, "error": "BOOKING_NOT_FOUND", "reason": f"Booking '{booking_ref}' not found."}
        if booking.status == BookingStatus.CONFIRMED:
            return {"success": False, "error": "ALREADY_CONFIRMED", "reason": "Booking is already confirmed."}
        if booking.status in (BookingStatus.CANCELLED, BookingStatus.REJECTED):
            return {"success": False, "error": "BOOKING_NOT_PAYABLE", "reason": "Booking cannot accept payment."}
        if booking.total_amount is None:
            return {"success": False, "error": "NO_AMOUNT", "reason": "Booking has no authoritative amount."}

        from app.services.booking_cutoff import evaluate_cutoff_for_booking
        # WhatsApp 'flight not confirmed yet' bookings carry no scheduled times:
        # the ops team confirms the flight with the customer, so the flight-time
        # cutoff cannot apply. Flagged via metadata (flight_later).
        if not (booking.metadata_json or {}).get("flight_later", False):
            cutoff = evaluate_cutoff_for_booking(db, booking)
            if not cutoff.allowed:
                return {
                    "success": False,
                    "error": "BOOKING_CUTOFF",
                    "reason": cutoff.reason,
                    "customer_message": cutoff.customer_message,
                }

        authoritative_amount = float(booking.total_amount)
        currency = (booking.currency or "INR").upper()

        reusable = cls._find_reusable_whatsapp_payment_link(db, booking_ref)
        if reusable:
            return reusable

        expire_hours = cls._payment_link_expire_hours()
        expire_by = int((datetime.now(timezone.utc).timestamp())) + int(expire_hours * 3600)

        recovered = cls._recover_gateway_link_by_reference(booking.booking_ref)
        if recovered:
            tx = cls._persist_whatsapp_payment_link_tx(
                db,
                booking=booking,
                link=recovered,
                previous_link_id=None,
            )
            return {
                "success": True,
                "reused": True,
                "recovered": True,
                "payment_link_id": recovered["payment_link_id"],
                "short_url": recovered["short_url"],
                "transaction_ref": tx.transaction_ref,
                "expire_by": recovered.get("expire_by") or expire_by,
            }

        intent = razorpay_provider.create_payment_link(
            amount=authoritative_amount,
            currency=currency,
            reference_id=booking.booking_ref,
            booking_ref=booking.booking_ref,
            description=f"Shafsky Aviation booking {booking.booking_ref}",
            customer_name=booking.passenger_name or "Guest",
            customer_email=booking.passenger_email or "",
            customer_phone=booking.passenger_phone or "",
            expire_by=expire_by,
            channel="whatsapp",
            notes={"booking_ref": booking.booking_ref, "channel": "whatsapp"},
        )

        if (not intent.get("success")) and cls._unknown_create_result(intent):
            recovered = cls._recover_gateway_link_by_reference(booking.booking_ref)
            if recovered:
                intent = recovered
            else:
                return {
                    "success": False,
                    "error": "LINK_CREATE_UNKNOWN",
                    "reason": "Payment link creation timed out. Please try again in a moment.",
                }

        if not intent.get("success") and "reference" in str(intent.get("error") or "").lower():
            recovered = cls._recover_gateway_link_by_reference(booking.booking_ref)
            if recovered:
                intent = recovered

        if not intent.get("success"):
            return {
                "success": False,
                "error": "LINK_CREATE_FAILED",
                "reason": intent.get("error") or "Payment link could not be created.",
            }

        plink_id = intent.get("payment_link_id")
        short_url = intent.get("short_url")
        if cls._is_simulated_payment_link(plink_id, short_url, bool(intent.get("simulated"))):
            logger.warning("[PaymentService] Simulated Payment Link rejected for customer delivery (booking %s)", booking_ref)
            return {
                "success": False,
                "error": "SIMULATED_LINK_REJECTED",
                "reason": "Payment gateway is not configured for live Payment Links.",
            }
        if not cls._is_https_payment_url(short_url):
            return {
                "success": False,
                "error": "UNDELIVERABLE_URL",
                "reason": "Payment link URL is not a secure HTTPS Razorpay URL.",
            }

        tx = cls._persist_whatsapp_payment_link_tx(
            db,
            booking=booking,
            link={
                "payment_link_id": plink_id,
                "short_url": short_url,
                "order_id": intent.get("order_id"),
                "expire_by": intent.get("expire_by") or expire_by,
                "status": intent.get("status") or "created",
                "notes": intent.get("notes") or {"booking_ref": booking.booking_ref, "channel": "whatsapp"},
                "raw_response": intent.get("raw_response") or intent,
                "simulated": False,
            },
            previous_link_id=None,
        )
        return {
            "success": True,
            "reused": False,
            "payment_link_id": plink_id,
            "short_url": short_url,
            "transaction_ref": tx.transaction_ref,
            "expire_by": intent.get("expire_by") or expire_by,
        }

    @classmethod
    def _recover_gateway_link_by_reference(cls, booking_ref: str) -> Optional[Dict[str, Any]]:
        from app.providers.razorpay_provider import razorpay_provider

        listed = razorpay_provider.list_payment_links_by_reference(booking_ref)
        items = listed.get("items") if listed.get("success") else []
        for item in items or []:
            recovered = cls._link_from_gateway_item(item)
            if recovered:
                return recovered
        return None

    @classmethod
    def _find_reusable_whatsapp_payment_link(cls, db: Session, booking_ref: str) -> Optional[Dict[str, Any]]:
        now_ts = int(datetime.now(timezone.utc).timestamp())
        txs = list(
            db.scalars(
                select(PaymentTransaction)
                .where(
                    PaymentTransaction.entity_id == booking_ref,
                    PaymentTransaction.gateway_provider == "RAZORPAY",
                    PaymentTransaction.status.in_([PaymentStatus.PENDING, PaymentStatus.PROCESSING]),
                    PaymentTransaction.is_duplicate.isnot(True),
                )
                .order_by(desc(PaymentTransaction.created_at))
            ).all()
        )
        for tx in txs:
            resp = tx.gateway_response if isinstance(tx.gateway_response, dict) else {}
            plink_id = resp.get("payment_link_id")
            if not plink_id and str(tx.gateway_payment_id or "").startswith("plink_"):
                plink_id = tx.gateway_payment_id
            url = resp.get("short_url")
            link_status = str(resp.get("link_status") or "created").lower()
            expire_by = resp.get("expire_by")
            if link_status in ("expired", "cancelled", "paid"):
                continue
            expired_locally = False
            if expire_by:
                try:
                    expired_locally = int(expire_by) <= now_ts
                except (TypeError, ValueError):
                    expired_locally = False
            if expired_locally:
                cls._mark_payment_link_terminal(tx, PaymentStatus.EXPIRED, "expired")
                continue
            if cls._is_simulated_payment_link(plink_id, url) or not cls._is_https_payment_url(url):
                continue
            return {
                "success": True,
                "reused": True,
                "payment_link_id": plink_id,
                "short_url": url,
                "transaction_ref": tx.transaction_ref,
                "expire_by": expire_by,
            }
        return None

    @classmethod
    def _mark_payment_link_terminal(cls, tx: PaymentTransaction, status: PaymentStatus, link_status: str) -> None:
        if tx.status == PaymentStatus.SUCCESSFUL:
            return
        if not PaymentStateMachine.can_transition(tx.status, status):
            return
        tx.status = status
        resp = dict(tx.gateway_response) if isinstance(tx.gateway_response, dict) else {}
        resp["link_status"] = link_status
        tx.gateway_response = resp
        tx.updated_at = datetime.now(timezone.utc)

    @classmethod
    def _persist_whatsapp_payment_link_tx(
        cls,
        db: Session,
        *,
        booking,
        link: Dict[str, Any],
        previous_link_id: Optional[str],
    ) -> PaymentTransaction:
        plink_id = link["payment_link_id"]
        existing = db.scalar(
            select(PaymentTransaction)
            .where(
                PaymentTransaction.entity_id == booking.booking_ref,
                PaymentTransaction.gateway_payment_id == plink_id,
                PaymentTransaction.is_duplicate.isnot(True),
            )
            .order_by(desc(PaymentTransaction.created_at))
        )
        if existing and existing.status in (PaymentStatus.PENDING, PaymentStatus.PROCESSING):
            existing.gateway_response = cls._merge_gateway_response(
                existing.gateway_response,
                extra={
                    "payment_link_id": plink_id,
                    "short_url": link.get("short_url"),
                    "order_id": link.get("order_id"),
                    "expire_by": link.get("expire_by"),
                    "link_status": link.get("status") or "created",
                    "channel": "whatsapp",
                    "booking_ref": booking.booking_ref,
                    "notes": link.get("notes") or {"booking_ref": booking.booking_ref, "channel": "whatsapp"},
                },
            )
            existing.updated_at = datetime.now(timezone.utc)
            db.commit()
            db.refresh(existing)
            return existing

        latest = db.scalar(
            select(PaymentTransaction)
            .where(
                PaymentTransaction.entity_id == booking.booking_ref,
                PaymentTransaction.gateway_provider == "RAZORPAY",
                PaymentTransaction.is_duplicate.isnot(True),
            )
            .order_by(desc(PaymentTransaction.created_at))
        )
        prev_id = previous_link_id
        prev_list = []
        if latest:
            prev_resp = latest.gateway_response if isinstance(latest.gateway_response, dict) else {}
            prev_id = prev_id or prev_resp.get("payment_link_id") or (
                latest.gateway_payment_id if str(latest.gateway_payment_id or "").startswith("plink_") else None
            )
            prev_list = list(prev_resp.get("previous_payment_links") or [])
            if prev_id and prev_id != plink_id:
                prev_list.append({
                    "payment_link_id": prev_id,
                    "short_url": prev_resp.get("short_url"),
                    "replaced_at": datetime.now(timezone.utc).isoformat(),
                })
            if latest.status in (PaymentStatus.PENDING, PaymentStatus.PROCESSING):
                stale_status = PaymentStatus.EXPIRED
                if str(prev_resp.get("link_status") or "").lower() == "cancelled":
                    stale_status = PaymentStatus.CANCELLED
                cls._mark_payment_link_terminal(latest, stale_status, prev_resp.get("link_status") or "expired")

        ref = f"PAY-WA-{uuid.uuid4().hex[:8].upper()}"
        gateway_response = {
            "payment_link_id": plink_id,
            "short_url": link.get("short_url"),
            "order_id": link.get("order_id"),
            "expire_by": link.get("expire_by"),
            "link_status": link.get("status") or "created",
            "channel": "whatsapp",
            "booking_ref": booking.booking_ref,
            "notes": link.get("notes") or {"booking_ref": booking.booking_ref, "channel": "whatsapp"},
            "raw_response": link.get("raw_response"),
            "previous_payment_links": prev_list,
        }
        tx = PaymentTransaction(
            transaction_ref=ref,
            entity_type="AIRPORT_BOOKING",
            entity_id=booking.booking_ref,
            customer_id=str(booking.user_id) if getattr(booking, "user_id", None) else None,
            amount=float(booking.total_amount),
            currency=(booking.currency or "INR").upper(),
            payment_method=PaymentMethod.UPI,
            status=PaymentStatus.PENDING,
            gateway_provider="RAZORPAY",
            gateway_payment_id=plink_id,
            gateway_response=gateway_response,
            notes="whatsapp_payment_link",
        )
        db.add(tx)
        try:
            TimelineService.add_entry(
                db,
                entity_type="AIRPORT_BOOKING",
                entity_id=booking.booking_ref,
                event_type="PAYMENT_LINK_INITIATED",
                title=f"WhatsApp Payment Link Initiated ({ref})",
                details={"payment_link_id": plink_id, "amount": float(booking.total_amount)},
            )
        except Exception:
            pass
        db.commit()
        db.refresh(tx)
        return tx

    @classmethod
    def replace_whatsapp_payment_link(cls, db: Session, booking_ref: str) -> Dict[str, Any]:
        """Force a new Payment Link for a still-PENDING booking after expiry/cancellation."""
        from app.models.schema import Booking, BookingStatus

        booking = db.scalar(select(Booking).where(Booking.booking_ref == booking_ref))
        if not booking:
            return {"success": False, "error": "BOOKING_NOT_FOUND"}
        if booking.status != BookingStatus.PENDING:
            return {"success": False, "error": "BOOKING_NOT_PENDING"}

        latest = db.scalar(
            select(PaymentTransaction)
            .where(
                PaymentTransaction.entity_id == booking_ref,
                PaymentTransaction.gateway_provider == "RAZORPAY",
                PaymentTransaction.is_duplicate.isnot(True),
            )
            .order_by(desc(PaymentTransaction.created_at))
        )
        if latest and latest.status in (PaymentStatus.PENDING, PaymentStatus.PROCESSING):
            cls._mark_payment_link_terminal(latest, PaymentStatus.EXPIRED, "expired")
            db.flush()
        return cls.initiate_whatsapp_payment_link(db, booking_ref)

    @classmethod
    def reconcile_whatsapp_payment_link(cls, db: Session, booking_ref: str) -> Dict[str, Any]:
        """
        Fetch live Payment Link status. Confirms only if Razorpay reports paid,
        via handle_verified_payment — never from customer text.
        """
        from app.models.schema import Booking, BookingStatus
        from app.providers.razorpay_provider import razorpay_provider

        booking = db.scalar(select(Booking).where(Booking.booking_ref == booking_ref))
        if not booking:
            return {"success": False, "status": "NOT_FOUND", "paid": False}
        if booking.status == BookingStatus.CONFIRMED:
            return {"success": True, "status": "ALREADY_CONFIRMED", "paid": True}

        tx = db.scalar(
            select(PaymentTransaction)
            .where(
                PaymentTransaction.entity_id == booking_ref,
                PaymentTransaction.gateway_provider == "RAZORPAY",
                PaymentTransaction.is_duplicate.isnot(True),
            )
            .order_by(desc(PaymentTransaction.created_at))
        )
        resp = tx.gateway_response if tx and isinstance(tx.gateway_response, dict) else {}
        plink_id = (resp.get("payment_link_id") if resp else None) or (
            tx.gateway_payment_id if tx and str(tx.gateway_payment_id or "").startswith("plink_") else None
        )
        if not plink_id:
            return {"success": True, "status": "NO_LINK", "paid": False}

        fetched = razorpay_provider.fetch_payment_link(plink_id)
        if not fetched.get("success"):
            return {
                "success": True,
                "status": "STATUS_UNAVAILABLE",
                "paid": False,
                "reason": fetched.get("error"),
            }

        remote_status = str(fetched.get("status") or "").lower()
        if remote_status in ("expired", "cancelled"):
            if tx:
                target = PaymentStatus.EXPIRED if remote_status == "expired" else PaymentStatus.CANCELLED
                cls._mark_payment_link_terminal(tx, target, remote_status)
                db.commit()
            return {"success": True, "status": remote_status.upper(), "paid": False}

        if remote_status != "paid":
            return {"success": True, "status": remote_status.upper() or "CREATED", "paid": False}

        amount = fetched.get("amount_paid")
        if amount is None:
            amount = fetched.get("amount")
        currency = fetched.get("currency") or (booking.currency if booking else "INR")
        data = fetched.get("data") if isinstance(fetched.get("data"), dict) else {}
        raw_payload = {
            "event": "payment_link.paid",
            "payload": {
                "payment_link": {
                    "entity": data or {
                        "id": plink_id,
                        "amount": amount,
                        "amount_paid": amount,
                        "currency": currency,
                        "status": "paid",
                        "order_id": fetched.get("order_id"),
                        "reference_id": booking_ref,
                        "notes": {"booking_ref": booking_ref, "channel": "whatsapp"},
                    }
                }
            },
        }
        result = cls.handle_verified_payment(
            db,
            event_name="payment_link.paid",
            gateway_provider="RAZORPAY",
            order_id=fetched.get("order_id") or (data.get("order_id") if data else None),
            payment_id=None,
            booking_ref=booking_ref,
            raw_payload=raw_payload,
            channel="whatsapp",
            amount=amount,
            currency=currency,
        )
        return {
            "success": bool(result.get("success")),
            "status": result.get("status"),
            "paid": result.get("status") in ("CONFIRMED",) or bool(result.get("already_confirmed")),
            "details": result,
        }

    @classmethod
    def verify_internal_webhook_signature(cls, payload: WebhookPayload, signature: Optional[str]) -> bool:
        secret = (os.getenv("PAYMENT_WEBHOOK_SECRET") or os.getenv("RAZORPAY_WEBHOOK_SECRET") or "").strip()
        if not secret:
            return not settings.is_production
        if not signature:
            return not settings.is_production
        canonical = f"{payload.transaction_ref}:{payload.event_type}:{payload.gateway_payment_id}"
        computed = hmac.new(secret.encode("utf-8"), canonical.encode("utf-8"), hashlib.sha256).hexdigest()
        return hmac.compare_digest(computed, signature)


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
        """
        Canonical, idempotent processor for all verified payment events.
        Handles payment.captured, order.paid, payment_link.paid, payment.failed,
        and payment.refunded events consistently across Web and WhatsApp.
        """
        from app.models.schema import Booking, BookingStatus
        from app.services.notification_service import NotificationService
        from sqlalchemy import or_

        extracted_ids = cls._extract_ids_from_razorpay_payload(raw_payload)
        payment_link_id = extracted_ids.get("payment_link_id")
        if not order_id:
            order_id = extracted_ids.get("order_id")
        if not payment_id:
            payment_id = extracted_ids.get("payment_id")
        if not booking_ref:
            booking_ref = extracted_ids.get("booking_ref")
        if not channel:
            channel = extracted_ids.get("channel") or channel

        logger.info(
            f"[PaymentService] Processing event '{event_name}' (order: {order_id}, payment: {payment_id}, plink: {payment_link_id}, ref: {booking_ref})"
        )

        # 1. Resolve PaymentTransaction
        transaction = None
        if order_id or payment_id or booking_ref or payment_link_id:
            conditions = []
            if order_id:
                conditions.append(PaymentTransaction.gateway_payment_id == order_id)
            if payment_id:
                conditions.append(PaymentTransaction.gateway_payment_id == payment_id)
            if payment_link_id:
                conditions.append(PaymentTransaction.gateway_payment_id == payment_link_id)
            if booking_ref:
                conditions.append(PaymentTransaction.transaction_ref == booking_ref)
                conditions.append(PaymentTransaction.entity_id == booking_ref)

            transaction = db.scalar(select(PaymentTransaction).where(or_(*conditions)).order_by(PaymentTransaction.created_at.desc()))
            if booking_ref and transaction and transaction.status != PaymentStatus.SUCCESSFUL:
                successful = db.scalar(
                    select(PaymentTransaction).where(
                        PaymentTransaction.entity_id == booking_ref,
                        PaymentTransaction.status == PaymentStatus.SUCCESSFUL,
                        PaymentTransaction.is_duplicate.isnot(True),
                    ).order_by(PaymentTransaction.created_at.asc())
                )
                if successful:
                    stored = cls._collect_tx_gateway_ids(successful)
                    incoming = {i for i in (order_id, payment_id, payment_link_id) if i}
                    if incoming & stored:
                        transaction = successful

        # 2. Resolve Booking
        booking = None
        if booking_ref:
            booking = db.scalar(select(Booking).where(Booking.booking_ref == booking_ref))
        
        if not booking and transaction and transaction.entity_id:
            try:
                b_uuid = uuid.UUID(str(transaction.entity_id))
                booking = db.scalar(select(Booking).where(or_(Booking.id == b_uuid, Booking.booking_ref == str(transaction.entity_id))))
            except ValueError:
                booking = db.scalar(select(Booking).where(Booking.booking_ref == str(transaction.entity_id)))

        if not transaction and not booking:
            logger.warning(f"[PaymentService] Neither transaction nor booking found for order '{order_id}', ref '{booking_ref}'")
            return {
                "success": False,
                "status": "NOT_FOUND",
                "reason": f"No transaction or booking found matching order '{order_id}', payment '{payment_id}', ref '{booking_ref}'"
            }

        # Cross-safety check: Ensure payment for Booking A cannot confirm Booking B
        resolved_booking_ref = booking.booking_ref if booking else (transaction.entity_id if transaction else booking_ref)
        if booking_ref and resolved_booking_ref and booking_ref != resolved_booking_ref:
            logger.error(f"[PaymentService] Safety mismatch: provided ref '{booking_ref}' != resolved ref '{resolved_booking_ref}'")
            return {
                "success": False,
                "status": "REF_MISMATCH",
                "reason": "Booking reference mismatch with order records."
            }

        # 3. Handle Successful Payment Events
        if event_name in [
            "payment.captured", "order.paid", "payment_link.paid",
            "VERIFY_ENDPOINT", "PAYMENT_SUCCESS", "payment.succeeded"
        ]:
            # For gateway webhooks (payment.captured, order.paid, payment_link.paid),
            # strictly reconcile received amount and currency with the booking / transaction records.
            if event_name in ["payment.captured", "order.paid", "payment_link.paid"]:
                received_amount_raw = amount
                received_currency_raw = currency

                if raw_payload and isinstance(raw_payload, dict):
                    pl = raw_payload.get("payload", {})
                    pay_ent = pl.get("payment", {}).get("entity", {}) if isinstance(pl.get("payment"), dict) else {}
                    ord_ent = pl.get("order", {}).get("entity", {}) if isinstance(pl.get("order"), dict) else {}
                    plink_ent = pl.get("payment_link", {}).get("entity", {}) if isinstance(pl.get("payment_link"), dict) else {}

                    if received_amount_raw is None:
                        if pay_ent.get("amount") is not None:
                            received_amount_raw = pay_ent.get("amount")
                        elif ord_ent.get("amount_paid") is not None:
                            received_amount_raw = ord_ent.get("amount_paid")
                        elif ord_ent.get("amount") is not None:
                            received_amount_raw = ord_ent.get("amount")
                        elif plink_ent.get("amount_paid") is not None:
                            received_amount_raw = plink_ent.get("amount_paid")
                        elif plink_ent.get("amount") is not None:
                            received_amount_raw = plink_ent.get("amount")

                    if not received_currency_raw:
                        received_currency_raw = (
                            pay_ent.get("currency") or
                            ord_ent.get("currency") or
                            plink_ent.get("currency")
                        )

                expected_amt_val = float(booking.total_amount) if booking else (float(transaction.amount) if transaction else None)
                expected_curr_val = (booking.currency if booking else (transaction.currency if transaction else "INR")) or "INR"

                # Check for missing amount or currency
                if received_amount_raw is None or received_currency_raw is None or str(received_currency_raw).strip() == "":
                    logger.warning(
                        f"[PaymentService] PAYMENT_AMOUNT_CHECK: Incomplete payment data for event '{event_name}' (booking '{resolved_booking_ref}'). "
                        f"expected_amount={expected_amt_val}, received_amount={received_amount_raw}, "
                        f"expected_currency={expected_curr_val}, received_currency={received_currency_raw}, "
                        f"result=PAYMENT_DATA_INCOMPLETE"
                    )
                    return {
                        "success": False,
                        "status": "PAYMENT_DATA_INCOMPLETE",
                        "reason": "Webhook payload missing required amount or currency data.",
                        "booking_ref": resolved_booking_ref
                    }

                # Currency validation (case-insensitive)
                exp_curr_norm = str(expected_curr_val).strip().upper()
                rec_curr_norm = str(received_currency_raw).strip().upper()

                if exp_curr_norm != rec_curr_norm:
                    logger.error(
                        f"[PaymentService] PAYMENT_AMOUNT_CHECK: "
                        f"expected_amount={expected_amt_val}, received_amount={received_amount_raw}, "
                        f"expected_currency={exp_curr_norm}, received_currency={rec_curr_norm}, "
                        f"result=CURRENCY_MISMATCH"
                    )
                    return {
                        "success": False,
                        "status": "CURRENCY_MISMATCH",
                        "reason": f"Payment currency '{rec_curr_norm}' does not match expected booking currency '{exp_curr_norm}'.",
                        "booking_ref": resolved_booking_ref,
                        "expected_currency": exp_curr_norm,
                        "received_currency": rec_curr_norm
                    }

                # Amount validation with Decimal precision (Razorpay paise / 100 -> rupees)
                try:
                    rec_paise = Decimal(str(received_amount_raw))
                    rec_rupees = rec_paise / Decimal("100")
                    exp_rupees = Decimal(str(expected_amt_val))
                except Exception as num_err:
                    logger.error(f"[PaymentService] PAYMENT_AMOUNT_CHECK: Numeric parsing error: {num_err}")
                    return {
                        "success": False,
                        "status": "PAYMENT_DATA_INCOMPLETE",
                        "reason": "Invalid numeric format in payment amount.",
                        "booking_ref": resolved_booking_ref
                    }

                if abs(rec_rupees - exp_rupees) > Decimal("0.001"):
                    logger.error(
                        f"[PaymentService] PAYMENT_AMOUNT_CHECK: "
                        f"expected_amount={float(exp_rupees)}, received_amount={float(rec_rupees)}, "
                        f"expected_currency={exp_curr_norm}, received_currency={rec_curr_norm}, "
                        f"result=AMOUNT_MISMATCH"
                    )
                    return {
                        "success": False,
                        "status": "AMOUNT_MISMATCH",
                        "reason": f"Payment amount {rec_rupees} {rec_curr_norm} does not match expected booking amount {exp_rupees} {exp_curr_norm}.",
                        "booking_ref": resolved_booking_ref,
                        "expected_amount": float(exp_rupees),
                        "received_amount": float(rec_rupees)
                    }

                logger.info(
                    f"[PaymentService] PAYMENT_AMOUNT_CHECK: "
                    f"expected_amount={float(exp_rupees)}, received_amount={float(rec_rupees)}, "
                    f"expected_currency={exp_curr_norm}, received_currency={rec_curr_norm}, "
                    f"result=EXACT_MATCH"
                )

            # Double Payment & Overpayment Protection Guard
            is_booking_confirmed = booking and booking.status == BookingStatus.CONFIRMED

            # Check if this is an idempotent replay of the exact same payment vs a secondary duplicate payment
            if is_booking_confirmed:
                existing_successful_tx = db.scalar(
                    select(PaymentTransaction).where(
                        PaymentTransaction.entity_id == resolved_booking_ref,
                        PaymentTransaction.status == PaymentStatus.SUCCESSFUL,
                        PaymentTransaction.is_duplicate.isnot(True)
                    ).order_by(PaymentTransaction.created_at.asc())
                )
                if not existing_successful_tx:
                    existing_successful_tx = db.scalar(
                        select(PaymentTransaction).where(
                            PaymentTransaction.transaction_ref == resolved_booking_ref,
                            PaymentTransaction.status == PaymentStatus.SUCCESSFUL,
                            PaymentTransaction.is_duplicate.isnot(True)
                        ).order_by(PaymentTransaction.created_at.asc())
                    )
                if not existing_successful_tx and transaction and transaction.status == PaymentStatus.SUCCESSFUL:
                    existing_successful_tx = transaction

                existing_pid = existing_successful_tx.gateway_payment_id if existing_successful_tx else None

                # Determine if the incoming event belongs to the same financial payment.
                # Case 1: Direct ID match (payment_id == existing gateway_payment_id, or order_id == existing gateway_payment_id)
                is_same_payment = (
                    (payment_id and existing_pid and payment_id == existing_pid) or
                    (order_id and existing_pid and order_id == existing_pid) or
                    (payment_link_id and existing_pid and payment_link_id == existing_pid)
                )

                # Case 2: The resolved transaction IS the same DB row as the existing successful TX.
                # Same row is the same financial payment only when incoming ids overlap stored ids
                # (or no ids were provided). A distinct payment_id on the same booking is a duplicate.
                if not is_same_payment and transaction and existing_successful_tx:
                    if transaction.id == existing_successful_tx.id:
                        stored_ids = cls._collect_tx_gateway_ids(existing_successful_tx)
                        incoming_ids = {i for i in (payment_id, order_id, payment_link_id) if i}
                        if not incoming_ids or (incoming_ids & stored_ids):
                            is_same_payment = True

                # Case 3: The order_id matches the original order stored in gateway_response.
                # After payment.captured, gateway_payment_id is updated from order_id to payment_id,
                # but the original order_id is preserved in the gateway_response payload.
                if not is_same_payment and order_id and existing_successful_tx:
                    gw_resp = existing_successful_tx.gateway_response or {}
                    original_order_id = None
                    if isinstance(gw_resp, dict):
                        # Check nested payload structures for the original order_id
                        original_order_id = (
                            gw_resp.get("order_id") or
                            gw_resp.get("payload", {}).get("payment", {}).get("entity", {}).get("order_id") or
                            gw_resp.get("payload", {}).get("order", {}).get("entity", {}).get("id")
                        )
                    if original_order_id and order_id == original_order_id:
                        is_same_payment = True

                # Case 4: stored payment_link_id / order_id / payment_id vs incoming ids
                if not is_same_payment and existing_successful_tx:
                    stored_ids = cls._collect_tx_gateway_ids(existing_successful_tx)
                    incoming_ids = {i for i in (payment_id, order_id, payment_link_id) if i}
                    if incoming_ids and (incoming_ids & stored_ids):
                        is_same_payment = True

                logger.info(f"[DEBUG_DP] existing_pid={existing_pid}, payment_id={payment_id}, order_id={order_id}, plink={payment_link_id}, is_same_payment={is_same_payment}")

                if not is_same_payment and (payment_id or order_id):
                    # Distinct second payment arrived for already confirmed booking!
                    logger.warning(
                        f"[PaymentService] DOUBLE PAYMENT DETECTED for booking '{resolved_booking_ref}'. "
                        f"Existing payment: '{existing_pid}', New payment: '{payment_id}'."
                    )
                    dup_ref = f"PAY-DUP-{uuid.uuid4().hex[:6].upper()}"
                    dup_tx = PaymentTransaction(
                        transaction_ref=dup_ref,
                        entity_type="AIRPORT_BOOKING",
                        entity_id=resolved_booking_ref or str(booking.id if booking else ""),
                        customer_id=str(booking.user_id) if booking and booking.user_id else None,
                        amount=float(booking.total_amount) if booking else (transaction.amount if transaction else 0.0),
                        currency=booking.currency if booking else (transaction.currency if transaction else "INR"),
                        payment_method=PaymentMethod.CREDIT_CARD,
                        status=PaymentStatus.SUCCESSFUL,
                        gateway_provider=gateway_provider,
                        gateway_payment_id=payment_id or order_id,
                        gateway_signature=signature,
                        gateway_response=raw_payload,
                        is_duplicate=True,
                        notes=f"DUPLICATE_PAYMENT_DETECTED: Booking was already confirmed by payment '{existing_pid}'."
                    )
                    db.add(dup_tx)
                    db.commit()

                    try:
                        AdminService.log_audit_action(
                            db,
                            actor_email=booking.passenger_email if booking else "system@shafsky.com",
                            action="DUPLICATE_PAYMENT_DETECTED",
                            resource_type="PAYMENT",
                            resource_id=str(dup_tx.id),
                            details={
                                "booking_ref": resolved_booking_ref,
                                "original_payment_id": existing_pid,
                                "duplicate_payment_id": payment_id,
                                "duplicate_tx_ref": dup_ref
                            }
                        )
                    except Exception:
                        pass

                    return {
                        "success": True,
                        "status": "DUPLICATE_PAYMENT_FLAGGED",
                        "booking_ref": resolved_booking_ref,
                        "payment_id": payment_id,
                        "duplicate_tx_ref": dup_ref
                    }

                # Exact same payment replay - idempotent return
                logger.info(f"[PaymentService] Booking '{resolved_booking_ref}' is already confirmed and paid. Idempotent return.")
                # Retry PDF/storage if the invoice row exists without a stored document.
                # Never creates a second invoice; never reverses payment.
                try:
                    tx_for_invoice = existing_successful_tx or transaction
                    if tx_for_invoice:
                        retry_inv = db.scalar(select(Invoice).where(Invoice.transaction_id == tx_for_invoice.id))
                        if retry_inv and not retry_inv.pdf_url:
                            from app.services.pdf_service import schedule_invoice_fulfillment
                            schedule_invoice_fulfillment(str(retry_inv.id))
                except Exception as pdf_retry_err:
                    logger.error(
                        "[PaymentService] Invoice PDF retry scheduling failed: %s",
                        type(pdf_retry_err).__name__,
                    )
                return {
                    "success": True,
                    "status": "CONFIRMED",
                    "already_confirmed": True,
                    "booking_ref": resolved_booking_ref,
                    "payment_id": payment_id or (transaction.gateway_payment_id if transaction else None)
                }

            # Update PaymentTransaction
            if transaction:
                transaction.status = PaymentStatus.SUCCESSFUL
                if payment_id:
                    transaction.gateway_payment_id = payment_id
                if signature:
                    transaction.gateway_signature = signature
                transaction.gateway_response = cls._merge_gateway_response(
                    transaction.gateway_response,
                    raw_payload=raw_payload,
                    extra={
                        "payment_link_id": payment_link_id,
                        "order_id": order_id,
                        "payment_id": payment_id,
                        "booking_ref": resolved_booking_ref,
                        "channel": channel or extracted_ids.get("channel"),
                        "link_status": "paid",
                    },
                )
                transaction.updated_at = datetime.now(timezone.utc)

            # Update Booking
            if booking:
                booking.status = BookingStatus.CONFIRMED
                booking.updated_at = datetime.now(timezone.utc)
                # Future-proof soft-link: always store booking_ref on the payment row.
                if transaction and booking.booking_ref:
                    if str(transaction.entity_id or "") != str(booking.booking_ref):
                        logger.info(
                            "[PaymentService] Normalizing payment entity_id to booking_ref=%s",
                            booking.booking_ref,
                        )
                    transaction.entity_id = str(booking.booking_ref)
                    transaction.entity_type = transaction.entity_type or "AIRPORT_BOOKING"

            # Auto-generate Tax Invoice DB row (Idempotent check inside generate_invoice).
            # PDF/storage/email happen after commit so the webhook is not blocked.
            invoice_id_to_fulfill = None
            if transaction:
                try:
                    customer_name = booking.passenger_name if booking else "Valued Guest"
                    customer_email = booking.passenger_email if booking else "customer@shafsky.com"
                    invoice_row = cls.generate_invoice(
                        db,
                        transaction=transaction,
                        customer_name=customer_name,
                        customer_email=customer_email
                    )
                    if invoice_row:
                        invoice_id_to_fulfill = str(invoice_row.id)
                except Exception:
                    logger.exception(
                        "[PaymentService] Invoice row creation failed; payment confirmation will still be committed"
                    )

            # Audit Log
            try:
                AdminService.log_audit_action(
                    db,
                    actor_email=booking.passenger_email if booking else "system@shafsky.com",
                    action="PAYMENT_SUCCESSFUL",
                    resource_type="PAYMENT",
                    resource_id=str(transaction.id) if transaction else (str(booking.id) if booking else ""),
                    details={
                        "order_id": order_id,
                        "payment_id": payment_id,
                        "booking_ref": resolved_booking_ref,
                        "event": event_name,
                        "amount": float(booking.total_amount) if booking else (transaction.amount if transaction else 0.0)
                    }
                )
            except Exception as audit_err:
                logger.warning(f"[PaymentService] Audit logging notice: {audit_err}")

            # Timeline Entry
            try:
                TimelineService.add_entry(
                    db,
                    entity_type="AIRPORT_BOOKING",
                    entity_id=resolved_booking_ref or "PAYMENT",
                    event_type="PAYMENT_SUCCESSFUL",
                    title=f"Payment Successful ({resolved_booking_ref})",
                    details={
                        "amount": float(booking.total_amount) if booking else (transaction.amount if transaction else 0.0),
                        "payment_id": payment_id,
                        "order_id": order_id,
                        "event": event_name
                    }
                )
            except Exception as time_err:
                logger.warning(f"[PaymentService] Timeline logging notice: {time_err}")

            # Commit DB changes before triggering external notifications
            try:
                db.commit()
            except Exception:
                logger.exception("[PaymentService] Payment confirmation commit failed")
                try:
                    db.rollback()
                except Exception:
                    pass
                return {
                    "success": False,
                    "status": "COMMIT_FAILED",
                    "reason": "Could not persist payment confirmation.",
                    "booking_ref": resolved_booking_ref,
                }
            if transaction:
                db.refresh(transaction)
            if booking:
                db.refresh(booking)

            # Invoice PDF + confirmation email/WhatsApp after commit (never blocks webhook).
            # PDF/storage/email failure must not undo payment or the invoice row.
            if invoice_id_to_fulfill:
                try:
                    from app.services.pdf_service import schedule_invoice_fulfillment
                    schedule_invoice_fulfillment(invoice_id_to_fulfill)
                except Exception as pdf_err:
                    logger.error("[PaymentService] Invoice fulfillment scheduling failed: %s", type(pdf_err).__name__)
                    if booking:
                        try:
                            meta = booking.metadata_json or {}
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
                                "invoice_attached": False,
                            })
                        except Exception as notif_err:
                            logger.error(f"[PaymentService] Confirmation notification error: {notif_err}")
            elif booking:
                try:
                    meta = booking.metadata_json or {}
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
                except Exception as notif_err:
                    logger.error(f"[PaymentService] Confirmation notification error: {notif_err}")

            # Update WhatsApp Conversation State if relevant
            try:
                from app.models.whatsapp_models import WhatsAppConversation
                conv = None
                if resolved_booking_ref:
                    conv = db.scalar(
                        select(WhatsAppConversation).where(WhatsAppConversation.booking_ref == resolved_booking_ref)
                    )
                if not conv and booking is not None:
                    conv = db.scalar(
                        select(WhatsAppConversation).where(WhatsAppConversation.booking_id == booking.id)
                    )
                if conv:
                    conv.payment_status = "SUCCESSFUL"
                    conv.current_state = "COMPLETED"
                    conv.updated_at = datetime.now(timezone.utc)
                    db.commit()
            except Exception as wa_err:
                logger.debug(f"[PaymentService] WhatsApp session sync notice: {wa_err}")

            return {
                "success": True,
                "status": "CONFIRMED",
                "booking_ref": resolved_booking_ref,
                "payment_id": payment_id
            }

        # 3b. Handle payment.authorized — funds pre-authorized but NOT yet captured/settled.
        # Do NOT confirm booking, generate invoice, or send confirmation notifications.
        # The booking remains PENDING until payment.captured or order.paid arrives.
        elif event_name in ["payment.authorized"]:
            if transaction:
                if transaction.status in (PaymentStatus.PENDING, PaymentStatus.PROCESSING):
                    transaction.status = PaymentStatus.PROCESSING
                    if payment_id:
                        transaction.gateway_payment_id = payment_id
                    if raw_payload:
                        transaction.gateway_response = raw_payload
                    transaction.updated_at = datetime.now(timezone.utc)

            # Booking explicitly NOT confirmed — awaiting capture
            logger.info(
                f"[PaymentService] payment.authorized received for booking '{resolved_booking_ref}'. "
                f"Transaction set to PROCESSING. Awaiting payment.captured/order.paid for confirmation."
            )

            try:
                TimelineService.add_entry(
                    db,
                    entity_type="AIRPORT_BOOKING",
                    entity_id=resolved_booking_ref or "PAYMENT",
                    event_type="PAYMENT_AUTHORIZED",
                    title=f"Payment Authorized ({resolved_booking_ref})",
                    details={
                        "payment_id": payment_id,
                        "order_id": order_id,
                        "event": event_name,
                        "note": "Awaiting capture for final confirmation."
                    }
                )
            except Exception as time_err:
                logger.warning(f"[PaymentService] Timeline logging notice: {time_err}")

            db.commit()
            return {
                "success": True,
                "status": "AUTHORIZED",
                "booking_ref": resolved_booking_ref,
                "payment_id": payment_id
            }

        # 3c. Payment Link expired / cancelled — booking stays PENDING and recoverable.
        elif event_name in ["payment_link.expired", "payment_link.cancelled"]:
            terminal_status = PaymentStatus.EXPIRED if event_name.endswith("expired") else PaymentStatus.CANCELLED
            link_status = "expired" if terminal_status == PaymentStatus.EXPIRED else "cancelled"
            if transaction and transaction.status != PaymentStatus.SUCCESSFUL:
                cls._mark_payment_link_terminal(transaction, terminal_status, link_status)
                transaction.gateway_response = cls._merge_gateway_response(
                    transaction.gateway_response,
                    raw_payload=raw_payload,
                    extra={
                        "payment_link_id": payment_link_id,
                        "link_status": link_status,
                        "booking_ref": resolved_booking_ref,
                    },
                )
            if booking and booking.status == BookingStatus.PENDING:
                logger.info(
                    "[PaymentService] Payment link %s for booking '%s'; booking remains PENDING.",
                    link_status,
                    resolved_booking_ref,
                )
            try:
                from app.models.whatsapp_models import WhatsAppConversation
                conv = None
                if resolved_booking_ref:
                    conv = db.scalar(
                        select(WhatsAppConversation).where(WhatsAppConversation.booking_ref == resolved_booking_ref)
                    )
                if conv:
                    conv.payment_status = "EXPIRED" if link_status == "expired" else "CANCELLED"
                    conv.current_state = "WAITING_PAYMENT"
                    conv.updated_at = datetime.now(timezone.utc)
            except Exception as wa_err:
                logger.debug("[PaymentService] WhatsApp expiry sync notice: %s", wa_err)
            db.commit()
            return {
                "success": True,
                "status": terminal_status.value,
                "booking_ref": resolved_booking_ref,
            }

        # 4. Handle Failed Payment Events
        elif event_name in ["payment.failed", "PAYMENT_FAILED"]:
            if transaction and transaction.status != PaymentStatus.SUCCESSFUL:
                transaction.status = PaymentStatus.FAILED
                if raw_payload:
                    transaction.gateway_response = raw_payload
                transaction.updated_at = datetime.now(timezone.utc)

            # Keep booking in PENDING state to allow payment retry
            if booking and booking.status == BookingStatus.PENDING:
                logger.info(f"[PaymentService] Payment failed for booking '{resolved_booking_ref}'; keeping booking PENDING for retry.")

            try:
                AdminService.log_audit_action(
                    db,
                    actor_email=booking.passenger_email if booking else "system@shafsky.com",
                    action="PAYMENT_FAILED",
                    resource_type="PAYMENT",
                    resource_id=str(transaction.id) if transaction else (str(booking.id) if booking else ""),
                    details={"order_id": order_id, "payment_id": payment_id, "event": event_name}
                )
            except Exception:
                pass

            db.commit()
            return {
                "success": True,
                "status": "FAILED",
                "booking_ref": resolved_booking_ref
            }

        # 5. Handle Refund Events
        elif event_name in ["refund.created", "refund.processed", "payment.refunded", "PAYMENT_REFUNDED"]:
            # Extract refund amounts from payload
            refund_amount_raw = None
            amount_refunded_raw = None
            payment_amount_raw = None
            refund_status_str = None

            if raw_payload and isinstance(raw_payload, dict):
                pl = raw_payload.get("payload", {})
                ref_ent = pl.get("refund", {}).get("entity", {}) if isinstance(pl.get("refund"), dict) else {}
                pay_ent = pl.get("payment", {}).get("entity", {}) if isinstance(pl.get("payment"), dict) else {}

                refund_amount_raw = ref_ent.get("amount")
                amount_refunded_raw = pay_ent.get("amount_refunded")
                payment_amount_raw = pay_ent.get("amount")
                refund_status_str = pay_ent.get("refund_status") or ref_ent.get("status")

            # Determine if this is a full refund or partial refund
            is_full_refund = True  # Default for explicit refund event unless partial indicators found

            target_amount = float(booking.total_amount) if booking else (float(transaction.amount) if transaction else None)

            if amount_refunded_raw is not None and payment_amount_raw is not None:
                is_full_refund = float(amount_refunded_raw) >= float(payment_amount_raw)
            elif refund_status_str in ("partial", "PARTIAL"):
                is_full_refund = False
            elif refund_status_str in ("full", "FULL"):
                is_full_refund = True
            elif refund_amount_raw is not None and target_amount is not None:
                # refund_amount_raw is in paise
                refund_rupees = float(refund_amount_raw) / 100.0
                is_full_refund = round(refund_rupees, 2) >= round(target_amount, 2)

            if transaction:
                transaction.status = PaymentStatus.REFUNDED if is_full_refund else PaymentStatus.PARTIALLY_REFUNDED
                transaction.updated_at = datetime.now(timezone.utc)
                for inv in transaction.invoices:
                    inv.status = InvoiceStatus.CANCELLED if is_full_refund else InvoiceStatus.PARTIALLY_PAID
            
            if booking:
                if is_full_refund:
                    booking.status = BookingStatus.CANCELLED
                    booking.updated_at = datetime.now(timezone.utc)
                    logger.info(f"[PaymentService] Full refund processed for booking '{resolved_booking_ref}'. Booking status set to CANCELLED.")
                else:
                    logger.info(f"[PaymentService] Partial refund processed for booking '{resolved_booking_ref}'. Booking remains '{booking.status.value}'.")

            # Timeline event for refund
            try:
                TimelineService.add_entry(
                    db,
                    entity_type=transaction.entity_type if transaction else "AIRPORT_BOOKING",
                    entity_id=resolved_booking_ref or "PAYMENT",
                    event_type="PAYMENT_REFUNDED",
                    title=f"{'Full' if is_full_refund else 'Partial'} Refund Processed ({resolved_booking_ref})",
                    details={
                        "event": event_name,
                        "is_full_refund": is_full_refund,
                        "payment_id": payment_id,
                        "order_id": order_id
                    }
                )
            except Exception as time_err:
                logger.warning(f"[PaymentService] Timeline logging notice: {time_err}")

            db.commit()

            return {
                "success": True,
                "status": "REFUNDED" if is_full_refund else "PARTIALLY_REFUNDED",
                "is_full_refund": is_full_refund,
                "booking_ref": resolved_booking_ref
            }

        return {
            "success": True,
            "status": "IGNORED",
            "event": event_name,
            "booking_ref": resolved_booking_ref
        }

    @classmethod
    def retry_booking_payment(
        cls,
        db: Session,
        booking_ref: str,
        customer_email: Optional[str] = None,
        user_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Retries payment on an existing PENDING booking without duplicating the booking.
        Reuses an unpaid Razorpay order when one already exists; otherwise creates a new order.
        """
        from app.models.schema import Booking, BookingStatus
        from app.providers.razorpay_provider import razorpay_provider

        booking = db.scalar(select(Booking).where(Booking.booking_ref == booking_ref))
        if not booking:
            raise ValueError(f"Booking with reference '{booking_ref}' not found.")

        if booking.status == BookingStatus.CONFIRMED:
            raise ValueError(f"Booking '{booking_ref}' is already confirmed and paid.")

        if booking.status in (BookingStatus.CANCELLED, BookingStatus.REJECTED):
            raise ValueError(f"Booking '{booking_ref}' is cancelled/rejected and cannot be paid.")

        authoritative_amount = float(booking.total_amount)
        amount_paise = int(round(authoritative_amount * 100))

        existing_tx = db.scalar(
            select(PaymentTransaction)
            .where(
                PaymentTransaction.entity_id.in_([booking.booking_ref, str(booking.id)]),
                PaymentTransaction.status == PaymentStatus.PENDING,
                PaymentTransaction.gateway_provider == "RAZORPAY",
            )
            .order_by(desc(PaymentTransaction.created_at))
        )
        existing_oid = (existing_tx.gateway_payment_id if existing_tx else "") or ""
        if existing_oid.startswith("order_") and not existing_oid.startswith("order_sim_"):
            return {
                "bookingRef": booking.booking_ref,
                "transactionRef": existing_tx.transaction_ref,
                "razorpay_order_id": existing_oid,
                "razorpay_key_id": razorpay_provider.key_id,
                "razorpay_amount_paise": amount_paise,
                "totalAmount": authoritative_amount,
                "currency": booking.currency or "INR",
                "passengerName": booking.passenger_name,
                "passengerEmail": booking.passenger_email,
                "passengerPhone": booking.passenger_phone,
            }

        ref = f"PAY-RETRY-{uuid.uuid4().hex[:6].upper()}"

        intent = razorpay_provider.create_order(
            amount=authoritative_amount,
            currency=booking.currency or "INR",
            receipt=booking.booking_ref,
            notes={
                "transaction_ref": ref,
                "booking_ref": booking.booking_ref,
                "is_retry": "true",
                "channel": "web"
            }
        )
        if not intent.get("success") or not intent.get("order_id"):
            raise ValueError(intent.get("error") or "Razorpay order could not be created.")
        if str(intent.get("order_id", "")).startswith("order_sim_"):
            raise ValueError("Payment gateway is not configured for live checkout.")
        order_id = intent.get("order_id")
        amount_paise = int(intent.get("amount") or amount_paise)

        tx = PaymentTransaction(
            transaction_ref=ref,
            entity_type="AIRPORT_BOOKING",
            entity_id=booking.booking_ref,
            customer_id=user_id or (str(booking.user_id) if booking.user_id else None),
            amount=authoritative_amount,
            currency=booking.currency or "INR",
            payment_method=PaymentMethod.CREDIT_CARD,
            status=PaymentStatus.PENDING,
            gateway_provider="RAZORPAY",
            gateway_payment_id=order_id,
            gateway_response=intent
        )
        db.add(tx)
        db.commit()
        db.refresh(tx)

        try:
            TimelineService.add_entry(
                db,
                entity_type="AIRPORT_BOOKING",
                entity_id=booking.booking_ref,
                event_type="PAYMENT_RETRY_INITIATED",
                title=f"Payment Retry Initiated ({ref})",
                details={"order_id": order_id, "amount": authoritative_amount}
            )
        except Exception:
            pass

        return {
            "bookingRef": booking.booking_ref,
            "transactionRef": ref,
            "razorpay_order_id": order_id,
            "razorpay_key_id": razorpay_provider.key_id,
            "razorpay_amount_paise": amount_paise,
            "totalAmount": authoritative_amount,
            "currency": booking.currency or "INR",
            "passengerName": booking.passenger_name,
            "passengerEmail": booking.passenger_email,
            "passengerPhone": booking.passenger_phone,
        }

    @classmethod
    def process_webhook(
        cls,
        db: Session,
        payload: WebhookPayload,
        provider: Optional[PaymentProvider] = None
    ) -> PaymentTransaction:
        """Handles incoming payment gateway webhooks with strict idempotency and state machine protection.

        Signature verification must already have passed at the router layer.
        Do not default to MockPaymentProvider (H10) — that path is for explicit tests only.
        """
        _ = provider  # optional injected provider reserved for tests; not used for auth

        transaction = db.scalar(
            select(PaymentTransaction).where(PaymentTransaction.transaction_ref == payload.transaction_ref)
        )
        if not transaction:
            raise ValueError(f"Transaction with reference '{payload.transaction_ref}' not found.")

        # 1. Rule 14: Webhook Idempotency Check
        if transaction.status == PaymentStatus.SUCCESSFUL:
            logger.info("Idempotent webhook delivery received for transaction %s; skipping duplicate processing.", payload.transaction_ref)
            return transaction

        # 2. Rule 15: Payment State Machine Transition Protection
        if transaction.status in [PaymentStatus.FAILED, PaymentStatus.REFUNDED, PaymentStatus.EXPIRED]:
            if payload.event_type in ["payment.succeeded", "PAYMENT_SUCCESS"]:
                raise ValueError(f"Invalid payment state transition from '{transaction.status.value}' to 'SUCCESSFUL'.")

        # Update status based on event
        if payload.event_type in ["payment.succeeded", "PAYMENT_SUCCESS"]:
            transaction.status = PaymentStatus.SUCCESSFUL
            transaction.gateway_payment_id = payload.gateway_payment_id

            # Auto-generate Invoice (Idempotent)
            cls.generate_invoice(
                db,
                transaction=transaction,
                customer_name="Valued Customer",
                customer_email="customer@shafsky.com"
            )

            # Timeline event
            TimelineService.add_entry(
                db,
                entity_type=transaction.entity_type,
                entity_id=transaction.entity_id,
                event_type="PAYMENT_VERIFIED",
                title=f"Payment Verified ({transaction.transaction_ref})",
                details={"amount": transaction.amount, "currency": transaction.currency}
            )

        elif payload.event_type in ["payment.failed", "PAYMENT_FAILED"]:
            transaction.status = PaymentStatus.FAILED

        transaction.updated_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(transaction)
        return transaction


    @classmethod
    def generate_invoice(
        cls,
        db: Session,
        transaction: PaymentTransaction,
        customer_name: str,
        customer_email: str
    ) -> Optional[Invoice]:
        """Generates a tax invoice for a transaction with duplicate prevention."""
        # Safety guard: never generate an invoice for a non-successful payment
        if transaction.status != PaymentStatus.SUCCESSFUL:
            logger.warning(
                "[PaymentService] generate_invoice called for non-SUCCESSFUL transaction %s (status=%s); skipping.",
                transaction.transaction_ref,
                transaction.status.value if hasattr(transaction.status, "value") else transaction.status,
            )
            return None

        existing_invoice = db.scalar(select(Invoice).where(Invoice.transaction_id == transaction.id))
        if existing_invoice:
            return existing_invoice

        tax_rate = 0.18  # 18% GST/Tax standard
        subtotal = round(transaction.amount / (1 + tax_rate), 2)
        tax_amt = round(transaction.amount - subtotal, 2)
        is_paid = transaction.status == PaymentStatus.SUCCESSFUL

        for _ in range(5):
            invoice_num = f"INV-{datetime.now(timezone.utc).strftime('%Y%m%d')}-{uuid.uuid4().hex[:8].upper()}"
            invoice = Invoice(
                invoice_number=invoice_num,
                transaction_id=transaction.id,
                customer_name=customer_name,
                customer_email=customer_email,
                subtotal_amount=subtotal,
                tax_amount=tax_amt,
                total_amount=transaction.amount,
                currency=transaction.currency,
                status=InvoiceStatus.PAID if is_paid else InvoiceStatus.ISSUED,
                paid_at=datetime.now(timezone.utc) if is_paid else None
            )
            try:
                with db.begin_nested():
                    db.add(invoice)
                    db.flush()
                return invoice
            except IntegrityError:
                logger.warning("[PaymentService] Invoice number collision; retrying allocation")
                continue

        logger.error("[PaymentService] Unable to allocate a unique invoice number without aborting payment")
        return None

    @classmethod
    def process_refund(
        cls,
        db: Session,
        payload: RefundRequest,
        actor_email: str = "admin@shafsky.com",
        provider: Optional[PaymentProvider] = None
    ) -> Refund:
        """
        Processes a production refund against a successful payment transaction.
        Validates refund ceiling, invokes Razorpay refund API, and manages partial vs full states.
        """
        from app.providers.razorpay_provider import razorpay_provider
        from app.models.schema import Booking, BookingStatus

        try:
            tx_uuid = uuid.UUID(payload.transaction_id)
        except Exception:
            raise ValueError("Invalid transaction UUID format.")

        transaction = db.scalar(select(PaymentTransaction).where(PaymentTransaction.id == tx_uuid))
        if not transaction:
            raise ValueError(f"Transaction with ID '{payload.transaction_id}' not found.")

        if transaction.status not in (PaymentStatus.SUCCESSFUL, PaymentStatus.PARTIALLY_REFUNDED):
            raise ValueError(
                f"Cannot refund transaction in '{transaction.status.value if hasattr(transaction.status, 'value') else transaction.status}' status. "
                "Only SUCCESSFUL or PARTIALLY_REFUNDED transactions can be refunded."
            )

        if payload.amount <= 0:
            raise ValueError("Refund amount must be greater than zero.")

        # Calculate already refunded total
        existing_refunds_total = sum(
            r.amount for r in transaction.refunds
            if r.status in (PaymentStatus.REFUNDED, PaymentStatus.SUCCESSFUL, PaymentStatus.PROCESSING)
        )
        available_for_refund = round(transaction.amount - existing_refunds_total, 2)

        if round(payload.amount, 2) > available_for_refund:
            raise ValueError(
                f"Refund amount (₹{payload.amount}) exceeds available refundable balance (₹{available_for_refund}). "
                f"Total paid: ₹{transaction.amount}, already refunded: ₹{existing_refunds_total}."
            )

        ref_id = f"REF-{uuid.uuid4().hex[:8].upper()}"

        # Invoke Razorpay Refund API
        gateway_payment_id = transaction.gateway_payment_id or str(transaction.id)
        gateway_res = razorpay_provider.create_refund(
            payment_id=gateway_payment_id,
            amount=payload.amount,
            reason=payload.reason or "Admin authorized refund"
        )

        if not gateway_res.get("success"):
            raise ValueError(gateway_res.get("error", "Razorpay refund execution failed."))

        gateway_refund_id = gateway_res.get("refund_id")

        # Determine if full or partial refund
        new_total_refunded = existing_refunds_total + payload.amount
        is_full_refund = round(new_total_refunded, 2) >= round(transaction.amount, 2)
        new_tx_status = PaymentStatus.REFUNDED if is_full_refund else PaymentStatus.PARTIALLY_REFUNDED

        refund = Refund(
            refund_ref=ref_id,
            transaction_id=transaction.id,
            amount=payload.amount,
            currency=transaction.currency,
            reason=payload.reason or "Admin authorized refund",
            status=PaymentStatus.REFUNDED,
            gateway_refund_id=gateway_refund_id,
            processed_at=datetime.now(timezone.utc)
        )
        db.add(refund)

        transaction.status = new_tx_status
        transaction.updated_at = datetime.now(timezone.utc)

        # Update linked Invoices
        for inv in transaction.invoices:
            inv.status = InvoiceStatus.CANCELLED if is_full_refund else InvoiceStatus.PARTIALLY_PAID

        # Update linked Booking if full refund
        if is_full_refund:
            booking = None
            if transaction.entity_id:
                booking = db.scalar(select(Booking).where(Booking.booking_ref == str(transaction.entity_id)))
                if not booking:
                    try:
                        b_uuid = uuid.UUID(str(transaction.entity_id))
                        booking = db.scalar(select(Booking).where(Booking.id == b_uuid))
                    except (ValueError, TypeError):
                        pass
            if not booking and transaction.transaction_ref:
                booking = db.scalar(select(Booking).where(Booking.booking_ref == transaction.transaction_ref))

            if booking:
                booking.status = BookingStatus.CANCELLED
                booking.updated_at = datetime.now(timezone.utc)
                logger.info(f"[PaymentService] Full refund processed for booking '{booking.booking_ref}'. Booking status set to CANCELLED.")

        # Timeline event
        TimelineService.add_entry(
            db,
            entity_type=transaction.entity_type,
            entity_id=transaction.entity_id,
            event_type="PAYMENT_REFUNDED",
            title=f"Refund Issued ({ref_id})",
            details={
                "refundAmount": payload.amount,
                "refundRef": ref_id,
                "isFullRefund": is_full_refund,
                "gatewayRefundId": gateway_refund_id
            }
        )

        # Audit log
        AdminService.log_audit_action(
            db,
            actor_email=actor_email,
            action="PAYMENT_REFUNDED",
            resource_type="PAYMENT",
            resource_id=str(transaction.id),
            details={
                "refund_ref": ref_id,
                "amount": payload.amount,
                "gateway_refund_id": gateway_refund_id,
                "is_full_refund": is_full_refund
            }
        )

        db.commit()
        db.refresh(refund)
        return refund

    @classmethod
    def get_reconciliation_report(cls, db: Session) -> Dict[str, Any]:
        """
        Scans all payment transactions and bookings to identify discrepancies,
        anomalies, duplicate payments, amount mismatches, and orphan records.
        """
        from app.models.schema import Booking, BookingStatus
        from datetime import timedelta

        transactions = list(db.scalars(select(PaymentTransaction).order_by(desc(PaymentTransaction.created_at))).all())
        bookings = {b.booking_ref: b for b in db.scalars(select(Booking)).all()}

        anomalies = []
        duplicate_payments = []
        paid_count = 0
        refunded_count = 0
        total_settled = 0.0
        total_refunded = 0.0

        tx_by_booking: Dict[str, List[PaymentTransaction]] = {}
        for tx in transactions:
            b_ref = tx.entity_id
            tx_by_booking.setdefault(b_ref, []).append(tx)

            if tx.status == PaymentStatus.SUCCESSFUL:
                paid_count += 1
                total_settled += tx.amount
            elif tx.status in (PaymentStatus.REFUNDED, PaymentStatus.PARTIALLY_REFUNDED):
                refunded_count += 1
                for r in tx.refunds:
                    total_refunded += r.amount

        now = datetime.now(timezone.utc)
        for b_ref, tx_list in tx_by_booking.items():
            booking = bookings.get(b_ref)
            successful_txs = [t for t in tx_list if t.status == PaymentStatus.SUCCESSFUL]

            # 1. Multiple Successful Payments for 1 Booking
            if len(successful_txs) > 1:
                duplicate_payments.append({
                    "booking_ref": b_ref,
                    "count": len(successful_txs),
                    "payments": [
                        {"tx_ref": t.transaction_ref, "gateway_payment_id": t.gateway_payment_id, "amount": t.amount}
                        for t in successful_txs
                    ]
                })
                anomalies.append({
                    "type": "DUPLICATE_PAYMENTS",
                    "severity": "CRITICAL",
                    "booking_ref": b_ref,
                    "message": f"Booking '{b_ref}' received {len(successful_txs)} successful payments."
                })

            # 2. Paid Transaction but Booking is PENDING
            if successful_txs and booking and booking.status == BookingStatus.PENDING:
                anomalies.append({
                    "type": "PAID_WITHOUT_CONFIRMATION",
                    "severity": "HIGH",
                    "booking_ref": b_ref,
                    "message": f"Payment is SUCCESSFUL ({successful_txs[0].transaction_ref}) but booking status is PENDING."
                })

            # 3. Amount Mismatch
            if successful_txs and booking:
                for stx in successful_txs:
                    if abs(float(stx.amount) - float(booking.total_amount)) > 0.01:
                        anomalies.append({
                            "type": "AMOUNT_MISMATCH",
                            "severity": "HIGH",
                            "booking_ref": b_ref,
                            "message": f"Tx amount (₹{stx.amount}) != Booking amount (₹{booking.total_amount})."
                        })

            # 4. Currency Mismatch
            if successful_txs and booking:
                for stx in successful_txs:
                    if (stx.currency or "INR").upper() != (booking.currency or "INR").upper():
                        anomalies.append({
                            "type": "CURRENCY_MISMATCH",
                            "severity": "MEDIUM",
                            "booking_ref": b_ref,
                            "message": f"Tx currency ({stx.currency}) != Booking currency ({booking.currency})."
                        })

            # 5. Stale Pending Transactions (> 24 hours)
            for ptx in tx_list:
                if ptx.status == PaymentStatus.PENDING:
                    tx_age = (now - ptx.created_at).total_seconds() / 3600.0 if ptx.created_at else 0
                    if tx_age > 24.0:
                        anomalies.append({
                            "type": "STALE_PENDING",
                            "severity": "LOW",
                            "booking_ref": b_ref,
                            "message": f"Transaction '{ptx.transaction_ref}' has been PENDING for {round(tx_age, 1)} hours."
                        })

        # 6. Booking Confirmed without any Successful Payment
        for b_ref, booking in bookings.items():
            if booking.status == BookingStatus.CONFIRMED:
                matching_txs = tx_by_booking.get(b_ref, [])
                has_success = any(t.status == PaymentStatus.SUCCESSFUL for t in matching_txs)
                if not has_success:
                    anomalies.append({
                        "type": "CONFIRMED_WITHOUT_PAYMENT",
                        "severity": "CRITICAL",
                        "booking_ref": b_ref,
                        "message": f"Booking '{b_ref}' is marked CONFIRMED without any verified SUCCESSFUL payment transaction."
                    })

        return {
            "summary": {
                "total_transactions": len(transactions),
                "total_settled_inr": round(total_settled, 2),
                "total_refunded_inr": round(total_refunded, 2),
                "paid_transactions": paid_count,
                "refunded_transactions": refunded_count,
                "total_anomalies": len(anomalies),
                "critical_anomalies": len([a for a in anomalies if a["severity"] == "CRITICAL"]),
            },
            "anomalies": anomalies,
            "duplicate_payments": duplicate_payments
        }

    @classmethod
    def reconcile_booking_payment(cls, db: Session, booking_ref: str) -> Dict[str, Any]:
        """
        Queries Razorpay directly for live order/payment status and synchronizes the local database state.
        """
        from app.models.schema import Booking, BookingStatus
        from app.providers.razorpay_provider import razorpay_provider

        booking = db.scalar(select(Booking).where(Booking.booking_ref == booking_ref))
        if not booking:
            raise ValueError(f"Booking '{booking_ref}' not found.")

        tx = db.scalar(
            select(PaymentTransaction)
            .where(PaymentTransaction.entity_id == booking_ref)
            .order_by(desc(PaymentTransaction.created_at))
        )

        gateway_order_id = tx.gateway_payment_id if tx else None
        if not gateway_order_id:
            raise ValueError(f"No gateway order ID found for booking '{booking_ref}'.")

        order_res = razorpay_provider.fetch_order(gateway_order_id)
        if not order_res.get("success"):
            raise ValueError(f"Failed to fetch order from Razorpay: {order_res.get('error')}")

        order_status = order_res.get("status")
        if order_status == "paid":
            result = cls.handle_verified_payment(
                db,
                event_name="RECONCILE_SYNC",
                gateway_provider="RAZORPAY",
                order_id=gateway_order_id,
                booking_ref=booking_ref,
                channel="web"
            )
            return {"reconciled": True, "status": "CONFIRMED", "details": result}

        return {
            "reconciled": True,
            "status": booking.status.value if hasattr(booking.status, "value") else str(booking.status),
            "gateway_status": order_status,
            "message": f"Gateway order status is '{order_status}'."
        }

    @classmethod
    def expire_stale_transactions(cls, db: Session, max_age_hours: float = 24.0) -> int:
        """
        Finds all PENDING payment transactions created more than max_age_hours ago
        and transitions them to EXPIRED. Also marks stale WhatsApp WAITING_PAYMENT
        conversations so abandoned links do not linger forever.
        """
        from datetime import timedelta

        threshold = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
        stale_txs = list(
            db.scalars(
                select(PaymentTransaction).where(
                    PaymentTransaction.status == PaymentStatus.PENDING,
                    PaymentTransaction.created_at <= threshold
                )
            ).all()
        )

        count = 0
        for tx in stale_txs:
            tx.status = PaymentStatus.EXPIRED
            tx.updated_at = datetime.now(timezone.utc)
            count += 1

        wa_expired = 0
        try:
            from app.models.whatsapp_models import WhatsAppConversation
            stale_convs = list(
                db.scalars(
                    select(WhatsAppConversation).where(
                        WhatsAppConversation.current_state.in_(["WAITING_PAYMENT", "PENDING_PAYMENT"]),
                        WhatsAppConversation.updated_at <= threshold,
                    )
                ).all()
            )
            for conv in stale_convs:
                conv.payment_status = "EXPIRED"
                # Keep booking PENDING; do not invent CANCELLED without customer action.
                # Move chat out of payment wait so the customer can restart cleanly.
                conv.current_state = "CATEGORY_SELECTION"
                conv.updated_at = datetime.now(timezone.utc)
                wa_expired += 1
        except Exception:
            logger.exception("[PaymentService] Stale WhatsApp WAITING_PAYMENT cleanup failed")

        if count > 0 or wa_expired > 0:
            db.commit()
            logger.info(
                "[PaymentService] Expired %s stale PENDING payment transactions and %s WhatsApp payment waits older than %sh.",
                count,
                wa_expired,
                max_age_hours,
            )

        return count

    @classmethod
    def get_payment_ledger(
        cls,
        db: Session,
        search: Optional[str] = None,
        provider: Optional[str] = None,
        status: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Returns structured enterprise payment ledger entries from PaymentTransaction."""
        from app.models.schema import Booking

        stmt = select(PaymentTransaction).order_by(desc(PaymentTransaction.created_at))
        if status and status != "all":
            try:
                p_status = PaymentStatus(status.upper())
                stmt = stmt.where(PaymentTransaction.status == p_status)
            except ValueError:
                pass

        if provider and provider != "all":
            stmt = stmt.where(PaymentTransaction.gateway_provider.ilike(f"%{provider}%"))

        transactions = list(db.scalars(stmt.limit(500)).all())
        booking_refs = {t.entity_id for t in transactions if t.entity_id}
        bookings_map = {
            b.booking_ref: b for b in db.scalars(select(Booking).where(Booking.booking_ref.in_(booking_refs))).all()
        }

        results = []
        for t in transactions:
            b = bookings_map.get(t.entity_id)
            inv = t.invoices[0] if t.invoices else None
            ref_rec = t.refunds[0] if t.refunds else None

            contact_name = b.passenger_name if b else "N/A"
            contact_email = b.passenger_email if b else "N/A"

            if search:
                s_lower = search.lower()
                matches = (
                    s_lower in t.transaction_ref.lower() or
                    s_lower in (t.gateway_payment_id or "").lower() or
                    s_lower in (t.entity_id or "").lower() or
                    s_lower in contact_name.lower() or
                    s_lower in contact_email.lower()
                )
                if not matches:
                    continue

            results.append({
                "id": str(t.id),
                "payment_id": t.transaction_ref,
                "booking_id": str(b.id) if b else (t.entity_id or "N/A"),
                "booking_ref": b.booking_ref if b else (t.entity_id or "N/A"),
                "contact_name": contact_name,
                "contact_email": contact_email,
                "provider": t.gateway_provider.lower(),
                "provider_payment_id": t.gateway_payment_id or "N/A",
                "amount": float(t.amount),
                "currency": t.currency,
                "status": t.status.value.lower() if hasattr(t.status, "value") else str(t.status).lower(),
                "transaction_time": t.created_at.isoformat() if t.created_at else None,
                "created_at": t.created_at.isoformat() if t.created_at else None,
                "receipt_number": inv.invoice_number if inv else None,
                "receipt_path": inv.pdf_url if inv else None,
                "refund_ref": ref_rec.refund_ref if ref_rec else None,
                "refund_amount": float(ref_rec.amount) if ref_rec else None,
                "is_duplicate": t.is_duplicate
            })

        return results

    @classmethod
    def get_customer_payment_history(
        cls,
        db: Session,
        user_id: Optional[str] = None,
        email: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Returns customer payment history securely filtered by user ID or email."""
        from app.models.schema import Booking
        from sqlalchemy import or_

        conditions = []
        if user_id:
            conditions.append(PaymentTransaction.customer_id == str(user_id))
        if email:
            bookings_by_email = list(db.scalars(select(Booking.booking_ref).where(Booking.passenger_email == email)).all())
            if bookings_by_email:
                conditions.append(PaymentTransaction.entity_id.in_(bookings_by_email))

        if not conditions:
            return []

        transactions = list(
            db.scalars(
                select(PaymentTransaction)
                .where(or_(*conditions))
                .order_by(desc(PaymentTransaction.created_at))
            ).all()
        )

        results = []
        for t in transactions:
            b = db.scalar(select(Booking).where(Booking.booking_ref == t.entity_id))
            results.append({
                "id": str(t.id),
                "transaction_ref": t.transaction_ref,
                "booking_ref": b.booking_ref if b else t.entity_id,
                "route": f"{b.origin_code} → {b.dest_code}" if b and b.origin_code and b.dest_code else "Airport Service",
                "service_type": b.service_type if b else "Concierge",
                "provider": t.gateway_provider,
                "amount": float(t.amount),
                "currency": t.currency,
                "status": t.status.value if hasattr(t.status, "value") else str(t.status),
                "transaction_time": t.created_at.isoformat() if t.created_at else None
            })

        return results

