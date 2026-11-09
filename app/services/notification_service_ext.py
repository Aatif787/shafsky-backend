"""
Extended WhatsApp enquiry, booking confirmation, customer template, and invoice
delivery helpers for NotificationService.
"""

from typing import Any, Dict, List, Optional, Tuple

from app.services.notification_customer_formatting import (
    _ns,
    TEMPLATE_PARAM_MAX_CHARS,
    build_template_parameter,
    _format_datetime,
    build_customer_summary,
)
from app.services.notification_customer_enquiry_mixin import NotificationCustomerEnquiryMixin
from app.services.notification_customer_confirmation_mixin import NotificationCustomerConfirmationMixin
from app.services.notification_customer_invoice_mixin import NotificationCustomerInvoiceMixin


class NotificationServiceExtMixin(
    NotificationCustomerConfirmationMixin,
    NotificationCustomerEnquiryMixin,
    NotificationCustomerInvoiceMixin,
):
    """
    Composed mixin providing complete Customer WhatsApp enquiry, booking confirmation,
    and invoice dispatch capabilities for NotificationService.
    """
    pass


__all__ = [
    "NotificationServiceExtMixin",
    "TEMPLATE_PARAM_MAX_CHARS",
    "build_template_parameter",
    "_format_datetime",
    "build_customer_summary",
]
