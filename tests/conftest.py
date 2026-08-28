"""
Pytest bootstrap — isolate tests from the production Neon database.

This module is imported before test files. It rewrites DATABASE_URL when
TEST_DATABASE_URL is set, and refuses to run against the known production
Neon host otherwise.
"""

from __future__ import annotations

import os
import sys

from dotenv import load_dotenv

PROD_HOST_MARKERS = (
    "neon.tech",
    "ep-flat-frost",
)


def _is_production_url(url: str) -> bool:
    lowered = (url or "").lower()
    return any(marker in lowered for marker in PROD_HOST_MARKERS)


def configure_test_database() -> None:
    load_dotenv()

    allow_prod = os.getenv("SHAFSKY_ALLOW_PROD_DB_TESTS", "").strip().lower() in {
        "1",
        "true",
        "yes",
    }
    test_url = (os.getenv("TEST_DATABASE_URL") or "").strip()
    current = (os.getenv("DATABASE_URL") or "").strip()

    os.environ["TESTING"] = "1"

    if test_url:
        os.environ["DATABASE_URL"] = test_url
        return

    if _is_production_url(current) and not allow_prod:
        sys.stderr.write(
            "\nRefusing to run pytest against the production Neon database.\n\n"
            "This suite inserts fixture bookings into whatever DATABASE_URL points at.\n"
            "Use an isolated Postgres database:\n\n"
            "  docker compose -f docker-compose.test.yml up -d\n"
            "  $env:TEST_DATABASE_URL = "
            "'postgresql://shafsky:shafsky@127.0.0.1:55432/shafsky_test'\n"
            "  python -m pytest -q\n\n"
            "Emergency override only (will pollute live data):\n"
            "  $env:SHAFSKY_ALLOW_PROD_DB_TESTS = '1'\n\n"
        )
        raise SystemExit(2)


configure_test_database()
