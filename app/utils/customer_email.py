"""Customer email validation shared across WhatsApp and web booking channels.

Reserved RFC 2606 documentation domains are rejected because Resend (and most ESP)
return HTTP 422 for them. This must not block payment confirmation for legacy rows.
"""

from __future__ import annotations

import re
from typing import Tuple

# Same pattern used by ServiceConfigService — keep in sync for booking forms.
EMAIL_REGEX = re.compile(r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$")

RESERVED_EMAIL_DOMAINS = frozenset({"example.com", "example.org", "example.net"})
PLACEHOLDER_EMAILS = frozenset(
    {
        "guest@example.com",
        "test@example.com",
        "demo@example.com",
        "name@example.com",
    }
)

REAL_EMAIL_HELP = (
    "Please enter your real email address so we can send your booking "
    "confirmation and invoice."
)
EMAIL_FORMAT_HELP = "Please enter a valid email address (e.g. name@customer-mail.test)."


def is_acceptable_customer_email(email: str) -> Tuple[bool, str]:
    """
    Returns (ok, reason) where reason is:
      'ok' | 'invalid_syntax' | 'reserved_or_placeholder'
    """
    clean = (email or "").strip()
    if not clean or not EMAIL_REGEX.match(clean):
        return False, "invalid_syntax"
    lowered = clean.lower()
    if lowered in PLACEHOLDER_EMAILS:
        return False, "reserved_or_placeholder"
    domain = lowered.rsplit("@", 1)[-1]
    if domain in RESERVED_EMAIL_DOMAINS:
        return False, "reserved_or_placeholder"
    return True, "ok"


def normalize_customer_email(email: str) -> str:
    return (email or "").strip()
