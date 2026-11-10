"""
Production WhatsApp Booking Notification Template Generator for Shafsky Aviation Services.

Implements the official Operations Alert card template:
- Clean, premium white & pale blue-grey background with subtle borders.
- Shafsky Aviation branding at top-left with vertical divider.
- Bold navy heading ("New Operations Alert") with subtitle ("Booking / Enquiry Received").
- Gold "PENDING" status badge at top-right.
- Two-column full-width details table with alternating row backgrounds and navy icons.
- Strict field ordering: Reference No., Service, Customer, Contact, Email,
  Passengers, Vehicle, Category, Route, Date & Time.
- Bottom Notes panel with information icon.
- Bottom-right aligned dark navy "View in Dashboard" button.
- 100% dynamic field extraction from actual booking/enquiry payloads with clean fallbacks.
"""

from html import escape
from datetime import datetime
from typing import Any, Dict, Optional, List, Tuple

from app.utils.service_display import (
    is_multi_service_placeholder,
    resolve_service_display_name,
)


def _safe(value: Any, default: str = "Not provided") -> str:
    """Escapes strings for safe HTML embedding, returning clean fallback if empty."""
    if value is None:
        return default
    text = str(value).strip()
    return escape(text) if text else default


def _format_datetime(value: Any) -> str:
    """Formats datetime string into '01 Oct 2026 at 10:01' or 'DD Mon YYYY' format."""
    if not value:
        return "Not provided"
    text = str(value).strip()
    # Try parsing ISO or space-separated datetime
    for fmt in (
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d",
    ):
        try:
            # Clean trailing Z
            clean_text = text.replace("Z", "+00:00") if "Z" in text else text
            dt = datetime.strptime(clean_text[:19], fmt[: len(clean_text[:19])])
            if len(text) > 10 and ("T" in text or " " in text or ":" in text):
                return dt.strftime("%d %b %Y at %H:%M")
            return dt.strftime("%d %b %Y")
        except Exception:
            continue
    return escape(text)


