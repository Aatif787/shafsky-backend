"""
Move currently live dashboard bookings into the recycle bin.

Default is dry-run. --execute recycles (soft-delete + actor). --purge
then permanently deletes those rows after they are in the bin.

Usage:
  python -m app.scripts.purge_live_dashboard_bookings
  python -m app.scripts.purge_live_dashboard_bookings --execute
  python -m app.scripts.purge_live_dashboard_bookings --execute --purge
"""

from __future__ import annotations

import argparse
import sys
from typing import Any, Dict, List

from sqlalchemy import func, select

from app.database import SessionLocal
from app.models.schema import Booking
from app.services.booking_recycle_service import BookingRecycleService

SYSTEM_ACTOR: Dict[str, Any] = {
    "email": "system:ops-dashboard-cleanup",
    "sub": "system:ops-dashboard-cleanup",
    "role": "SYSTEM",
}


def main() -> int:
    parser = argparse.ArgumentParser(description="Recycle (and optionally purge) live dashboard bookings.")
    parser.add_argument("--execute", action="store_true", help="Move live bookings into the recycle bin.")
    parser.add_argument(
        "--purge",
        action="store_true",
        help="After recycling, permanently delete those bookings from the database.",
    )
    args = parser.parse_args()

    db = SessionLocal()
    try:
        live = list(
            db.scalars(
                select(Booking)
                .where(Booking.deleted_at.is_(None))
                .order_by(Booking.created_at.desc())
            ).all()
        )
        print(f"Live bookings: {len(live)}")
        for booking in live:
            print(
                f"  {booking.booking_ref}  {booking.passenger_name}  "
                f"{booking.passenger_email}  {booking.status}"
            )

        if not args.execute:
            print("\nDry-run only. Re-run with --execute to move these into the recycle bin.")
            print("Add --purge to also permanently delete them after recycling.")
            return 0

        recycled: List[str] = []
        purged: List[str] = []
        errors: List[str] = []
        for booking in live:
            ref = booking.booking_ref
            try:
                BookingRecycleService.recycle_booking(db, ref, SYSTEM_ACTOR)
                recycled.append(ref)
                print(f"Recycled {ref}")
                if args.purge:
                    BookingRecycleService.purge_booking(db, ref, SYSTEM_ACTOR)
                    purged.append(ref)
                    print(f"Purged {ref}")
            except Exception as exc:
                db.rollback()
                errors.append(f"{ref}: {exc}")
                print(f"FAILED {ref}: {exc}")

        live_left = db.scalar(select(func.count()).select_from(Booking).where(Booking.deleted_at.is_(None))) or 0
        print(f"\nRecycled: {len(recycled)}")
        print(f"Purged: {len(purged)}")
        print(f"Errors: {len(errors)}")
        print(f"Live remaining: {live_left}")
        return 1 if errors else 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
