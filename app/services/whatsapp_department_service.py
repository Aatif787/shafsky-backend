"""
Service-Wise WhatsApp Notification Dispatcher for Shafsky Aviation.

Dispatches internal operational notifications for every booking and enquiry
to all 5 configured recipient numbers across all 8 service categories:
- Meet & Greet
- Lounge
- Ticketing
- Transport
- Hotels
- Air Charter
- Cargo & Logistics
- Special Services
"""

import logging
import threading
from typing import Any, Dict, Tuple

from app.services.whatsapp_department_ext import WhatsAppDepartmentExtMixin
from app.services.whatsapp_department_idempotency import WhatsAppDepartmentIdempotencyMixin
from app.services.whatsapp_department_formatting import (
    Department,
    WhatsAppDepartmentFormattingMixin,
)

logger = logging.getLogger("shafsky.whatsapp_department")
from app.config import settings
from app.integrations.whatsapp.client import whatsapp_client


class WhatsAppDepartmentService(
    WhatsAppDepartmentFormattingMixin,
    WhatsAppDepartmentIdempotencyMixin,
    WhatsAppDepartmentExtMixin,
):
    """
    Evaluates service category from booking/enquiry payloads,
    resolves all 5 recipient numbers, applies robust idempotency lifecycle checks,
    and dispatches structured notifications to all 5 recipients.
    """

    # In-memory idempotency state with thread lock: dict of key -> {"state": str, "expires": float}
    _dispatched_events: Dict[str, Dict[str, Any]] = {}
    _lock = threading.Lock()

    IDEMPOTENCY_LOCK_TTL_SECONDS: int = 120  # In-progress concurrency mutex
    IDEMPOTENCY_DISPATCHED_TTL_SECONDS: int = 86400  # 24 hours completed state
    IDEMPOTENCY_TIMEOUT_HOLD_SECONDS: int = 300  # 5 minutes hold on ambiguous timeout

    MAX_TEMPLATE_PARAM_CHARS: int = 1000  # Strictly below Meta Cloud API 1024-character limit

    # Meta Cloud API failures that need an operator action rather than a retry.
    META_FAILURE_REMEDIES: Dict[int, Tuple[str, str]] = {
        190: (
            "AUTH_ERROR",
            "WHATSAPP_ACCESS_TOKEN is invalid or expired. Issue a new system-user token and update the deployment.",
        ),
        100: (
            "INVALID_REQUEST",
            "Meta rejected the payload. Verify template name, language and parameter count.",
        ),
        131047: (
            "WINDOW_CLOSED",
            "Recipient is outside the 24-hour customer service window. Business-initiated messages "
            "require an approved WHATSAPP_DEPARTMENT_NOTIFICATION_TEMPLATE.",
        ),
        131026: (
            "UNDELIVERABLE",
            "Recipient cannot receive WhatsApp messages (not on WhatsApp or has not opted in).",
        ),
        131030: (
            "INVALID_RECIPIENT",
            "Recipient number is not in the allowed recipient list for this WhatsApp Business Account.",
        ),
        131031: (
            "ACCOUNT_RESTRICTED",
            "The WhatsApp Business Account is restricted. Resolve the restriction in Meta Business Manager.",
        ),
        131042: (
            "BILLING_ISSUE",
            "Meta will not deliver any template message because this WhatsApp Business Account has "
            "no usable payment method. Add a card or prepaid balance in Meta Business Manager -> "
            "Billing (business.facebook.com/billing_hub); the exact ADD_PM link is in the error "
            "detail. If a card is already on file, the account instead reports 'payment restricted' "
            "and the restriction must be cleared. Meta still accepts the send (HTTP 200 + wamid), "
            "so this only becomes visible on the delivery-status webhook.",
        ),
        132000: (
            "TEMPLATE_PARAM_MISMATCH",
            "Parameter count does not match the approved template body. Align build_template_components with the template.",
        ),
        132001: (
            "TEMPLATE_NOT_FOUND",
            "Template name/language is not approved on this WhatsApp Business Account. "
            "Check WHATSAPP_DEPARTMENT_NOTIFICATION_TEMPLATE and WHATSAPP_DEPARTMENT_TEMPLATE_LANG.",
        ),
        132005: (
            "TEMPLATE_PARAM_MISMATCH",
            "Template parameter text violates Meta limits. Check sanitize_template_param output.",
        ),
        132007: (
            "TEMPLATE_PAUSED",
            "The approved template is paused for quality. Fix the template in Meta Business Manager.",
        ),
        132012: (
            "TEMPLATE_PARAM_MISMATCH",
            "Template parameter format is invalid for the approved template.",
        ),
        130429: (
            "RATE_LIMITED",
            "Meta Cloud API rate limit reached. Reduce burst volume or request a higher tier.",
        ),
        131048: (
            "RATE_LIMITED",
            "Meta Cloud API spam rate limit reached. Slow down outbound messaging.",
        ),
        131056: (
            "RATE_LIMITED",
            "Pair rate limit reached for this recipient. Retry later.",
        ),
    }

    # Department 1 service keywords
    DEPT1_CATEGORIES = {
        "meet & greet",
        "meet and greet",
        "airport assistance",
        "airport services",
        "lounge",
        "airport lounge",
        "lounge access",
        "vip lounge",
        "fast track",
        "baggage assistance",
    }

    # Department 2 service keywords
    DEPT2_CATEGORIES = {
        "ticketing",
        "air ticketing",
        "flight ticket",
        "flight booking",
        "transport",
        "ground transport",
        "round trip",
        "round_trip",
        "roundtrip",
        "transportation services",
        "hotel & transportation",
        "hotel",
        "hotels",
        "hotel booking",
        "hotel services",
        "travel support",
    }

    # Department 3 service keywords
    DEPT3_CATEGORIES = {
        "private charter",
        "air charter",
        "charter",
        "aircraft charter",
        "cargo & logistics",
        "cargo",
        "air cargo",
        "freight",
        "logistics",
        "special services",
        "special service",
        "medical assistance",
        "medical services",
        "travel services",
    }


__all__ = ["Department", "WhatsAppDepartmentService"]
