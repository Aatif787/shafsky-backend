"""
Phase 22 — provenance-based test-data cleanup.

Identifies records created by pytest fixtures / development generators using
positive evidence (test booking-ref prefixes, fixture emails, explicit test
markers). Soft-deletes bookings (sets deleted_at). Does not delete payment
transactions, invoices, notification history, or audit logs.

Default is dry-run. Destructive mode requires --execute.

Usage:
  python -m app.scripts.cleanup_test_data
  python -m app.scripts.cleanup_test_data --dry-run
  python -m app.scripts.cleanup_test_data --execute
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import bindparam, select, text
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models.schema import Booking
from app.models.charter_models import PrivateCharterRequest
from app.models.operations_models import OperationsQueue
from app.models.payment import PaymentTransaction, Invoice


# Booking-ref prefixes used only by pytest (production uses SHF-YYYYMMDD-xxxx).
TEST_REF_PREFIXES: Tuple[str, ...] = (
    "SHF-INV-",
    "SHF-TEST-",
    "SHF-COMM-",
    "SHF-REC-",
    "SHF-E2E-",
    "SHF-DBL-",
    "SHF-RTRY-",
    "SHF-CONF-",
    "SHF-NOTIF-",
    "SHF-WA-",
    "SHF-DEL-",
    "SHF-AUTH-",
)

# Emails hardcoded in tests/*.py fixtures. Never treat real Gmail as test.
TEST_EMAILS = frozenset(
    e.lower()
    for e in (
        "arthur@galaxy.com",
        "guest@shafsky.com",
        "guest@example.com",
        "bob@example.com",
        "test@shafsky.com",
        "alice@example.com",
        "john@shafsky.com",
        "jane@shafsky.com",
        "eleanor@example.com",
        "david@example.com",
        "charles@example.com",
        "fiona@example.com",
        "r1@example.com",
        "diana@example.com",
        "john@example.com",
        "charlie@example.com",
        "eve@example.com",
        "ok@shafsky.com",
        "amt@shafsky.com",
        "pend@shafsky.com",
        "unpaid@shafsky.com",
        "johndoe@shafskyaviation.com",
        "lock@shafskyaviation.com",
        "team@example.com",
        "retry@shafskyaviation.com",
        "dup@shafskyaviation.com",
        "idemp@shafskyaviation.com",
        "alex@shafskyaviation.com",
        "henry@sterling.com",
        "janesmith@shafskyaviation.com",
        "concurrent@shafskyaviation.com",
        "smoke_atq@shafsky.com",
        "randolph@shafsky.com",
        "single@example.com",
        "dep@example.com",
        "arr@example.com",
        "passenger@shafskyaviation.com",
        "uat1@shafsky.com",
        "uat2@shafsky.com",
        "uat3@shafsky.com",
        "uat4@shafsky.com",
        "smoke@shafsky.com",
        "smoke_bom@shafsky.com",
        "smoke_amd@shafsky.com",
        "smoke_del@shafsky.com",
        "smoke_blr@shafsky.com",
        "qa@shafsky.com",
        "dom_arr@shafsky.com",
        "dom_dep@shafsky.com",
        "intl_dep@shafsky.com",
        "intl_arr@shafsky.com",
        "transit@example.com",
        "transit@shafsky.com",
        "john.doe@shafskytest.com",
        "ravi.kumar@shafskytest.com",
        "t@test.com",
        "sec@test.com",
        "sterling@vip.aero",
        "elena.rostova@monaco.mc",
        "zhang@diplomatic.cn",
    )
)

TEST_EMAIL_DOMAINS = frozenset(
    {
        "example.com",
        "galaxy.com",
        "shafskytest.com",
        "vip.aero",
        "diplomatic.cn",
        "monaco.mc",
        "sterling.com",
    }
)

# Explicit test markers on booking_reference / operations queue.
TEST_REFERENCE_MARKERS = frozenset({"SHK-20260806-TEST"})

KEEP_EMAILS = frozenset(
    e.lower()
    for e in (
        "aarizfarooqui786@gmail.com",
        "aariz3732@gmail.com",
        "aariz.khan@gmail.com",
        "iqbalhussain1977@gmail.com",
        "bhagwati.shafsky41@gmail.com",
        "shafskyaviation.social@gmail.com",
        "hahmad8864@gmail.com",
        "john.doe@gmail.com",
        "vikram@shafskyaviation.com",
        "single.traveler@gmail.com",
    )
)


def _email_domain(email: str) -> str:
    if not email or "@" not in email:
        return ""
    return email.rsplit("@", 1)[-1].strip().lower()


def classify_booking(booking: Booking) -> Optional[Tuple[str, str]]:
    """Return (source, reason) if the row is a verified test record, else None."""
    email = (booking.passenger_email or "").strip().lower()
    ref = booking.booking_ref or ""

    if email in KEEP_EMAILS:
        return None

    if ref in TEST_REFERENCE_MARKERS:
        return ("explicit test marker", f"booking_ref={ref}")

    for prefix in TEST_REF_PREFIXES:
        if ref.startswith(prefix):
            return ("pytest fixture prefix", f"booking_ref starts with {prefix}")

    if email in TEST_EMAILS:
        return ("pytest fixture email", f"email={email}")

    domain = _email_domain(email)
    if domain in TEST_EMAIL_DOMAINS:
        return ("pytest fixture domain", f"domain={domain}")

    return None


def classify_charter(req: PrivateCharterRequest) -> Optional[Tuple[str, str]]:
    email = (req.email or "").strip().lower()
    if email in KEEP_EMAILS:
        return None
    if email in TEST_EMAILS:
        return ("pytest charter fixture", f"email={email}")
    domain = _email_domain(email)
    if domain in TEST_EMAIL_DOMAINS:
        return ("pytest charter fixture domain", f"domain={domain}")
    return None


def collect(db: Session) -> Dict[str, Any]:
    bookings = list(db.scalars(select(Booking).where(Booking.deleted_at.is_(None))).all())
    verified_bookings: List[Dict[str, Any]] = []
    for b in bookings:
        hit = classify_booking(b)
        if not hit:
            continue
        source, reason = hit
        verified_bookings.append(
            {
                "id": str(b.id),
                "booking_ref": b.booking_ref,
                "passenger_name": b.passenger_name,
                "email": b.passenger_email,
                "source": source,
                "reason": reason,
            }
        )

    charter_hits: List[Dict[str, Any]] = []
    try:
        charters = list(db.scalars(select(PrivateCharterRequest)).all())
    except Exception:
        charters = []
    for r in charters:
        hit = classify_charter(r)
        if not hit:
            continue
        source, reason = hit
        charter_hits.append(
            {
                "id": str(r.id),
                "request_reference": r.request_reference,
                "customer_name": r.customer_name,
                "email": r.email,
                "source": source,
                "reason": reason,
            }
        )

    ops_hits: List[Dict[str, Any]] = []
    try:
        ops_rows = list(db.scalars(select(OperationsQueue)).all())
    except Exception:
        ops_rows = []
    for row in ops_rows:
        if row.booking_reference in TEST_REFERENCE_MARKERS:
            ops_hits.append(
                {
                    "id": str(row.id),
                    "booking_reference": row.booking_reference,
                    "source": "explicit test marker",
                    "reason": "operations_queue.booking_reference is a test marker",
                }
            )

    return {
        "bookings": verified_bookings,
        "charter": charter_hits,
        "operations_queue": ops_hits,
        "live_booking_count": len(bookings),
        "remaining_bookings": len(bookings) - len(verified_bookings),
        "financial": collect_financial(db),
    }


def _classified_test_refs(db: Session) -> List[str]:
    """Booking refs classified as test, including already soft-deleted rows."""
    refs: List[str] = []
    for booking in db.scalars(select(Booking)).all():
        if classify_booking(booking):
            refs.append(booking.booking_ref)
    return refs


def collect_financial(db: Session) -> Dict[str, Any]:
    test_refs = _classified_test_refs(db)
    payments: List[Dict[str, Any]] = []
    invoices: List[Dict[str, Any]] = []
    notifications: List[Dict[str, Any]] = []
    notification_logs: List[Dict[str, Any]] = []
    ambiguous_payments = 0

    if test_refs:
        pay_rows = list(
            db.scalars(select(PaymentTransaction).where(PaymentTransaction.entity_id.in_(test_refs))).all()
        )
        pay_ids = [row.id for row in pay_rows]
        for row in pay_rows:
            payments.append(
                {
                    "id": str(row.id),
                    "transaction_ref": row.transaction_ref,
                    "entity_id": row.entity_id,
                    "amount": float(row.amount or 0),
                    "status": row.status.value if hasattr(row.status, "value") else str(row.status),
                    "gateway_provider": row.gateway_provider,
                    "source": "payment.entity_id matches classified test booking_ref",
                }
            )
        if pay_ids:
            for inv in db.scalars(select(Invoice).where(Invoice.transaction_id.in_(pay_ids))).all():
                invoices.append(
                    {
                        "id": str(inv.id),
                        "invoice_number": inv.invoice_number,
                        "transaction_id": str(inv.transaction_id),
                        "source": "invoice.transaction_id belongs to classified test payment",
                    }
                )

        notif_sql = text(
            """
            SELECT id, recipient_email, template_type,
                   COALESCE(payload->>'booking_ref', payload->>'bookingRef', '') AS booking_ref
            FROM notification_records
            WHERE COALESCE(payload->>'booking_ref', payload->>'bookingRef', '') IN :refs
               OR lower(recipient_email) IN :emails
            """
        ).bindparams(bindparam("refs", expanding=True), bindparam("emails", expanding=True))
        try:
            email_list = tuple(TEST_EMAILS) or ("__none__",)
            refs_tuple = tuple(test_refs)
            for row in db.execute(notif_sql, {"refs": refs_tuple, "emails": email_list}).mappings():
                email = (row["recipient_email"] or "").strip().lower()
                if email in KEEP_EMAILS:
                    continue
                notifications.append(
                    {
                        "id": str(row["id"]),
                        "recipient_email": row["recipient_email"],
                        "template_type": row["template_type"],
                        "booking_ref": row["booking_ref"],
                        "source": "notification payload booking_ref or pytest fixture email",
                    }
                )
        except Exception as exc:
            print(f"(notification_records scan skipped: {exc})")

        try:
            log_sql = text(
                """
                SELECT id, booking_ref, recipient, channel
                FROM notification_logs
                WHERE booking_ref IN :refs
                """
            ).bindparams(bindparam("refs", expanding=True))
            for row in db.execute(log_sql, {"refs": tuple(test_refs)}).mappings():
                notification_logs.append(
                    {
                        "id": str(row["id"]),
                        "booking_ref": row["booking_ref"],
                        "recipient": row["recipient"],
                        "source": "notification_logs.booking_ref matches classified test booking",
                    }
                )
        except Exception as exc:
            print(f"(notification_logs scan skipped: {exc})")

        ambiguous_payments = db.execute(
            text(
                """
                SELECT COUNT(*) FROM payment_transactions
                WHERE entity_id IS NOT NULL
                  AND entity_id <> ''
                  AND entity_id NOT IN (SELECT booking_ref FROM bookings WHERE deleted_at IS NULL)
                  AND entity_id NOT IN (SELECT booking_ref FROM bookings WHERE deleted_at IS NOT NULL)
                """
            )
        ).scalar() or 0

    return {
        "test_booking_refs": len(test_refs),
        "payments": payments,
        "invoices": invoices,
        "notifications": notifications,
        "notification_logs": notification_logs,
        "ambiguous_unmatched_payments": int(ambiguous_payments),
    }


def print_plan(plan: Dict[str, Any]) -> None:
    bookings = plan["bookings"]
    print(f"Found {len(bookings)} verified test bookings")
    print()
    for row in bookings:
        print(row["booking_ref"])
        print(f"  source: {row['source']}")
        print(f"  reason: {row['reason']}")
        print(f"  name:   {row['passenger_name']}")
        print(f"  email:  {row['email']}")
        print()
    print(f"Found {len(plan['charter'])} verified test charter requests")
    for row in plan["charter"]:
        print(f"  {row['request_reference']}  {row['email']}  ({row['source']})")
    print()
    print(f"Found {len(plan['operations_queue'])} verified test operations-queue rows")
    for row in plan["operations_queue"]:
        print(f"  {row['booking_reference']}  ({row['source']})")
    print()
    print(f"Live bookings before cleanup: {plan['live_booking_count']}")
    print(f"Live bookings after cleanup:  {plan['remaining_bookings']}")

    financial = plan.get("financial") or {}
    print()
    print("=== FINANCIAL (classified test bookings only) ===")
    print(f"Classified test booking refs: {financial.get('test_booking_refs', 0)}")
    print(f"Verified test payments: {len(financial.get('payments') or [])}")
    print(f"Verified test invoices: {len(financial.get('invoices') or [])}")
    print(f"Verified test notification_records: {len(financial.get('notifications') or [])}")
    print(f"Verified test notification_logs: {len(financial.get('notification_logs') or [])}")
    print(f"Ambiguous unmatched payments (not deleted): {financial.get('ambiguous_unmatched_payments', 0)}")
    for row in (financial.get("payments") or [])[:80]:
        print(
            f"  {row['transaction_ref']}  {row['entity_id']}  {row['status']}  "
            f"{row['gateway_provider']}  {row['amount']}"
        )
    extra = len(financial.get("payments") or []) - 80
    if extra > 0:
        print(f"  ... {extra} more payment rows")


def execute_cleanup(db: Session, plan: Dict[str, Any], include_financial: bool = False) -> None:
    now = datetime.now(timezone.utc)
    booking_ids = [row["id"] for row in plan["bookings"]]
    if booking_ids:
        targets = list(
            db.scalars(select(Booking).where(Booking.id.in_(booking_ids), Booking.deleted_at.is_(None))).all()
        )
        for b in targets:
            b.deleted_at = now
            meta = dict(b.metadata_json or {})
            meta["test_data_cleanup"] = {
                "at": now.isoformat(),
                "phase": "22",
            }
            b.metadata_json = meta

    charter_ids = [row["id"] for row in plan["charter"]]
    if charter_ids:
        for r in db.scalars(select(PrivateCharterRequest).where(PrivateCharterRequest.id.in_(charter_ids))).all():
            db.delete(r)

    ops_ids = [row["id"] for row in plan["operations_queue"]]
    if ops_ids:
        for row in db.scalars(select(OperationsQueue).where(OperationsQueue.id.in_(ops_ids))).all():
            db.delete(row)

    if include_financial:
        financial = plan.get("financial") or {}
        pay_ids = [row["id"] for row in financial.get("payments") or []]
        if pay_ids:
            for txn in db.scalars(select(PaymentTransaction).where(PaymentTransaction.id.in_(pay_ids))).all():
                db.delete(txn)
        notif_ids = [row["id"] for row in financial.get("notifications") or []]
        if notif_ids:
            db.execute(
                text("DELETE FROM notification_records WHERE id IN :ids").bindparams(
                    bindparam("ids", expanding=True)
                ),
                {"ids": tuple(notif_ids)},
            )
        log_ids = [row["id"] for row in financial.get("notification_logs") or []]
        if log_ids:
            db.execute(
                text("DELETE FROM notification_logs WHERE id IN :ids").bindparams(
                    bindparam("ids", expanding=True)
                ),
                {"ids": tuple(log_ids)},
            )

    db.commit()


def integrity_report(db: Session) -> None:
    print()
    print("=== DATABASE INTEGRITY ===")
    checks = [
        ("bookings (live)", "SELECT COUNT(*) FROM bookings WHERE deleted_at IS NULL"),
        ("bookings (soft-deleted)", "SELECT COUNT(*) FROM bookings WHERE deleted_at IS NOT NULL"),
        ("payment_transactions", "SELECT COUNT(*) FROM payment_transactions"),
        ("invoices", "SELECT COUNT(*) FROM invoices"),
        ("notification_records", "SELECT COUNT(*) FROM notification_records"),
        ("notification_logs", "SELECT COUNT(*) FROM notification_logs"),
        ("audit_logs", "SELECT COUNT(*) FROM audit_logs"),
        ("operations_queue", "SELECT COUNT(*) FROM operations_queue"),
        ("private_charter_requests", "SELECT COUNT(*) FROM private_charter_requests"),
    ]
    for label, sql in checks:
        try:
            n = db.execute(text(sql)).scalar()
            print(f"  {label}: {n}")
        except Exception as exc:
            print(f"  {label}: unavailable ({exc})")


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Provenance-based test data cleanup")
    parser.add_argument("--dry-run", action="store_true", help="Print plan only (default)")
    parser.add_argument("--execute", action="store_true", help="Apply soft-delete for verified test bookings")
    parser.add_argument(
        "--financial",
        action="store_true",
        help="Also remove payments/invoices/notifications tied to classified test bookings",
    )
    parser.add_argument("--json", action="store_true", help="Emit plan as JSON")
    args = parser.parse_args(argv)

    if args.execute and args.dry_run:
        print("Choose either --dry-run or --execute, not both.", file=sys.stderr)
        return 2

    execute = bool(args.execute)
    db = SessionLocal()
    try:
        plan = collect(db)
        if args.json:
            print(json.dumps(plan, indent=2, default=str))
        else:
            print_plan(plan)

        if not execute:
            print()
            print("No data modified.")
            integrity_report(db)
            return 0

        print()
        if args.financial:
            print("Executing provenance cleanup (bookings + classified test payments/invoices/notifications)...")
        else:
            print("Executing provenance cleanup (soft-delete bookings; payments/invoices/audit preserved)...")
        execute_cleanup(db, plan, include_financial=args.financial)
        print("Cleanup committed.")
        integrity_report(db)
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
