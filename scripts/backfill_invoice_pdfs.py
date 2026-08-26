"""
Safe backfill: upload Tax Invoice PDFs for PAID invoices with null pdf_url.

Does NOT:
- create payments
- change booking/payment/invoice status amounts
- send email or WhatsApp (send_notifications=False)

Usage (from shafsky-backend-main, venv active):
  python -m scripts.backfill_invoice_pdfs
  python -m scripts.backfill_invoice_pdfs --limit 50
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Load .env before app imports
for line in (ROOT / ".env").read_text(encoding="utf-8", errors="ignore").splitlines():
    line = line.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    k, v = line.split("=", 1)
    os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill invoice PDFs without notifications")
    parser.add_argument("--limit", type=int, default=100, help="Max invoices to process")
    args = parser.parse_args()

    from sqlalchemy import text
    from app.database import SessionLocal
    from app.services.pdf_service import fulfill_paid_invoice

    ok = fail = skip = 0
    # Fresh session for listing — Neon pooler can drop long-lived connections.
    list_db = SessionLocal()
    try:
        rows = list_db.execute(
            text(
                """
                SELECT cast(i.id as text) AS id, i.invoice_number
                FROM invoices i
                WHERE cast(i.status as text) = 'PAID'
                  AND i.pdf_url IS NULL
                ORDER BY coalesce(i.paid_at, i.issued_at) DESC NULLS LAST
                LIMIT :lim
                """
            ),
            {"lim": max(1, args.limit)},
        ).mappings().all()
    finally:
        list_db.close()

    print(f"candidates={len(rows)} limit={args.limit}")
    for row in rows:
        # One session per invoice so a mid-run disconnect cannot poison the pool.
        db = SessionLocal()
        try:
            result = fulfill_paid_invoice(db, row["id"], send_notifications=False)
            if result.get("pdf_url"):
                ok += 1
                print(f"OK {row['invoice_number']} -> {result.get('pdf_url')}")
            elif result.get("success"):
                skip += 1
                print(f"SKIP {row['invoice_number']} success_without_pdf={result}")
            else:
                fail += 1
                print(f"FAIL {row['invoice_number']} {result}")
        except Exception as exc:
            fail += 1
            print(f"FAIL {row['invoice_number']} exception={type(exc).__name__}: {exc}")
            try:
                db.rollback()
            except Exception:
                pass
        finally:
            db.close()

    print(f"done ok={ok} skip={skip} fail={fail}")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
