"""Customer/ops-facing display names for booked service packages.

Multi-service bookings are stored internally with the placeholder package
``multi_service``. That internal value must never be shown to customers or
operations staff; they should see the package(s) that were actually selected
(for example Platinum, Gold, Elite or Elite Plus).
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("shafsky.service_display")

_MULTI_SERVICE_PLACEHOLDERS = {
    "multi_service",
    "multi-service",
    "multiservice",
    "multi_services",
    "multi-services",
}
_JOURNEY_LABELS = {
    "DEPARTURE": "Departure",
    "ARRIVAL": "Arrival",
    "TRANSIT": "Transit",
}

_KNOWN_PACKAGES = {
    "platinum": "Platinum",
    "gold": "Gold",
    "elite": "Elite",
    "elite_plus": "Elite Plus",
    "elite-plus": "Elite Plus",
    "silver": "Silver",
    "bronze": "Bronze",
    "vip": "VIP",
    "vvip": "VVIP",
}


def is_multi_service_placeholder(value: Any) -> bool:
    if value is None:
        return False
    val_str = str(value).strip().lower()
    return val_str in _MULTI_SERVICE_PLACEHOLDERS


def _package_label(slug: Any) -> str:
    if not slug:
        return ""
    slug_str = str(slug).strip().lower()
    if slug_str in _KNOWN_PACKAGES:
        return _KNOWN_PACKAGES[slug_str]

    text = slug_str.replace("_", "-")
    if text.endswith("-service"):
        text = text[: -len("-service")]
    if text in _KNOWN_PACKAGES:
        return _KNOWN_PACKAGES[text]

    return " ".join(part.capitalize() for part in text.split("-") if part)


def _extract_services_from_dict(d: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Extract list of service dictionaries from a payload or metadata dict."""
    services: List[Dict[str, Any]] = []

    # 0. service_breakdown
    breakdown = d.get("service_breakdown")
    if isinstance(breakdown, list):
        for s in breakdown:
            if isinstance(s, dict):
                services.append(s)

    # 1. available_services
    avail = d.get("available_services")
    if isinstance(avail, list):
        for s in avail:
            if isinstance(s, dict):
                services.append(s)

    # 2. services
    svcs = d.get("services")
    if isinstance(svcs, list):
        for s in svcs:
            if isinstance(s, dict):
                services.append(s)

    # 3. selected_services
    sel = d.get("selected_services")
    if isinstance(sel, list):
        for s in sel:
            if isinstance(s, dict):
                services.append(s)
    elif isinstance(sel, dict):
        nested = sel.get("services")
        if isinstance(nested, list):
            for s in nested:
                if isinstance(s, dict):
                    services.append(s)

    # 4. details
    details = d.get("details")
    if isinstance(details, dict) and details is not d:
        services.extend(_extract_services_from_dict(details))

    # 5. metadata_json
    meta = d.get("metadata_json")
    if isinstance(meta, dict) and meta is not d:
        services.extend(_extract_services_from_dict(meta))

    return services


def _extract_direct_package(d: Dict[str, Any]) -> Optional[str]:
    """Check for direct package keys in a dictionary."""
    for key in ("package", "package_name", "package_slug", "service_package", "tier", "service_tier"):
        val = d.get(key)
        if val and not is_multi_service_placeholder(val):
            label = _package_label(val)
            if label:
                return label

    # Check nested details / metadata_json
    for nested_key in ("details", "metadata_json"):
        nested = d.get(nested_key)
        if isinstance(nested, dict) and nested is not d:
            res = _extract_direct_package(nested)
            if res:
                return res

    return None


