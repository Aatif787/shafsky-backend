"""
ICICI Bank TSP UAT payment provider (Standard / Redirection payType=0).

Initiate Sale uses JSON + secureHash field (Step Wise V1 hashing).
STATUS / REFUND use application/x-www-form-urlencoded against the command API.
Secrets stay backend-only; never log ICICI_KEY or raw credentials.
"""

from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Dict, Mapping, MutableMapping, Optional
from urllib.parse import urlencode, urlparse

import httpx

from app.config import settings
from app.providers.icici_hash import (
    attach_secure_hash_v1,
    verify_callback_or_status_response_hash,
)

logger = logging.getLogger(__name__)

_TXN_NO_RE = re.compile(r"^[A-Za-z0-9]{1,20}$")
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


def format_icici_amount(amount: float | Decimal | str) -> str:
    """ICICI amount: numeric string with exactly 2 decimal places (not paise)."""
    value = Decimal(str(amount)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if value <= 0:
        raise ValueError("ICICI amount must be greater than zero.")
    return f"{value:.2f}"


def format_icici_txn_date(when: Optional[datetime] = None) -> str:
    """YYYYMMDDHHMISS in server local/UTC consistent formatting (UTC)."""
    dt = when or datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y%m%d%H%M%S")


def generate_merchant_txn_no(prefix: str = "SF") -> str:
    """Alphanumeric, max 20 chars, no special characters."""
    raw = f"{prefix}{uuid.uuid4().hex}".upper().replace("-", "")
    txn = raw[:20]
    if not _TXN_NO_RE.match(txn):
        raise ValueError("Generated merchantTxnNo is invalid.")
    return txn


def sanitize_phone(phone: Optional[str]) -> str:
    digits = re.sub(r"\D+", "", phone or "")
    return digits[-15:] if digits else ""


def _public_https_url(url: str, *, label: str) -> str:
    parsed = urlparse((url or "").strip())
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not host or host in _LOCAL_HOSTS:
        raise ValueError(
            f"{label} must be a publicly reachable HTTPS URL (not localhost). "
            "Set ICICI_RETURN_URL or PUBLIC_BACKEND_URL."
        )
    return parsed.geturl().rstrip("/")


class IciciPaymentProvider:
    """ICICI TSP gateway for hosted Standard Method (payType=0)."""

    INIT_SUCCESS_CODE = "R1000"
    STATUS_OK_CODE = "000"
    TXN_SUCCESS = "SUC"
    TXN_RESPONSE_OK = "0000"

    def __init__(self) -> None:
        self.reload()

    def reload(self) -> None:
        self.mid = (getattr(settings, "ICICI_MID", None) or "").strip()
        self.key = (getattr(settings, "ICICI_KEY", None) or "").strip()
        self.agg_id = (getattr(settings, "ICICI_AGG_ID", None) or "").strip()
        self.env = (getattr(settings, "ICICI_ENV", None) or "UAT").strip().upper()
        self.sale_url = (
            getattr(settings, "ICICI_SALE_URL", None)
            or "https://pgpayuat.icicibank.com/tsp/pg/api/v2/initiateSale"
        ).strip()
        self.command_url = (
            getattr(settings, "ICICI_COMMAND_URL", None)
            or "https://pgpayuat.icicibank.com/tsp/pg/api/command"
        ).strip()
        self.timeout = float(getattr(settings, "ICICI_HTTP_TIMEOUT", 30) or 30)

    def is_configured(self) -> bool:
        return bool(self.mid and self.key and self.agg_id)

    def ensure_configured(self) -> None:
        self.reload()
        if not self.is_configured():
            raise ValueError("ICICI payment gateway is not configured.")

    def resolve_return_url(self) -> str:
        explicit = (getattr(settings, "ICICI_RETURN_URL", None) or "").strip()
        if explicit:
            return _public_https_url(explicit, label="ICICI_RETURN_URL")
        base = (getattr(settings, "PUBLIC_BACKEND_URL", None) or "").strip()
        if not base:
            raise ValueError(
                "ICICI return URL is not configured. Set ICICI_RETURN_URL or PUBLIC_BACKEND_URL "
                "to the public HTTPS backend (e.g. https://shafsky-backend-1.onrender.com)."
            )
        base = _public_https_url(base, label="PUBLIC_BACKEND_URL")
        return f"{base}/api/payments/icici/callback"

    def resolve_customer_return_url(
        self,
        booking_ref: str,
        *,
        success: bool,
        pending: bool = False,
        cancelled: bool = False,
    ) -> str:
        base = (getattr(settings, "ICICI_CUSTOMER_RETURN_URL", None) or "").strip()
        if not base:
            frontend = (getattr(settings, "PUBLIC_FRONTEND_URL", None) or "").strip()
            if frontend:
                base = f"{frontend.rstrip('/')}/book/payment-result"
            else:
                # Fallback: still redirect to aviation marketing site path if configured
                base = "https://shafskyaviation.com/book/payment-result"
        sep = "&" if "?" in base else "?"
        if cancelled:
            status = "cancelled"
        elif pending:
            status = "pending"
        else:
            status = "success" if success else "failed"
        return f"{base}{sep}ref={booking_ref}&status={status}&gateway=icici"

    def build_initiate_sale_request(
        self,
        *,
        merchant_txn_no: str,
        amount: float | Decimal | str,
        customer_email: str,
        customer_mobile: str,
        customer_name: str,
        return_url: Optional[str] = None,
        addl_param1: str = "",
        addl_param2: str = "",
        txn_date: Optional[str] = None,
    ) -> Dict[str, Any]:
        self.ensure_configured()
        if not _TXN_NO_RE.match(merchant_txn_no):
            raise ValueError("merchantTxnNo must be alphanumeric and at most 20 characters.")

        payload: Dict[str, Any] = {
            "merchantId": self.mid,
            "aggregatorID": self.agg_id,
            "merchantTxnNo": merchant_txn_no,
            "amount": format_icici_amount(amount),
            "currencyCode": "356",
            "payType": "0",
            "customerEmailID": (customer_email or "").strip(),
            "transactionType": "SALE",
            "returnURL": return_url or self.resolve_return_url(),
            "txnDate": txn_date or format_icici_txn_date(),
        }
        mobile = sanitize_phone(customer_mobile)
        if mobile:
            payload["customerMobileNo"] = mobile
        name = (customer_name or "").strip()
        if name:
            payload["customerName"] = name
        if addl_param1:
            payload["addlParam1"] = str(addl_param1)[:100]
        if addl_param2:
            payload["addlParam2"] = str(addl_param2)[:100]

        attach_secure_hash_v1(payload, self.key)
        return payload

    def build_redirect_url(self, redirect_uri: str, tran_ctx: str) -> str:
        base = (redirect_uri or "").strip()
        ctx = (tran_ctx or "").strip()
        if not base or not ctx:
            raise ValueError("ICICI redirectURI and tranCtx are required.")
        if "tranCtx=" in base:
            return base
        sep = "&" if "?" in base else "?"
        return f"{base}{sep}tranCtx={ctx}"

    def initiate_sale(self, request_body: Mapping[str, Any]) -> Dict[str, Any]:
        """POST JSON initiateSale. Returns sanitized result dict."""
        self.ensure_configured()

        try:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.post(
                    self.sale_url,
                    json=dict(request_body),
                    headers={"Content-Type": "application/json", "Accept": "application/json"},
                )
        except httpx.TimeoutException:
            logger.error("[ICICI] initiateSale timeout merchantTxnNo=%s", request_body.get("merchantTxnNo"))
            return {"success": False, "error": "ICICI initiateSale timed out.", "timeout": True}
        except httpx.HTTPError as exc:
            logger.error("[ICICI] initiateSale network error: %s", type(exc).__name__)
            return {"success": False, "error": "ICICI initiateSale network error."}

        try:
            data = response.json()
        except Exception:
            logger.error("[ICICI] initiateSale non-JSON response status=%s", response.status_code)
            return {
                "success": False,
                "error": "ICICI initiateSale returned an invalid response.",
                "http_status": response.status_code,
            }

        if not isinstance(data, dict):
            return {"success": False, "error": "ICICI initiateSale response format invalid."}

        response_code = str(data.get("responseCode") or "")
        ok = response_code == self.INIT_SUCCESS_CODE
        redirect_uri = str(data.get("redirectURI") or "")
        tran_ctx = str(data.get("tranCtx") or "")
        result: Dict[str, Any] = {
            "success": ok,
            "responseCode": response_code,
            "merchantId": data.get("merchantId"),
            "aggregatorID": data.get("aggregatorID"),
            "merchantTxnNo": data.get("merchantTxnNo"),
            "redirectURI": redirect_uri,
            "tranCtx": tran_ctx,
            "showOTPCapturePage": data.get("showOTPCapturePage"),
            "generateOTPURI": data.get("generateOTPURI"),
            "verifyOTPURI": data.get("verifyOTPURI"),
            "authorizeURI": data.get("authorizeURI"),
            "raw": self._sanitize_gateway_payload(data),
        }
        if ok and redirect_uri and tran_ctx:
            result["payment_url"] = self.build_redirect_url(redirect_uri, tran_ctx)
        elif not ok:
            result["error"] = f"ICICI initiateSale failed with responseCode={response_code or 'unknown'}"
        return result

    def build_command_request(
        self,
        *,
        transaction_type: str,
        merchant_txn_no: str,
        original_txn_no: str,
        amount: Optional[float | Decimal | str] = None,
        extra: Optional[Mapping[str, Any]] = None,
    ) -> Dict[str, Any]:
        self.ensure_configured()
        if not _TXN_NO_RE.match(merchant_txn_no):
            raise ValueError("merchantTxnNo must be alphanumeric and at most 20 characters.")

        payload: Dict[str, Any] = {
            "merchantId": self.mid,
            "aggregatorID": self.agg_id,
            "merchantTxnNo": merchant_txn_no,
            "originalTxnNo": original_txn_no,
            "transactionType": transaction_type,
        }
        if amount is not None:
            payload["amount"] = format_icici_amount(amount)
        if extra:
            for key, value in extra.items():
                if value is not None and str(key).lower() not in {"securehash", "secure_hash"}:
                    payload[str(key)] = value
        attach_secure_hash_v1(payload, self.key)
        return payload

    def post_command(self, form_fields: Mapping[str, Any]) -> Dict[str, Any]:
        """POST application/x-www-form-urlencoded to command URL."""
        self.reload()
        try:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.post(
                    self.command_url,
                    content=urlencode({k: str(v) for k, v in form_fields.items()}),
                    headers={
                        "Content-Type": "application/x-www-form-urlencoded",
                        "Accept": "application/json, text/plain, */*",
                    },
                )
        except httpx.TimeoutException:
            logger.error("[ICICI] command timeout type=%s", form_fields.get("transactionType"))
            return {"success": False, "error": "ICICI command API timed out.", "timeout": True}
        except httpx.HTTPError as exc:
            logger.error("[ICICI] command network error: %s", type(exc).__name__)
            return {"success": False, "error": "ICICI command API network error."}

        data = self._parse_command_response(response)
        if not isinstance(data, dict):
            return {
                "success": False,
                "error": "ICICI command API returned an invalid response.",
                "http_status": response.status_code,
            }
        return {"success": True, "http_status": response.status_code, "data": self._sanitize_gateway_payload(data), "raw": data}

    def check_transaction_status(
        self,
        *,
        merchant_txn_no: str,
        original_txn_no: Optional[str] = None,
    ) -> Dict[str, Any]:
        original = original_txn_no or merchant_txn_no
        body = self.build_command_request(
            transaction_type="STATUS",
            merchant_txn_no=merchant_txn_no,
            original_txn_no=original,
        )
        result = self.post_command(body)
        if not result.get("success"):
            return result
        data = result.get("raw") or {}
        if not self.verify_response_hash(data):
            logger.warning("[ICICI] STATUS secureHash verification failed txn=%s", merchant_txn_no)
            return {"success": False, "error": "ICICI STATUS response hash verification failed.", "data": result.get("data")}

        response_code = str(data.get("responseCode") or "")
        txn_status = str(data.get("txnStatus") or "").upper()
        txn_response_code = str(data.get("txnResponseCode") or "")
        paid = (
            response_code == self.STATUS_OK_CODE
            and txn_status == self.TXN_SUCCESS
            and txn_response_code == self.TXN_RESPONSE_OK
        )
        pending = txn_status in {"PEN", "PENDING", "PEN"} or response_code in {"R1001", "100"}
        failed = txn_status in {"FAIL", "FAL", "FAILED", "CAN", "CANCELLED"} or (
            response_code == self.STATUS_OK_CODE and txn_status and not paid and not pending
        )
        return {
            "success": True,
            "paid": paid,
            "pending": bool(pending) and not paid,
            "failed": bool(failed) and not paid,
            "responseCode": response_code,
            "txnStatus": txn_status,
            "txnResponseCode": txn_response_code,
            "txnRespDescription": data.get("txnRespDescription") or data.get("respDescription"),
            "txnID": data.get("txnID"),
            "paymentDateTime": data.get("paymentDateTime"),
            "txnAuthID": data.get("txnAuthID"),
            "merchantTxnNo": data.get("merchantTxnNo") or merchant_txn_no,
            "amount": data.get("amount"),
            "data": result.get("data"),
        }

    def refund(
        self,
        *,
        merchant_txn_no: str,
        original_txn_no: str,
        amount: float | Decimal | str,
        extra: Optional[Mapping[str, Any]] = None,
    ) -> Dict[str, Any]:
        body = self.build_command_request(
            transaction_type="REFUND",
            merchant_txn_no=merchant_txn_no,
            original_txn_no=original_txn_no,
            amount=amount,
            extra=extra,
        )
        result = self.post_command(body)
        if result.get("success") and isinstance(result.get("raw"), dict):
            if not self.verify_response_hash(result["raw"]):
                return {"success": False, "error": "ICICI REFUND response hash verification failed."}
        return result

    def verify_response_hash(
        self,
        params: Mapping[str, Any],
        *,
        header_securehash: Optional[str] = None,
    ) -> bool:
        """
        Verify ICICI response secureHash.

        Callback (returnURL): Gateway Interface Spec V0.4 page 65 (form POST) +
        Hash Calculation V1 on pages 68–69 — NOT Hash Calculation (v2) page 70.

        STATUS/Refund JSON with secureHash in body: page 66 samples + page 68 Note 2 ⇒ V1.
        """
        self.reload()
        return verify_callback_or_status_response_hash(
            params,
            self.key,
            header_securehash=header_securehash,
        )

    def verify_callback_identity(
        self,
        params: Mapping[str, Any],
        *,
        expected_merchant_txn_no: str,
        expected_amount: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Validate callback fields + secureHash. Does not alone confirm payment success."""
        errors: list[str] = []
        merchant_id = str(params.get("merchantId") or params.get("merchantID") or "").strip()
        aggregator = str(params.get("aggregatorID") or params.get("aggregatorId") or "").strip()
        merchant_txn = str(params.get("merchantTxnNo") or "")

        if not merchant_id:
            errors.append("merchantId missing")
        elif merchant_id != self.mid:
            errors.append("merchantId mismatch")
        if not aggregator:
            errors.append("aggregatorID missing")
        elif aggregator != self.agg_id:
            errors.append("aggregatorID mismatch")
        if merchant_txn != expected_merchant_txn_no:
            errors.append("merchantTxnNo mismatch")
        if expected_amount is not None and params.get("amount") not in (None, ""):
            try:
                if format_icici_amount(params.get("amount")) != format_icici_amount(expected_amount):
                    errors.append("amount mismatch")
            except Exception:
                errors.append("amount invalid")
        if not self.verify_response_hash(params):
            errors.append("secureHash verification failed")

        return {"ok": not errors, "errors": errors}

    @staticmethod
    def flatten_callback_payload(raw: Mapping[str, Any] | list | None) -> Dict[str, Any]:
        """Normalize form/json callback bodies into a flat string-key dict."""
        if raw is None:
            return {}
        if isinstance(raw, list):
            # atypical; ignore
            return {}
        flat: Dict[str, Any] = {}
        for key, value in raw.items():
            if isinstance(value, list) and len(value) == 1:
                flat[str(key)] = value[0]
            else:
                flat[str(key)] = value
        return flat

    @staticmethod
    def _sanitize_gateway_payload(data: Mapping[str, Any]) -> Dict[str, Any]:
        blocked = {"cardnumber", "cardNumber", "cvv", "CVV", "expiry", "secureHash", "securehash"}
        out: Dict[str, Any] = {}
        for key, value in data.items():
            if key in blocked or str(key).lower() in {"securehash", "icici_key", "key"}:
                continue
            out[str(key)] = value
        return out

    @staticmethod
    def _parse_command_response(response: httpx.Response) -> Any:
        content_type = (response.headers.get("content-type") or "").lower()
        text = response.text or ""
        if "json" in content_type or text.strip().startswith(("{", "[")):
            try:
                return response.json()
            except Exception:
                pass
        # Some gateways return form-urlencoded bodies
        from urllib.parse import parse_qs

        parsed = parse_qs(text, keep_blank_values=False)
        if parsed:
            return {k: (v[0] if len(v) == 1 else v) for k, v in parsed.items()}
        return {"rawText": text[:2000], "http_status": response.status_code}


icici_provider = IciciPaymentProvider()
