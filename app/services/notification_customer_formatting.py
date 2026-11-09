"""
Extended WhatsApp enquiry, booking confirmation,customer template, and invoice
delivery helpers for NotificationService.
"""

import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.models.schema import NotificationRecord, NotificationStatus


def _ns():
    import app.services.notification_service as ns_mod
    return ns_mod


# Meta Cloud API rejects any template body parameter that contains a new-line or
# tab character, or more than four consecutive spaces. A formatted multi-line
# alert therefore cannot be used directly as {{1}} -- the request is accepted by
# HTTP but Meta answers 132018 / 132000 and the customer never receives it.
TEMPLATE_PARAM_MAX_CHARS = 900  # leaves headroom inside Meta's 1024-char BODY limit


def build_template_parameter(value: Any, max_chars: int = TEMPLATE_PARAM_MAX_CHARS) -> str:
    """Return a single-line string that is safe to place inside a template variable.

    Enforces the three documented Meta rules for template parameters:
    no new-line characters, no tabs, and never more than four consecutive spaces.
    """
    text = str(value or "")
    text = text.replace("\r\n", " ").replace("\r", " ").replace("\n", " ").replace("\t", " ")
    text = re.sub(r" {2,}", " ", text)
    text = text.strip()
    if max_chars and len(text) > max_chars:
        text = text[: max_chars - 3].rstrip() + "..."
    return text


def _format_datetime(value: Any, airport_code: Optional[str] = None) -> Optional[str]:
    """Renders a booking date as a clean, human-readable local value."""
    if value is None:
        return None
    from datetime import datetime
    if isinstance(value, datetime):
        dt = value
    else:
        text = str(value).strip()
        if not text:
            return None
        if len(text) == 10 and text[4] == "-" and text[7] == "-":
            try:
                parsed_d = datetime.strptime(text, "%Y-%m-%d")
                return parsed_d.strftime("%d %b %Y")
            except ValueError:
                return text
        try:
            normalized = text.replace("Z", "+00:00").replace(" ", "T")
            dt = datetime.fromisoformat(normalized)
        except (ValueError, TypeError):
            return text[:40]

    try:
        from zoneinfo import ZoneInfo
        from app.services.booking_cutoff import lookup_airport_timezone

        tz_name = lookup_airport_timezone(None, airport_code) if airport_code else "Asia/Kolkata"
        target_tz = ZoneInfo(tz_name)
        if dt.tzinfo is not None:
            dt = dt.astimezone(target_tz)
    except Exception:
        pass

    return dt.strftime("%d %b %Y, %H:%M")



def build_customer_summary(
    *,
    reference_label: str,
    reference: str,
    guest_name: Optional[str] = None,
    service: Optional[str] = None,
    airport: Optional[str] = None,
    location_label: str = "Airport",
    date_time: Optional[str] = None,
    flight_num: Optional[str] = None,
    passengers: Optional[Any] = None,
    amount: Optional[float] = None,
    currency: Optional[str] = None,
    status_label: Optional[str] = None,
    invoice_number: Optional[str] = None,
    invoice_url: Optional[str] = None,
) -> str:
    """Builds the clean single-line summary used inside the approved template.

    The Meta body already introduces the booking, so this value starts with the
    reference and then reads as a scannable sequence of facts. No new-lines or
    pipe characters: only the middle dot separator.
    """
    parts: List[str] = [str(reference).strip()]

    if status_label:
        status = str(status_label).strip()
        if "enquir" in status.lower():
            status = "Enquiry received"
        elif status.upper().startswith("CONFIRMED"):
            status = "Confirmed"
        else:
            status = status.split("(")[0].strip().title()
        if status:
            parts.append(status)

    if guest_name:
        parts.append(str(guest_name).strip())
    if service:
        parts.append(str(service).strip())
    if airport:
        parts.append(str(airport).strip())
    if date_time:
        parts.append(str(date_time).strip())
    if flight_num:
        parts.append(f"Flight {str(flight_num).strip()}")

    try:
        pax = int(passengers) if passengers is not None else 0
    except (TypeError, ValueError):
        pax = 0
    if pax > 0:
        parts.append(f"{pax} guest" + ("" if pax == 1 else "s"))

    if amount is not None:
        try:
            amount_value = float(amount)
        except (TypeError, ValueError):
            amount_value = 0.0
        if amount_value > 0:
            code = (currency or "INR").upper()
            if code == "INR":
                parts.append(f"₹{amount_value:,.0f}")
            else:
                parts.append(f"{code} {amount_value:,.0f}")

    if invoice_number:
        parts.append(f"Invoice {str(invoice_number).strip()}")
    if invoice_url and str(invoice_url).strip().startswith("https://"):
        parts.append(f"Invoice PDF: {str(invoice_url).strip()}")

    return build_template_parameter(" · ".join(part for part in parts if part))