def resolve_service_display_name(
    name: Any,
    meta: Optional[Any] = None,
    db: Optional[Any] = None,
) -> Any:
    """Return ``name`` unchanged unless it contains the internal multi-service placeholder.

    For the placeholder, build the label from the services stored on the booking:
    a single package ("Platinum", "Gold", "Elite", "Elite Plus"), or per-journey labels
    when packages differ ("Departure Gold, Arrival Platinum").
    Never returns an internal multi_service placeholder string when a package or
    clean fallback can be determined.
    """
    name_str = str(name or "").strip()
    is_placeholder = is_multi_service_placeholder(name_str) or not name_str
    contains_placeholder = "multi_service" in name_str.lower() or "multi-service" in name_str.lower()

    if not is_placeholder and not contains_placeholder:
        return name

    meta_dict: Dict[str, Any] = {}
    if isinstance(meta, dict):
        meta_dict = dict(meta)
    elif hasattr(meta, "__dict__"):
        if hasattr(meta, "metadata_json") and isinstance(meta.metadata_json, dict):
            meta_dict.update(meta.metadata_json)
        if hasattr(meta, "selected_services"):
            meta_dict["selected_services"] = meta.selected_services
        if hasattr(meta, "service_package") and meta.service_package:
            meta_dict["package"] = meta.service_package
        if hasattr(meta, "booking_ref"):
            meta_dict["booking_ref"] = meta.booking_ref

    direct_pkg = _extract_direct_package(meta_dict)
    services = _extract_services_from_dict(meta_dict)

    # Fallback DB lookup if neither direct package nor services found
    booking_ref = meta_dict.get("booking_ref") or meta_dict.get("bookingRef") or meta_dict.get("reference")
    if not direct_pkg and not services and booking_ref:
        try:
            from app.database import SessionLocal
            from app.models.schema import Booking
            local_db = db or SessionLocal()
            try:
                b_row = local_db.query(Booking).filter(Booking.booking_ref == str(booking_ref)).first()
                if b_row:
                    if isinstance(b_row.metadata_json, dict):
                        meta_dict.update(b_row.metadata_json)
                    if b_row.selected_services:
                        meta_dict["selected_services"] = b_row.selected_services
                    direct_pkg = _extract_direct_package(meta_dict)
                    services = _extract_services_from_dict(meta_dict)
            finally:
                if db is None:
                    local_db.close()
        except Exception as db_err:
            logger.debug("Database lookup skipped in resolve_service_display_name: %s", db_err)

    resolved_label: Optional[str] = None
    if services:
        entries: List[Tuple[str, str]] = []
        for svc in services:
            pkg = (
                svc.get("package")
                or svc.get("package_slug")
                or svc.get("package_name")
                or svc.get("service_type")
                or svc.get("slug")
            )
            if not pkg or is_multi_service_placeholder(pkg):
                continue
            label = _package_label(pkg)
            if label:
                jt = str(svc.get("service_type") or svc.get("journey_type") or "").upper()
                entries.append((jt, label))

        if entries:
            unique_labels = list(dict.fromkeys(label for _, label in entries))
            if len(unique_labels) == 1:
                resolved_label = unique_labels[0]
            else:
                parts = []
                for journey, label in entries:
                    prefix = _JOURNEY_LABELS.get(journey)
                    parts.append(f"{prefix} {label}" if prefix else label)
                resolved_label = ", ".join(dict.fromkeys(parts))

    if not resolved_label and direct_pkg:
        resolved_label = direct_pkg

    if resolved_label:
        if is_placeholder:
            return resolved_label
        for ph in (" — multi_service", " — multi-service", " - multi_service", " - multi-service"):
            if ph in name_str:
                return name_str.replace(ph, f" — {resolved_label}")
        if "multi_service" in name_str:
            return name_str.replace("multi_service", resolved_label)
        if "multi-service" in name_str:
            return name_str.replace("multi-service", resolved_label)
        return resolved_label

    if contains_placeholder:
        import re
        cleaned = re.sub(r"\s*[-—–]\s*multi[-_]services?", "", name_str, flags=re.IGNORECASE).strip()
        cleaned = re.sub(r"multi[-_]services?", "", cleaned, flags=re.IGNORECASE).strip()
        if cleaned:
            return cleaned
        cat = meta_dict.get("service_category") or meta_dict.get("category")
        if cat and not is_multi_service_placeholder(cat):
            return str(cat).strip()
        return "Meet & Greet"

    return name
