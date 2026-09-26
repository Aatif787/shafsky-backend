"""
ICICI Bank TSP secureHash helpers.

Authoritative source: Gateway Interface Specification V0.4 (Orange PG).

Hash Calculation V1 — Document pages 68–69 (TOC: “Hash Calculation”):
  Step 1: Concatenate parameter values (if not null and not an empty string)
          in ascending order of parameter names.
  Step 2: HMAC-SHA256 with the merchant key shared by ICICI.
  Step 3–4: HEX, lowercase → secureHash field.
  Note 1: Do not omit extra non-empty response/request parameters merely
          because they are absent from the published field list; only null/empty
          values may be ignored for hash calculation.
  Note 2: When hash version is unspecified, assume V1.

Payment Response (callback) — Document page 65:
  Browser POSTs application/x-www-form-urlencoded to merchant returnURL with
  secureHash in the form body. Callback verification MUST use Hash Calc V1
  (secureHash field), NOT Hash Calculation (v2) on page 70.

Transaction Status / Refund JSON samples — Document page 66:
  Responses include secureHash inside the JSON body. With no V2 version marker,
  Note 2 on page 68 ⇒ Hash Calc V1 over response parameters.

Hash Calculation (v2) — Document page 70:
  Minified JSON + HMAC-SHA256 → HTTP header “securehash”. Only for APIs that
  explicitly use the V2 header mechanism. Kept isolated; not used for the
  hosted payment returnURL callback (page 65).

HMAC sample code (vendor): key encoded UTF-8; message encoded ASCII
(matches Java example on pages 68–69 / 70).
"""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any, Mapping, MutableMapping, Optional


_HASH_EXCLUDE_KEYS = {"securehash", "secure_hash"}


def _is_empty(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, bool):
        return not value
    if isinstance(value, str):
        cleaned = value.strip()
        return cleaned == "" or cleaned.lower() == "false"
    return False


def build_v1_hash_payload(params: Mapping[str, Any]) -> str:
    """
    Gateway Interface Spec V0.4 page 68 Step 1:
    concatenate non-empty values in ascending parameter-name order.
    """
    parts: list[str] = []
    for key in sorted(params.keys(), key=lambda k: str(k)):
        if str(key).lower() in _HASH_EXCLUDE_KEYS:
            continue
        value = params[key]
        if _is_empty(value):
            continue
        if value is True:
            parts.append("true")
        elif isinstance(value, (dict, list)):
            # Nested structures are not described for V1 form callbacks; stringify
            # stably so unexpected nested values still participate (Note 1).
            parts.append(json.dumps(value, separators=(",", ":"), ensure_ascii=True, sort_keys=True))
        else:
            parts.append(str(value))
    return "".join(parts)


def hmac_sha256_hex_lower(secret: str, message: str) -> str:
    """
    Gateway Interface Spec V0.4 pages 68–69 Java sample:
    key = UTF-8 bytes; msg = ASCII bytes; digest = lowercase hex.
    """
    key = secret.encode("utf-8")
    try:
        msg_bytes = message.encode("ascii")
    except UnicodeEncodeError:
        # Spec samples are ASCII; fall back only if a value contains non-ASCII.
        msg_bytes = message.encode("utf-8")
    digest = hmac.new(key, msg_bytes, hashlib.sha256).hexdigest()
    return digest.lower()


def generate_secure_hash_v1(params: Mapping[str, Any], secret_key: str) -> str:
    """Hash Calculation V1 (pages 68–69) → secureHash field."""
    if not secret_key:
        raise ValueError("ICICI secret key is required to generate secureHash.")
    payload = build_v1_hash_payload(params)
    return hmac_sha256_hex_lower(secret_key, payload)


def attach_secure_hash_v1(
    params: MutableMapping[str, Any],
    secret_key: str,
    field_name: str = "secureHash",
) -> MutableMapping[str, Any]:
    cleaned = {k: v for k, v in params.items() if str(k).lower() not in _HASH_EXCLUDE_KEYS}
    params.clear()
    params.update(cleaned)
    params[field_name] = generate_secure_hash_v1(params, secret_key)
    return params


def verify_secure_hash_v1(
    params: Mapping[str, Any],
    secret_key: str,
    received_hash: Optional[str] = None,
) -> bool:
    """
    Callback / STATUS response verification per pages 65 + 68:
    recalculate V1 hash over all non-empty received parameters and
    constant-time compare to secureHash.
    """
    if not secret_key:
        return False
    provided = received_hash
    if provided is None:
        for key, value in params.items():
            if str(key).lower() in _HASH_EXCLUDE_KEYS:
                provided = value
                break
    if not provided or not isinstance(provided, str):
        return False
    expected = generate_secure_hash_v1(params, secret_key)
    return hmac.compare_digest(expected.lower(), provided.strip().lower())


def minify_json_for_v2(body: Mapping[str, Any]) -> str:
    """Hash Calculation (v2) page 70 Step 1: minified JSON string."""
    return json.dumps(body, separators=(",", ":"), ensure_ascii=False)


def generate_secure_hash_v2(body: Mapping[str, Any], secret_key: str) -> str:
    """Hash Calculation (v2) page 70 → HTTP header securehash (not callback form)."""
    if not secret_key:
        raise ValueError("ICICI secret key is required to generate V2 securehash.")
    return hmac_sha256_hex_lower(secret_key, minify_json_for_v2(body))


def verify_secure_hash_v2(
    body: Mapping[str, Any],
    secret_key: str,
    received_header_hash: Optional[str],
) -> bool:
    if not secret_key or not received_header_hash:
        return False
    expected = generate_secure_hash_v2(body, secret_key)
    return hmac.compare_digest(expected.lower(), received_header_hash.strip().lower())


def verify_callback_or_status_response_hash(
    params: Mapping[str, Any],
    secret_key: str,
    *,
    header_securehash: Optional[str] = None,
) -> bool:
    """
    Prefer V1 secureHash field (pages 65–66 + 68).
    Only if the body has no secureHash but a V2 header is present, try page 70 V2.
    """
    has_body_hash = any(str(k).lower() in _HASH_EXCLUDE_KEYS for k in params.keys())
    if has_body_hash:
        return verify_secure_hash_v1(params, secret_key)
    if header_securehash:
        body = {k: v for k, v in params.items() if str(k).lower() not in _HASH_EXCLUDE_KEYS}
        return verify_secure_hash_v2(body, secret_key, header_securehash)
    return False
