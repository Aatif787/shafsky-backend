"""Customer/ops-facing display names for booked service packages.

Multi-service bookings are stored internally with the placeholder package
``multi_service``. That internal value must never be shown to customers or
operations staff; they should see the package(s) that were actually selected
(for example Platinum, Gold, Elite or Elite Plus).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

_MULTI_SERVICE_PLACEHOLDERS = {"multi_service", "multi-service", "multiservice"}
_JOURNEY_LABELS = {"DEPARTURE": "Departure", "ARRIVAL": "Arrival", "TRANSIT": "Transit"}


def is_multi_service_placeholder(value: Any) -> bool:
    return str(value or "").strip().lower() in _MULTI_SERVICE_PLACEHOLDERS


def _package_label(slug: Any) -> str:
    text = str(slug or "").strip().replace("_", "-")
    if text.lower().endswith("-service"):
        text = text[: -len("-service")]
    return " ".join(part.capitalize() for part in text.split("-") if part)


def resolve_service_display_name(name: Any, meta: Optional[Dict[str, Any]] = None) -> Any:
    """Return ``name`` unchanged unless it is the internal multi-service placeholder.

    For the placeholder, build the label from the services stored on the booking:
    a single package ("Gold"), or per-journey labels when packages differ
    ("Departure Gold, Arrival Platinum"). Falls back to the original value if
    no package information is available.
    """
    if not is_multi_service_placeholder(name):
        return name
    meta = meta if isinstance(meta, dict) else {}
    services: List[Dict[str, Any]] = [
        s
        for s in (meta.get("available_services") or meta.get("services") or [])
        if isinstance(s, dict)
    ]
    entries = []
    for svc in services:
        label = _package_label(svc.get("package") or svc.get("package_slug"))
        if label:
            entries.append((str(svc.get("service_type") or "").upper(), label))
    if not entries:
        return name
    unique_labels = list(dict.fromkeys(label for _, label in entries))
    if len(unique_labels) == 1:
        return unique_labels[0]
    parts = []
    for journey, label in entries:
        prefix = _JOURNEY_LABELS.get(journey)
        parts.append(f"{prefix} {label}" if prefix else label)
    return ", ".join(dict.fromkeys(parts))

