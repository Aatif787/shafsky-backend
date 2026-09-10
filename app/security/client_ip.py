"""Resolve client IP without trusting spoofable proxy headers by default."""

import ipaddress

from fastapi import Request
from app.config import settings


def _is_trusted_proxy(peer: str) -> bool:
    try:
        address = ipaddress.ip_address(peer)
    except ValueError:
        return False
    for raw_cidr in str(getattr(settings, "TRUSTED_PROXY_CIDRS", "")).split(","):
        cidr = raw_cidr.strip()
        if not cidr:
            continue
        try:
            if address in ipaddress.ip_network(cidr, strict=False):
                return True
        except ValueError:
            continue
    return False


def get_client_ip(request: Request) -> str:
    peer = request.client.host if request.client else "127.0.0.1"
    if settings.TRUST_PROXY and _is_trusted_proxy(peer):
        cf_ip = request.headers.get("CF-Connecting-IP")
        if cf_ip:
            candidate = cf_ip.strip()
            try:
                return str(ipaddress.ip_address(candidate))
            except ValueError:
                pass
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            candidate = forwarded.split(",")[0].strip()
            try:
                return str(ipaddress.ip_address(candidate))
            except ValueError:
                pass
    return peer
