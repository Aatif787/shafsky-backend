"""Resolve client IP without trusting spoofable proxy headers by default."""

from fastapi import Request
from app.config import settings


def get_client_ip(request: Request) -> str:
    if settings.TRUST_PROXY:
        cf_ip = request.headers.get("CF-Connecting-IP")
        if cf_ip:
            return cf_ip.strip()
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "127.0.0.1"
