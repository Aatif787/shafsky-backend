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


def _feature_list(raw: Any) -> List[str]:
    if isinstance(raw, list):
        return [str(item).strip() for item in raw if str(item).strip()]
    return []


def compact_inclusion_lines(features: Any, max_show: int = 4) -> List[str]:
    """Up to 4 DB inclusions verbatim, plus '+ X more' when additional exist."""
    feats = _feature_list(features)
    show = feats[: max(0, max_show)]
    extra = max(0, len(feats) - len(show))
    lines = [f"✓ {item}" for item in show]
    if extra:
        lines.append(f"+ {extra} more")
    return lines


def numbered_service_lines_with_inclusions(
    services: List[Dict[str, Any]],
    max_chars: int = 900,
) -> str:
    """Compact package cards for WhatsApp body (Meta body max 1024)."""

    def _blocks(max_show: int) -> str:
        chunks = []
        for i, svc in enumerate(services, 1):
            title = svc.get("title") or svc.get("name") or "Service"
            price = svc.get("price", svc.get("base_price"))
            lines = [f"{i}. *{title}*", format_inr(price)]
            lines.extend(compact_inclusion_lines(svc.get("features"), max_show=max_show))
            chunks.append("\n".join(lines))
        return "\n\n".join(chunks)

    for max_show in (4, 3, 2, 0):
        text = _blocks(max_show) if max_show else numbered_service_lines(services)
        if len(text) <= max_chars:
            return text
    return numbered_service_lines(services)[:max_chars]


def list_row_description(price: Any, features: Any) -> str:
    """Meta list row description max 72 characters. Prefer first DB inclusion."""
    price_part = format_inr(price)
    feats = _feature_list(features)
    if feats:
        first = feats[0]
        candidate = f"{price_part} · {first}"
        if len(candidate) <= 72:
            return candidate
        keep = 72 - len(price_part) - 3
        if keep > 8:
            return f"{price_part} · {first[:keep].rstrip()}"
    return price_part[:72]


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
    prefix = (
        f"✨ *Available services at {loc}*\n\n"
        f"• *Journey Type*: {journey_label}\n"
        f"• *Travel Type*: {travel_label}\n"
        f"{term_line}\n"
        "Reply with a number or tap *View Packages*:\n\n"
    )
    numbered = numbered_service_lines_with_inclusions(
        services, max_chars=max(200, 1024 - len(prefix))
    )
    return prefix + numbered


def catalog_services_body(category_name: str, services: List[Dict[str, Any]]) -> str:
    numbered = numbered_service_lines(services)
    return (
        f"✨ *{category_name}*\n\n"
        "Reply with a number or tap *Select Option*:\n\n"
        f"{numbered}"
    )
