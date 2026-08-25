"""Customer-facing WhatsApp copy. Keep test-stable phrases intact."""

from typing import Any, Dict, List, Optional

BRAND = "Shafsky Aviation"
FOOTER = "Reply HI · BACK · HELP · CANCEL"
SESSION_TIMEOUT_HINT = "Sessions close after 15 minutes of inactivity."

WELCOME_HEADLINE = "✨ *Welcome to Shafsky Aviation ✈️*"
EXPIRED_PREFIX = "Your previous session has expired. Let's start again.\n\n"

CATEGORY_LINES = (
    "1️⃣ Airport Services\n"
    "2️⃣ Travel Services\n"
    "3️⃣ Private Charter\n"
    "4️⃣ Hotel & Transportation"
)


def format_inr(amount: Any) -> str:
    try:
        return f"₹{int(float(amount)):,}"
    except (TypeError, ValueError):
        return "₹—"


def list_row_title(name: str, price: Any) -> str:
    """Meta list row title max 24 characters. Prefer name + price when it fits."""
    name = (name or "Service").strip()
    price_part = f" {format_inr(price)}"
    if len(name) + len(price_part) <= 24:
        return f"{name}{price_part}"
    if len(price_part) < 24:
        keep = 24 - len(price_part)
        return f"{name[:keep].rstrip()}{price_part}"[:24]
    return name[:24]


def numbered_service_lines(services: List[Dict[str, Any]]) -> str:
    lines = []
    for i, svc in enumerate(services, 1):
        title = svc.get("title") or svc.get("name") or "Service"
        price = svc.get("price", svc.get("base_price"))
        lines.append(f"{i}. *{title}* — {format_inr(price)}")
    return "\n".join(lines)


def category_menu_body(prefix_notice: str = "") -> str:
    return (
        f"{prefix_notice}"
        f"{WELCOME_HEADLINE}\n\n"
        f"{SESSION_TIMEOUT_HINT}\n\n"
        "How may our concierge assist you today?\n\n"
        f"{CATEGORY_LINES}\n\n"
        "Reply with *1–4* or tap *View Options*."
    )


def category_menu_fallback(prefix_notice: str = "") -> str:
    return (
        f"{category_menu_body(prefix_notice)}\n\n"
        "Please reply with *1*, *2*, *3*, or *4*."
    )


def airport_packages_body(
    airport_name: str,
    iata: Optional[str],
    journey_label: str,
    travel_label: str,
    services: List[Dict[str, Any]],
    terminal: Optional[str] = None,
) -> str:
    loc = f"{airport_name} ({iata})" if iata else airport_name
    term_line = f"• *Terminal*: {terminal}\n" if terminal else ""
    numbered = numbered_service_lines(services)
    return (
        f"✨ *Available services at {loc}*\n\n"
        f"• *Journey Type*: {journey_label}\n"
        f"• *Travel Type*: {travel_label}\n"
        f"{term_line}\n"
        "Reply with a number or tap *View Packages*:\n\n"
        f"{numbered}"
    )


def catalog_services_body(category_name: str, services: List[Dict[str, Any]]) -> str:
    numbered = numbered_service_lines(services)
    return (
        f"✨ *{category_name}*\n\n"
        "Reply with a number or tap *Select Option*:\n\n"
        f"{numbered}"
    )
