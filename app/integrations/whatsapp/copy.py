"""Customer-facing WhatsApp copy. Keep test-stable phrases intact."""

from typing import Any, Dict, List, Optional

BRAND = "Shafsky Aviation Services"
FOOTER = "Reply HI · BACK · HELP · CANCEL"
SESSION_TIMEOUT_HINT = "Sessions close after 15 minutes of inactivity."
EXECUTIVE_PHONE = "+91-9599087959"

WELCOME_HEADLINE = "✨ *Welcome to Shafsky Aviation Services ✈️*"
EXPIRED_PREFIX = "Your previous session has expired. Let's start again.\n\n"
# Shown when a session timed out but the customer has not typed Hi yet.
# Never auto-send the full welcome menu without an explicit restart command.
SESSION_EXPIRED_PROMPT = (
    f"*{BRAND}*\n\n"
    "Your previous session has expired.\n\n"
    "Type *Hi* to start a new booking."
)
TYPE_HI_TO_START = (
    f"*{BRAND}*\n\n"
    "Type *Hi* to start a booking.\n\n"
    f"{FOOTER}"
)
UNSUPPORTED_INBOUND = (
    f"*{BRAND}*\n\n"
    "Please reply with text, or tap a button from the menu.\n"
    "Type *Hi* to start a booking."
)

# Services without a published online price are handled as quote requests
# instead of being sent an unpayable Razorpay link.
QUOTE_REQUEST_REGISTERED = (
    f"*{BRAND}*\n\n"
    "Thank you — your request *{booking_ref}* has been registered.\n\n"
    "This service is priced individually, so one of our reservation executives "
    "will contact you shortly with a tailored quote and a secure payment link.\n\n"
    f"Need it sooner? Call our executive on *{EXECUTIVE_PHONE}*.\n\n"
    "Type *Hi* anytime to start a different booking."
)

QUOTE_REQUEST_PENDING_ACK = (
    f"*{BRAND}*\n\n"
    "Your quote request *{booking_ref}* is with our reservations team. "
    "They will send your tailored price and payment link shortly.\n\n"
    f"For immediate assistance, call *{EXECUTIVE_PHONE}*.\n\n"
    "Type *Hi* to start a new booking instead."
)

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


def _extract_tier_name(title: str) -> str:
    """Extract clean tier name (e.g. 'Silver', 'Gold', 'Elite', 'Platinum') or fallback."""
    import re
    t = (title or "").strip()
    for tier in ("Silver", "Gold", "Elite", "Platinum", "Bronze", "VIP"):
        if re.search(rf"\b{tier}\b", t, re.IGNORECASE):
            return tier
    cleaned = re.sub(r"(?i)\s*(service|package)\s*$", "", t).strip()
    return cleaned or t