def extract_operations_alert_fields(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Dynamically extracts all fields from actual booking/enquiry database dictionaries
    or API response payloads with appropriate fallbacks.
    """
    details = data.get("details") if isinstance(data.get("details"), dict) else {}
    options = data.get("service_options") if isinstance(data.get("service_options"), dict) else {}
    metadata = data.get("metadata_json") if isinstance(data.get("metadata_json"), dict) else {}

    # 1. Reference No.
    ref = (
        data.get("booking_ref")
        or data.get("bookingRef")
        or data.get("reference")
        or "Not provided"
    )

    # 2. Service
    raw_service = (
        data.get("service_name")
        or data.get("serviceName")
        or data.get("service_type")
        or data.get("serviceType")
        or data.get("package")
        or data.get("service_category")
        or data.get("service")
        or "Not provided"
    )
    resolved_svc = resolve_service_display_name(raw_service, data)
    if is_multi_service_placeholder(resolved_svc):
        resolved_svc = data.get("service_category") or "Meet & Greet"
    service = str(resolved_svc).strip()

    # 3. Customer
    customer = (
        data.get("passenger_name")
        or data.get("passengerName")
        or data.get("customer_name")
        or data.get("customerName")
        or data.get("contact_name")
        or data.get("client_name")
        or "Not provided"
    )

    # 4. Contact
    contact = (
        data.get("passenger_phone")
        or data.get("passengerPhone")
        or data.get("phone")
        or data.get("contact_phone")
        or "Not provided"
    )

    # 5. Email
    email = (
        data.get("passenger_email")
        or data.get("passengerEmail")
        or data.get("email")
        or data.get("contact_email")
        or "Not provided"
    )

    # 6. Passengers
    raw_pax = (
        details.get("passenger_count")
        or details.get("passengers")
        or data.get("passenger_count")
        or data.get("passengerCount")
        or data.get("passengers")
        or data.get("pax_adults")
    )
    if raw_pax is not None and str(raw_pax).strip():
        try:
            pax = str(int(raw_pax))
        except (ValueError, TypeError):
            pax = str(raw_pax)
    else:
        pax = "Not provided"

    # 7. Vehicle (when applicable)
    raw_vehicle = (
        details.get("vehicle_preference")
        or details.get("vehicle_name")
        or details.get("vehicleName")
        or options.get("vehicle_name")
        or data.get("vehicle_name")
        or data.get("vehicle")
    )
    raw_vehicle_id = (
        details.get("vehicle_id")
        or details.get("vehicleId")
        or options.get("vehicle_id")
        or data.get("vehicle_id")
    )
    if raw_vehicle:
        if raw_vehicle_id and str(raw_vehicle_id).strip() and str(raw_vehicle_id).lower() not in str(raw_vehicle).lower():
            vehicle = f"{raw_vehicle} (ID: {raw_vehicle_id})"
        else:
            vehicle = str(raw_vehicle)
    else:
        vehicle = None

    # 8. Category (when applicable)
    category = (
        details.get("vehicle_category")
        or details.get("vehicleCategory")
        or options.get("vehicle_category")
        or data.get("vehicle_category")
        or data.get("service_category")
        or data.get("category")
    )

    # 9. Route
    origin = (
        data.get("origin_code")
        or data.get("originCode")
        or data.get("origin")
        or data.get("airport_code")
        or data.get("airportCode")
        or details.get("pickup_location")
        or details.get("origin")
        or details.get("from_location")
    )
    dest = (
        data.get("dest_code")
        or data.get("destCode")
        or data.get("destination")
        or details.get("dropoff_location")
        or details.get("destination")
        or details.get("to_location")
    )
    if origin and dest and origin != dest:
        route = f"{origin} → {dest}"
    elif origin:
        route = str(origin)
    elif dest:
        route = str(dest)
    else:
        route = "Not provided"

    # 10. Date & Time
    raw_date = (
        data.get("departure_time")
        or data.get("departureTime")
        or data.get("service_date")
        or data.get("serviceDate")
        or data.get("travel_date")
        or details.get("date")
        or details.get("travel_date")
        or details.get("service_date")
    )
    date_time = _format_datetime(raw_date)

    # Notes
    raw_notes = (
        data.get("notes")
        or details.get("notes")
        or details.get("additional_requirements")
        or details.get("special_instructions")
        or metadata.get("notes")
    )
    notes = str(raw_notes).strip() if raw_notes else "Please action this booking from the operations dashboard."

    # Status
    status = str(data.get("status") or "PENDING").upper()

    # Dashboard URL
    dashboard_url = str(
        data.get("dashboard_url")
        or f"https://shafskyaviation.com/admin/bookings"
    ).strip()

    return {
        "reference_no": str(ref).strip(),
        "service": str(service).strip(),
        "customer": str(customer).strip(),
        "contact": str(contact).strip(),
        "email": str(email).strip(),
        "passengers": str(pax).strip(),
        "vehicle": vehicle.strip() if vehicle else None,
        "category": str(category).strip() if category else None,
        "route": str(route).strip(),
        "date_time": str(date_time).strip(),
        "notes": notes,
        "status": status,
        "dashboard_url": dashboard_url,
    }


# Standard Navy Icon SVGs (viewBox 0 0 24 24, stroke="#0a196f")
ICONS = {
    "reference": '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="#0a196f" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="4" x2="20" y1="9" y2="9"/><line x1="4" x2="20" y1="15" y2="15"/><line x1="10" x2="8" y1="3" y2="21"/><line x1="16" x2="14" y1="3" y2="21"/></svg>',
    "service": '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="#0a196f" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect width="20" height="14" x="2" y="7" rx="2" ry="2"/><path d="M16 21V5a2 2 0 0 0-2-2h-4a2 2 0 0 0-2 2v16"/></svg>',
    "customer": '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="#0a196f" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M19 21v-2a4 4 0 0 0-4-4H9a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/></svg>',
    "contact": '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="#0a196f" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6 19.79 19.79 0 0 1-3.07-8.67A2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72 12.84 12.84 0 0 0 .7 2.81 2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45 12.84 12.84 0 0 0 2.81.7A2 2 0 0 1 22 16.92z"/></svg>',
    "email": '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="#0a196f" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect width="20" height="16" x="2" y="4" rx="2"/><path d="m22 7-8.97 5.7a1.94 1.94 0 0 1-2.06 0L2 7"/></svg>',
    "passengers": '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="#0a196f" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M22 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/></svg>',
    "vehicle": '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="#0a196f" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M19 17h2c.6 0 1-.4 1-1v-3c0-.9-.7-1.7-1.5-1.9C18.7 10.6 16 10 16 10s-1.3-1.4-2.2-2.3c-.5-.4-1.1-.7-1.8-.7H5c-.6 0-1.1.4-1.4.9l-1.4 2.9A3.7 3.7 0 0 0 2 12v4c0 .6.4 1 1 1h2"/><circle cx="7" cy="17" r="2"/><path d="M9 17h6"/><circle cx="17" cy="17" r="2"/></svg>',
    "category": '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="#0a196f" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="12 2 2 7 12 12 22 7 12 2"/><polyline points="2 17 12 22 22 17"/><polyline points="2 12 12 17 22 12"/></svg>',
    "route": '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="#0a196f" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="10" r="3"/><path d="M12 21.7C17.3 17 20 13 20 10a8 8 0 1 0-16 0c0 3 2.7 7 8 11.7z"/></svg>',
    "date_time": '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="#0a196f" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect width="18" height="18" x="3" y="4" rx="2" ry="2"/><line x1="16" x2="16" y1="2" y2="6"/><line x1="8" x2="8" y1="2" y2="6"/><line x1="3" x2="21" y1="10" y2="10"/></svg>',
    "info": '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#0a196f" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><path d="M12 16v-4"/><path d="M12 8h.01"/></svg>',
    "external": '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="#ffffff" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M15 3h6v6"/><path d="M10 14 21 3"/><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/></svg>',
}


def render_whatsapp_operations_alert_html(
    data: Dict[str, Any],
    logo_src: Optional[str] = None,
) -> str:
    """
    Renders the complete, self-contained Operations Alert HTML template.
    Strictly follows:
    - White & light blue-grey background with subtle borders.
    - Top-left Shafsky logo with thin vertical divider.
    - Large bold dark navy heading ("New Operations Alert") & subtitle ("Booking / Enquiry Received").
    - Gold PENDING status badge aligned top-right.
    - Two-column full-width details table with alternating row backgrounds and navy icons.
    - Strict field ordering:
      1. Reference No.
      2. Service
      3. Customer
      4. Contact
      5. Email
      6. Passengers
      7. Vehicle (if present)
      8. Category (if present)
      9. Route
      10. Date & Time
    - Notes panel at bottom with information icon.
    - Dark navy View in Dashboard button aligned bottom-right.
    """
    fields = extract_operations_alert_fields(data)

    # Build rows list strictly in order
    raw_rows: List[Tuple[str, str, str, str]] = [
        ("Reference No.", fields["reference_no"], "reference", "font-mono font-bold text-[#0a196f]"),
        ("Service", fields["service"], "service", "font-semibold text-slate-900"),
        ("Customer", fields["customer"], "customer", "font-semibold text-slate-900"),
        ("Contact", fields["contact"], "contact", "font-mono text-slate-800"),
        ("Email", fields["email"], "email", "text-slate-700"),
        ("Passengers", fields["passengers"], "passengers", "font-medium text-slate-800"),
    ]

    # Vehicle (include if present, fallback cleanly)
    if fields.get("vehicle"):
        raw_rows.append(("Vehicle", fields["vehicle"], "vehicle", "font-medium text-slate-900"))

    # Category (include if present)
    if fields.get("category"):
        raw_rows.append(("Category", fields["category"], "category", "font-medium text-slate-800"))

    raw_rows.append(("Route", fields["route"], "route", "font-medium text-slate-900"))
    raw_rows.append(("Date & Time", fields["date_time"], "date_time", "font-mono font-medium text-slate-800"))

    # Generate alternating table rows
    table_rows_html = []
    for idx, (label, val, icon_key, val_class) in enumerate(raw_rows):
        bg_style = "background-color: #ffffff;" if idx % 2 == 0 else "background-color: #f8fafc;"
        icon_svg = ICONS.get(icon_key, "")
        table_rows_html.append(f"""
        <tr style="{bg_style} border-bottom: 1px solid #e2e8f0;">
          <td style="padding: 10px 16px; width: 38%; vertical-align: middle; border-right: 1px solid #f1f5f9;">
            <div style="display: flex; align-items: center; gap: 8px;">
              <span style="display: inline-flex; align-items: center; justify-content: center; width: 22px; height: 22px; border-radius: 6px; background-color: #f1f5f9; shrink: 0;">
                {icon_svg}
              </span>
              <span style="font-size: 13px; font-weight: 600; color: #1e293b; letter-spacing: -0.01em;">
                {escape(label)}
              </span>
            </div>
          </td>
          <td style="padding: 10px 16px; width: 62%; vertical-align: middle;">
            <span class="{val_class}" style="font-size: 13px; color: #0f172a; line-height: 1.4; word-break: break-word;">
              {escape(val)}
            </span>
          </td>
        </tr>""")

    rows_combined = "".join(table_rows_html)

    # Status Badge Style
    status_label = escape(fields["status"])
    if status_label == "PENDING":
        badge_style = "background-color: #fefce8; border: 1px solid #fef08a; color: #854d0e;"
        dot_color = "#eab308"
    elif status_label in ("CONFIRMED", "ACCEPTED", "SUCCESS"):
        badge_style = "background-color: #ecfdf5; border: 1px solid #a7f3d0; color: #065f46;"
        dot_color = "#10b981"
    else:
        badge_style = "background-color: #f1f5f9; border: 1px solid #cbd5e1; color: #334155;"
        dot_color = "#64748b"

    # Logo HTML (Uses provided logo or inline SVG representation of Shafsky Aviation globe logo)
    if logo_src:
        logo_markup = f'<img src="{escape(logo_src)}" alt="Shafsky Aviation" style="height: 38px; width: auto; object-fit: contain; display: block;" />'
    else:
        # High quality inline SVG of Shafsky Aviation Logo with Globe and Gold Plane
        logo_markup = """
        <div style="display: flex; align-items: center; gap: 8px;">
          <svg width="40" height="40" viewBox="0 0 100 100" fill="none" style="display: block;">
            <circle cx="50" cy="50" r="44" fill="#0284c7" />
            <path d="M50 6 A44 44 0 0 1 94 50 A44 44 0 0 1 50 94 A44 44 0 0 1 6 50 A44 44 0 0 1 50 6" stroke="#38bdf8" stroke-width="2" fill="none"/>
            <path d="M15 50 Q 50 20 85 50 Q 50 80 15 50" fill="#22c55e" opacity="0.85"/>
            <path d="M22 62 L48 46 L76 28 L66 45 L82 46 L58 58 L46 54 L32 64 Z" fill="#eab308" stroke="#ca8a04" stroke-width="1.5" />
          </svg>
          <div style="display: flex; flex-direction: column;">
            <span style="font-size: 13px; font-weight: 800; color: #0a196f; letter-spacing: -0.02em; line-height: 1.1;">SHAFSKY</span>
            <span style="font-size: 8.5px; font-weight: 700; color: #b45309; letter-spacing: 0.12em; text-transform: uppercase;">AVIATION</span>
          </div>
        </div>
        """

    notes_text = escape(fields["notes"])
    dashboard_url = escape(fields["dashboard_url"])

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1.0"/>
  <title>New Operations Alert — {fields['reference_no']}</title>
  <style>
    * {{
      box-sizing: border-box;
      margin: 0;
      padding: 0;
    }}
    body {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
      background-color: #f1f5f9;
      color: #0f172a;
      padding: 24px 16px;
      display: flex;
      justify-content: center;
      align-items: flex-start;
      min-height: 100vh;
      -webkit-font-smoothing: antialiased;
    }}
    .alert-card {{
      width: 100%;
      max-width: 640px;
      background-color: #ffffff;
      border: 1px solid #e2e8f0;
      border-radius: 16px;
      box-shadow: 0 10px 25px -5px rgba(15, 23, 42, 0.08), 0 8px 10px -6px rgba(15, 23, 42, 0.04);
      padding: 28px;
      overflow: hidden;
    }}
    @media (max-width: 480px) {{
      body {{ padding: 12px 8px; }}
      .alert-card {{ padding: 18px 14px; border-radius: 12px; }}
    }}
  </style>
</head>
<body>
  <div class="alert-card">
    <!-- Header Section -->
    <div style="display: flex; align-items: center; justify-content: space-between; gap: 12px; flex-wrap: wrap; padding-bottom: 20px; border-bottom: 1px solid #e2e8f0;">
      <!-- Logo + Divider + Headings -->
      <div style="display: flex; align-items: center; gap: 14px; min-width: 0;">
        <div style="shrink: 0;">
          {logo_markup}
        </div>
        <div style="width: 1px; height: 38px; background-color: #cbd5e1; shrink: 0;"></div>
        <div style="min-width: 0;">
          <h1 style="font-size: 19px; font-weight: 800; color: #0a196f; letter-spacing: -0.025em; line-height: 1.2; margin: 0;">
            New Operations Alert
          </h1>
          <p style="font-size: 12.5px; font-weight: 500; color: #64748b; margin-top: 2px;">
            Booking / Enquiry Received
          </p>
        </div>
      </div>

      <!-- Gold PENDING Status Badge -->
      <div style="display: inline-flex; align-items: center; gap: 6px; padding: 5px 12px; border-radius: 9999px; font-size: 11px; font-weight: 700; letter-spacing: 0.08em; text-transform: uppercase; {badge_style} shrink: 0;">
        <span style="display: inline-block; width: 6px; height: 6px; border-radius: 50%; background-color: {dot_color};"></span>
        <span>{status_label}</span>
      </div>
    </div>

    <!-- Structured Full-Width Details Table -->
    <table style="width: 100%; border-collapse: separate; border-spacing: 0; margin-top: 20px; border: 1px solid #e2e8f0; border-radius: 12px; overflow: hidden;">
      <tbody>
        {rows_combined}
      </tbody>
    </table>

    <!-- Notes Panel -->
    <div style="margin-top: 18px; padding: 14px 16px; background-color: #f8fafc; border: 1px solid #e2e8f0; border-radius: 12px;">
      <div style="display: flex; align-items: center; gap: 7px; margin-bottom: 6px;">
        <span style="display: inline-flex; align-items: center; justify-content: center; shrink: 0;">
          {ICONS['info']}
        </span>
        <span style="font-size: 11.5px; font-weight: 700; color: #0a196f; text-transform: uppercase; letter-spacing: 0.06em;">
          Special Notes & Instructions
        </span>
      </div>
      <p style="font-size: 12.5px; color: #334155; line-height: 1.5; margin: 0; font-style: normal; padding-left: 23px;">
        “{notes_text}”
      </p>
    </div>

    <!-- Bottom Actions Row -->
    <div style="margin-top: 22px; display: flex; align-items: center; justify-content: flex-end;">
      <a href="{dashboard_url}" target="_blank" rel="noopener noreferrer" style="display: inline-flex; align-items: center; gap: 8px; padding: 10px 20px; background-color: #0a196f; color: #ffffff; text-decoration: none; border-radius: 8px; font-size: 13px; font-weight: 600; letter-spacing: 0.01em; box-shadow: 0 4px 12px rgba(10, 25, 111, 0.22); transition: background-color 0.15s ease;">
        <span>View in Dashboard</span>
        {ICONS['external']}
      </a>
    </div>
  </div>
</body>
</html>
"""
