"""
Extended template resolution, Meta failure classification, configuration diagnostics,
and multi-recipient dispatch mixin for WhatsAppDepartmentService.
"""

import os
from typing import Any, Dict, List, Optional, Tuple


def _wds():
    import app.services.whatsapp_department_service as wds_mod
    return wds_mod


class WhatsAppDepartmentExtMixin:
    """Mixin providing Meta template resolution, failure diagnostics, and dispatch methods."""

    @classmethod
    def _resolve_template(cls) -> Tuple[str, str]:
        """
        Resolves the approved Meta template name and language for operations notifications.

        A missing template is not a cosmetic warning: Meta only accepts freeform text inside
        a recipient's 24-hour customer service window, so template-less delivery succeeds for
        a while and then fails for every recipient with error 131047. Surface that loudly.
        """
        w = _wds()
        settings = w.settings
        logger = w.logger

        template_name = getattr(settings, "WHATSAPP_DEPARTMENT_NOTIFICATION_TEMPLATE", None)
        if template_name is None:
            template_name = os.getenv("WHATSAPP_DEPARTMENT_NOTIFICATION_TEMPLATE", "")
        template_name = (template_name or "").strip()

        template_lang = getattr(settings, "WHATSAPP_DEPARTMENT_TEMPLATE_LANG", None)
        if template_lang is None:
            template_lang = os.getenv("WHATSAPP_DEPARTMENT_TEMPLATE_LANG", "en_US")
        template_lang = (template_lang or "en_US").strip()

        if not template_name:
            if bool(getattr(settings, "is_production", False)):
                # The WABA has this operations template approved. Without it, every
                # alert is freeform and Meta rejects recipients outside the 24-hour
                # window with 131047. Default to the approved template so department
                # numbers keep receiving booking alerts after their window closes.
                template_name = "shafsky_ops_alert"
                template_lang = "en_US"
                logger.warning(
                    "[WhatsApp Notify] WHATSAPP_DEPARTMENT_NOTIFICATION_TEMPLATE is not set; "
                    "using the approved default template '%s' (%s) so department alerts are "
                    "not limited by the 24-hour window.",
                    template_name,
                    template_lang,
                )
            else:
                message = (
                    "[WhatsApp Notify] WHATSAPP_DEPARTMENT_NOTIFICATION_TEMPLATE is not set. "
                    "Falling back to freeform text, which Meta only accepts inside the recipient's "
                    "24-hour customer service window; outside it every send fails with error 131047. "
                    "Set WHATSAPP_DEPARTMENT_NOTIFICATION_TEMPLATE to an approved template and "
                    "WHATSAPP_DEPARTMENT_TEMPLATE_LANG to its language code to make delivery window-independent."
                )
                logger.warning(message)

        return template_name, template_lang

    @classmethod
    def _send_to_recipient(
        cls,
        phone: str,
        message_body: str,
        template_name: str,
        template_lang: str,
    ) -> Dict[str, Any]:
        """Sends one operations notification using the template path when configured."""
        whatsapp_client = _wds().whatsapp_client
        logger = _wds().logger
        if not template_name:
            return whatsapp_client.send_text_message(phone, message_body)

        components = cls.build_template_components(message_body)
        res = whatsapp_client.send_message(
            to_phone=phone,
            template_name=template_name,
            template_language=template_lang,
            template_components=components,
        )

        # 132001 means the name/language pair does not exist on this WABA. It is rejected
        # before a message is created, so one retry with the sibling language code is
        # safe. Without this, a single wrong character in WHATSAPP_DEPARTMENT_TEMPLATE_LANG
        # silently stops the alert for all five numbers.
        if not res.get("success") and res.get("error_code") == 132001:
            alt_lang = cls.alternate_template_language(template_lang)
            if alt_lang and alt_lang != template_lang:
                logger.error(
                    "[WhatsApp Notify] Template '%s' is not approved for language '%s' (132001). "
                    "Retrying once with '%s'. Align WHATSAPP_DEPARTMENT_TEMPLATE_LANG with the "
                    "language registered in Meta.",
                    template_name,
                    template_lang,
                    alt_lang,
                )
                retry = whatsapp_client.send_message(
                    to_phone=phone,
                    template_name=template_name,
                    template_language=alt_lang,
                    template_components=components,
                )
                if retry.get("success"):
                    return retry
                if retry.get("error_code") == 132001:
                    logger.error(
                        "[WhatsApp Notify] Template '%s' is not approved for '%s' either. "
                        "Check WHATSAPP_DEPARTMENT_NOTIFICATION_TEMPLATE against the approved "
                        "template list in Meta WhatsApp Manager.",
                        template_name,
                        alt_lang,
                    )
                return retry

        return res

    @classmethod
    def _classify_meta_failure(cls, res: Dict[str, Any], error_text: str) -> Tuple[str, str]:
        """Maps a failed Meta response to (recipient status, actionable remedy)."""
        code = res.get("error_code")
        code_int: Optional[int]
        try:
            code_int = int(code) if code is not None else None
        except (TypeError, ValueError):
            code_int = None

        if code_int is not None and code_int in cls.META_FAILURE_REMEDIES:
            return cls.META_FAILURE_REMEDIES[code_int]

        # Some results only carry the rendered message; match long numeric codes to avoid
        # false positives from short values such as "100" inside arbitrary text.
        for known_code, outcome in cls.META_FAILURE_REMEDIES.items():
            if len(str(known_code)) >= 5 and str(known_code) in error_text:
                return outcome

        return (
            "FAILED",
            "Inspect the Meta Cloud API error code and the WhatsApp Business Account status.",
        )

    @classmethod
    def _describe_send_result(
        cls,
        phone_label: str,
        masked_phone: str,
        res: Optional[Dict[str, Any]],
        entity_ref: str = "N/A",
    ) -> Dict[str, Any]:
        """
        Normalizes one Meta send result into a per-recipient record that keeps the real HTTP
        status and Meta error code/subcode, so an operator can tell a closed messaging window
        apart from an expired token or a rejected template.
        """
        logger = _wds().logger
        payload = res or {}
        message_id = payload.get("message_id")
        is_success = payload.get("success") is True
        res_status = str(payload.get("status") or "").lower()
        res_err = str(payload.get("error") or "")

        entry: Dict[str, Any] = {"phone": phone_label, "masked_phone": masked_phone}
        for source_key, target_key in (
            ("status_code", "http_status"),
            ("error_code", "error_code"),
            ("error_subcode", "error_subcode"),
            ("error_user_msg", "error_user_msg"),
        ):
            if payload.get(source_key) is not None:
                entry[target_key] = payload.get(source_key)

        # Only a Meta-confirmed acceptance carrying a message identifier counts as sent.
        if is_success and message_id:
            entry["status"] = "ACCEPTED"
            entry["message_id"] = message_id
            logger.info(
                "[WhatsApp Notify] Meta accepted %s for %s (message_id=%s)",
                entity_ref,
                masked_phone,
                message_id,
            )
            return entry

        if res_status == "unconfigured":
            entry["status"] = "FAILED"
            entry["error"] = res_err or "WhatsApp Cloud API is not configured in backend environment."
            entry["remedy"] = (
                "Set WHATSAPP_ACCESS_TOKEN and WHATSAPP_PHONE_NUMBER_ID in the deployment environment."
            )
            logger.error(
                "[WhatsApp Notify] %s not sent to %s: WhatsApp Cloud API credentials missing.",
                entity_ref,
                masked_phone,
            )
            return entry

        if res_status == "timeout_unverified" or "timeout" in res_err.lower():
            entry["status"] = "UNKNOWN"
            entry["error"] = res_err or "Timeout connecting to Meta WhatsApp API; delivery state unknown."
            logger.error(
                "[WhatsApp Notify] %s send to %s timed out; delivery state UNKNOWN. "
                "Verify in Meta WhatsApp Manager before resending.",
                entity_ref,
                masked_phone,
            )
            return entry

        failure_status, remedy = cls._classify_meta_failure(payload, res_err)
        entry["status"] = failure_status
        entry["error"] = res_err or "Missing message identifier from Meta API"
        entry["remedy"] = remedy
        logger.error(
            "[WhatsApp Notify] %s send to %s FAILED (%s | http=%s code=%s subcode=%s): %s | remedy: %s",
            entity_ref,
            masked_phone,
            failure_status,
            payload.get("status_code"),
            payload.get("error_code"),
            payload.get("error_subcode"),
            entry["error"],
            remedy,
        )
        return entry

    @classmethod
    def validate_configuration(cls) -> Dict[str, Any]:
        """
        Startup diagnostic for the operations notification path.
        Reports what is missing instead of failing silently at booking time.
        """
        whatsapp_client = _wds().whatsapp_client
        recipients = cls.get_recipient_phone_numbers()
        template_name, template_lang = cls._resolve_template()
        problems: List[str] = []

        if not recipients:
            problems.append(
                "No recipient numbers: set WHATSAPP_BOOKING_NOTIFY_PHONE_1..5 in the deployment environment."
            )
        if not template_name:
            problems.append(
                "No approved template: set WHATSAPP_DEPARTMENT_NOTIFICATION_TEMPLATE so notifications "
                "work outside the 24-hour customer service window."
            )
        if not whatsapp_client.is_configured():
            problems.append(
                "Cloud API credentials missing: set WHATSAPP_ACCESS_TOKEN and WHATSAPP_PHONE_NUMBER_ID."
            )

        return {
            "ok": not problems,
            "recipients_configured": len(recipients),
            "recipients_masked": [cls._mask_phone(phone) for phone in recipients],
            "template": template_name or None,
            "template_language": template_lang,
            "template_configured": bool(template_name),
            "problems": problems,
        }

    @classmethod
    def dispatch_booking_or_enquiry_notification(cls, context: Dict[str, Any]) -> Dict[str, Any]:
        """
        Evaluates context, performs two-phase idempotency check, and dispatches WhatsApp alert
        to all 5 configured recipient numbers. Continues on failure of any single number.
        Accurately distinguishes ACCEPTED, FAILED, and UNKNOWN delivery states.
        Guaranteed to catch all exceptions and never raise or crash booking creation.
        """
        w = _wds()
        whatsapp_client = w.whatsapp_client
        logger = w.logger

        booking_ref = str(context.get("booking_ref") or context.get("bookingRef") or "N/A").strip()
        idempotency_key = cls.generate_idempotency_key("booking", booking_ref, context)

        try:
            # 1. Two-Phase Idempotency Check: prevent duplicate notifications
            claim_status = cls.claim_idempotency(idempotency_key)
            if claim_status in ("DUPLICATE", "IN_PROGRESS", "UNKNOWN_TIMEOUT"):
                logger.info(
                    "[WhatsApp Notify] Notification for booking %s skipped (idempotency state: %s).",
                    booking_ref,
                    claim_status,
                )
                return {
                    "status": "SKIPPED",
                    "reason": claim_status.lower(),
                    "booking_ref": booking_ref,
                }

            # 2. Resolve recipients
            recipients = cls.get_recipient_phone_numbers()
            if not recipients:
                logger.warning(
                    "[WhatsApp Notify] Notification skipped for %s: no recipient phones configured.",
                    booking_ref,
                )
                cls.finalize_idempotency(idempotency_key, "FAILED", [], context)
                return {
                    "status": "BYPASSED",
                    "reason": "no_recipients_configured",
                    "booking_ref": booking_ref,
                }

            # 3. Check WhatsApp client configuration
            if not whatsapp_client.is_configured():
                logger.warning(
                    "[WhatsApp Notify] Notification skipped for %s: WhatsApp Cloud API credentials not configured.",
                    booking_ref,
                )
                cls.finalize_idempotency(idempotency_key, "FAILED", [], context)
                return {
                    "status": "BYPASSED",
                    "reason": "whatsapp_client_unconfigured",
                    "booking_ref": booking_ref,
                }

            # 4. Resolve service category and format message
            category = context.get("service_category") or context.get("serviceCategory")
            service_type = context.get("service_type") or context.get("serviceType")
            dept, service_label = cls.resolve_department(category, service_type, context)
            message_body = cls.format_booking_notification(context, dept, service_label)

            template_name, template_lang = cls._resolve_template()

            # 5. Dispatch to ALL configured recipients (continue if any individual number fails)
            recipient_results: List[Dict[str, Any]] = []
            successful_count = 0
            timeout_count = 0
            window_closed_count = 0

            for phone in recipients:
                masked = cls._mask_phone(phone)
                try:
                    res = cls._send_to_recipient(phone, message_body, template_name, template_lang)
                except Exception as item_err:
                    logger.error(
                        "[WhatsApp Notify] Exception during send for %s to %s: %s",
                        booking_ref,
                        masked,
                        item_err,
                    )
                    recipient_results.append({
                        "phone": phone,
                        "masked_phone": masked,
                        "status": "FAILED",
                        "error": str(item_err),
                    })
                    continue

                entry = cls._describe_send_result(phone, masked, res, entity_ref=booking_ref)
                if entry["status"] == "ACCEPTED":
                    successful_count += 1
                elif entry["status"] == "UNKNOWN":
                    timeout_count += 1
                elif entry["status"] == "WINDOW_CLOSED":
                    window_closed_count += 1
                recipient_results.append(entry)

            # Determine overall outcome
            status_counts: Dict[str, int] = {}
            for entry in recipient_results:
                key = str(entry.get("status") or "FAILED")
                status_counts[key] = status_counts.get(key, 0) + 1
            if window_closed_count:
                logger.error(
                    "[WhatsApp Notify] %d/%d recipients for %s are outside the 24-hour window (Meta 131047). "
                    "Configure an approved WHATSAPP_DEPARTMENT_NOTIFICATION_TEMPLATE to make delivery reliable.",
                    window_closed_count,
                    len(recipients),
                    booking_ref,
                )
            if successful_count == len(recipients):
                overall_status = "ACCEPTED"
            elif successful_count > 0:
                overall_status = "PARTIALLY_ACCEPTED"
            elif timeout_count > 0:
                overall_status = "UNKNOWN"
            else:
                overall_status = "FAILED"

            # Finalize idempotency state and persist durable record
            cls.finalize_idempotency(idempotency_key, overall_status, recipient_results, context)

            logger.info(
                "[WhatsApp Notify] %s notification completed for %s: %d/%d accepted by Meta. Status: %s",
                service_label,
                booking_ref,
                successful_count,
                len(recipients),
                overall_status,
            )

            return {
                "status": overall_status,
                "service_label": service_label,
                "booking_ref": booking_ref,
                "recipients_attempted": len(recipients),
                "recipients_accepted": successful_count,
                # Backward-compatible alias for existing consumers
                "recipients_delivered": successful_count,
                "recipient_results": recipient_results,
                "template_configured": bool(template_name),
                "status_counts": status_counts,
            }

        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.exception(
                "[WhatsApp Notify] Unexpected exception during dispatch for %s: %s",
                booking_ref,
                str(exc),
            )
            cls.finalize_idempotency(idempotency_key, "FAILED", [], context)
            return {
                "status": "FAILED",
                "error": str(exc),
            }

    @classmethod
    def dispatch_charter_notification(cls, charter_req: Any) -> Dict[str, Any]:
        """
        Dispatches Air Charter notification to all 5 configured recipient numbers.
        Applies robust two-phase idempotency deduplication and error isolation.
        Accurately distinguishes ACCEPTED, FAILED, and UNKNOWN delivery states.
        Never raises: fails gracefully with error logging.
        """
        w = _wds()
        whatsapp_client = w.whatsapp_client
        logger = w.logger

        ref = getattr(charter_req, "request_reference", "N/A")
        charter_dict = {
            "request_reference": ref,
            "customer_name": getattr(charter_req, "customer_name", "N/A"),
            "email": getattr(charter_req, "email", "N/A"),
            "phone": getattr(charter_req, "phone", "N/A"),
            "trip_type": getattr(charter_req, "trip_type", "ONE_WAY"),
            "origin": getattr(charter_req, "origin", "N/A"),
            "destination": getattr(charter_req, "destination", "N/A"),
            "departure_date": getattr(charter_req, "departure_date", "N/A"),
        }
        idempotency_key = cls.generate_idempotency_key("charter", ref, charter_dict)

        try:
            # 1. Two-Phase Idempotency Check
            claim_status = cls.claim_idempotency(idempotency_key)
            if claim_status in ("DUPLICATE", "IN_PROGRESS", "UNKNOWN_TIMEOUT"):
                logger.info(
                    "[WhatsApp Notify] Notification for charter %s skipped (idempotency state: %s).",
                    ref,
                    claim_status,
                )
                return {
                    "status": "SKIPPED",
                    "reason": claim_status.lower(),
                    "request_reference": ref,
                }

            # 2. Resolve recipients
            recipients = cls.get_recipient_phone_numbers()
            if not recipients:
                logger.warning(
                    "[WhatsApp Notify] Charter notification skipped for %s: no recipient phones configured.",
                    ref,
                )
                cls.finalize_idempotency(idempotency_key, "FAILED", [], charter_dict)
                return {
                    "status": "BYPASSED",
                    "reason": "no_recipients_configured",
                    "request_reference": ref,
                }

            # 3. Check WhatsApp client configuration
            if not whatsapp_client.is_configured():
                logger.warning(
                    "[WhatsApp Notify] Charter notification skipped for %s: WhatsApp Cloud API credentials not configured.",
                    ref,
                )
                cls.finalize_idempotency(idempotency_key, "FAILED", [], charter_dict)
                return {
                    "status": "BYPASSED",
                    "reason": "whatsapp_client_unconfigured",
                    "request_reference": ref,
                }

            # 4. Format message body
            message_body = cls.format_charter_notification(charter_req)

            template_name, template_lang = cls._resolve_template()

            # 5. Dispatch to ALL configured recipients
            recipient_results: List[Dict[str, Any]] = []
            successful_count = 0
            timeout_count = 0
            window_closed_count = 0

            for phone in recipients:
                masked = cls._mask_phone(phone)
                try:
                    res = cls._send_to_recipient(phone, message_body, template_name, template_lang)
                except Exception as item_err:
                    logger.error(
                        "[WhatsApp Notify] Exception during charter send for %s to %s: %s",
                        ref,
                        masked,
                        item_err,
                    )
                    recipient_results.append({
                        "phone": masked,
                        "status": "FAILED",
                        "error": str(item_err),
                    })
                    continue

                entry = cls._describe_send_result(masked, masked, res, entity_ref=ref)
                if entry["status"] == "ACCEPTED":
                    successful_count += 1
                elif entry["status"] == "UNKNOWN":
                    timeout_count += 1
                elif entry["status"] == "WINDOW_CLOSED":
                    window_closed_count += 1
                recipient_results.append(entry)

            status_counts: Dict[str, int] = {}
            for entry in recipient_results:
                key = str(entry.get("status") or "FAILED")
                status_counts[key] = status_counts.get(key, 0) + 1
            if window_closed_count:
                logger.error(
                    "[WhatsApp Notify] %d/%d charter recipients for %s are outside the 24-hour window (Meta 131047).",
                    window_closed_count,
                    len(recipients),
                    ref,
                )
            if successful_count == len(recipients):
                overall_status = "ACCEPTED"
            elif successful_count > 0:
                overall_status = "PARTIALLY_ACCEPTED"
            elif timeout_count > 0:
                overall_status = "UNKNOWN"
            else:
                overall_status = "FAILED"

            cls.finalize_idempotency(idempotency_key, overall_status, recipient_results, charter_dict)

            logger.info(
                "[WhatsApp Notify] Air Charter notification completed for %s: %d/%d accepted by Meta. Status: %s",
                ref,
                successful_count,
                len(recipients),
                overall_status,
            )

            return {
                "status": overall_status,
                "request_reference": ref,
                "recipients_attempted": len(recipients),
                "recipients_accepted": successful_count,
                # Backward-compatible alias
                "recipients_delivered": successful_count,
                "recipient_results": recipient_results,
                "template_configured": bool(template_name),
                "status_counts": status_counts,
            }

        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.exception(
                "[WhatsApp Notify] Unexpected exception during charter dispatch for %s: %s",
                ref,
                str(exc),
            )
            cls.finalize_idempotency(idempotency_key, "FAILED", [], charter_dict)
            return {
                "status": "FAILED",
                "error": str(exc),
            }