def compute_package_inheritance(services: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Dynamically computes the inheritance and additional inclusions for each package.
    Never hardcodes tier names or differences.
    """
    import re
    result = []
    prev_features: List[str] = []
    prev_tier_name: Optional[str] = None

    for i, svc in enumerate(services, 1):
        title = svc.get("title") or svc.get("name") or f"Service {i}"
        price = svc.get("price", svc.get("base_price"))
        features_curr = _feature_list(svc.get("features"))
        tier_name = _extract_tier_name(title)

        if i == 1:
            # Base tier
            result.append({
                "index": i,
                "service": svc,
                "title": title,
                "tier_name": tier_name,
                "price": price,
                "features": features_curr,
                "count": len(features_curr),
                "is_base": True,
                "prev_tier_name": None,
                "additions": features_curr,
            })
        else:
            # Higher tier: compute dynamic additions over immediately previous tier
            norm_prev = {re.sub(r"[^a-z0-9]+", " ", f.lower()).strip() for f in prev_features}
            additions = []
            for f in features_curr:
                norm_f = re.sub(r"[^a-z0-9]+", " ", f.lower()).strip()
                if norm_f and norm_f not in norm_prev:
                    additions.append(f)

            result.append({
                "index": i,
                "service": svc,
                "title": title,
                "tier_name": tier_name,
                "price": price,
                "features": features_curr,
                "count": len(features_curr),
                "is_base": False,
                "prev_tier_name": prev_tier_name,
                "additions": additions,
            })

        prev_features = features_curr
        prev_tier_name = tier_name

    return result


def compact_inheritance_package_lines(services: List[Dict[str, Any]]) -> str:
    """
    Renders Level 1 compact package list using dynamic inheritance logic.
    - Base package shows: X services included
    - Subsequent packages show: Includes all [PrevTier] services + [Additions]
    """
    if not services:
        return ""

    inheritance = compute_package_inheritance(services)
    blocks = []

    for item in inheritance:
        i = item["index"]
        title = item["title"]
        price_str = format_inr(item["price"])
        lines = [f"{i}. *{title}*", price_str]

        if item["is_base"]:
            count = item["count"]
            if count > 0:
                s_word = "service" if count == 1 else "services"
                lines.append(f"• {count} {s_word} included")
            else:
                lines.append("• Standard services included")
        else:
            prev_name = item["prev_tier_name"] or "previous"
            lines.append(f"• Includes all {prev_name} services")
            additions = item["additions"]
            for add in additions:
                lines.append(f"• + {add}")

        blocks.append("\n".join(lines))

    return "\n\n".join(blocks)


def selected_package_details_text(
    selected_svc: Dict[str, Any],
    all_services: Optional[List[Dict[str, Any]]] = None
) -> str:
    """
    Level 2 package details:
    - Base package: lists all exact service inclusions.
    - Higher package: communicates inheritance ('[Tier] includes all [PrevTier] services plus:')
      followed by the additional services.
    Preserves exact service wording.
    """
    features = _feature_list(selected_svc.get("features"))
    if not features:
        return ""

    if all_services and len(all_services) > 1:
        inheritance = compute_package_inheritance(all_services)
        sel_id = str(selected_svc.get("id"))
        sel_title = str(selected_svc.get("title") or selected_svc.get("name") or "").lower()

        matched_item = None
        for item in inheritance:
            if str(item["service"].get("id")) == sel_id:
                matched_item = item
                break
            if str(item["title"]).lower() == sel_title:
                matched_item = item
                break

        if matched_item and not matched_item["is_base"]:
            prev_name = matched_item["prev_tier_name"] or "previous"
            tier_name = matched_item["tier_name"] or "This package"
            additions = matched_item["additions"]
            lines = ["📋 *Package Inclusions:*"]
            if additions:
                lines.append(f"{tier_name} includes all {prev_name} services plus:")
                for add in additions:
                    lines.append(f"• + {add}")
            else:
                lines.append(f"• Includes all {prev_name} services")
            return "\n".join(lines)

    # Base package or single package -> list all inclusions
    lines = ["📋 *Package Inclusions:*"]
    for f in features:
        lines.append(f"• {f}")
    return "\n".join(lines)


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
    if not services:
        return ""
    text = compact_inheritance_package_lines(services)
    if len(text) <= max_chars:
        return text
    return numbered_service_lines(services)[:max_chars]


def list_row_description(price: Any, features: Any, svc_info: Optional[Dict[str, Any]] = None) -> str:
    """Meta list row description max 72 characters. Compact and descriptive."""
    price_part = format_inr(price)
    if svc_info and not svc_info.get("is_base") and svc_info.get("prev_tier_name"):
        prev_name = svc_info["prev_tier_name"]
        add_count = len(svc_info.get("additions", []))
        if add_count > 0:
            add_word = "addition" if add_count == 1 else "additions"
            candidate = f"{price_part} · All {prev_name} + {add_count} {add_word}"
        else:
            candidate = f"{price_part} · All {prev_name} services"
        if len(candidate) <= 72:
            return candidate

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
