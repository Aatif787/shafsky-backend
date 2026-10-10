"""Shared, environment-aware browser-origin allowlist.

Single source of truth used by BOTH:
- the CORS middleware (``app.main``), and
- the cookie-authenticated origin check (``app.routers.auth_router``)

so the two can never drift apart again.

Production deliberately excludes:
- plain ``http://`` origins (production frontends are HTTPS-only),
- ``localhost`` / ``127.0.0.1`` (developer tooling),
- ``*.ngrok-*`` tunnels (any third party can register one).

Development/testing keeps the historical permissive regex so local Vite dev
servers and ngrok-tunnelled mobile testing keep working.

Exact entries from ``settings.ALLOWED_ORIGINS`` normally win over the regex —
that is the ops escape hatch for pinning precise hosts. In production,
loopback and ngrok entries are filtered out of that list (with a one-time
warning) so stale development allowlists cannot silently re-open the API to
developer tooling.

Known tradeoff (documented, accepted for Vercel preview deployments): the
``shafsky*`` Vercel tenant prefix still admits any Vercel project whose name
starts with "shafsky" over HTTPS. Pin exact origins via ALLOWED_ORIGINS to
close that gap; the regex is the fallback, not the primary control.
"""
import logging
import re
from typing import Dict, List
from urllib.parse import urlsplit

from app.config import settings

_logger = logging.getLogger("shafsky.security.cors_origins")

_DEV_ORIGIN_REGEX = (
    r"^https?://(localhost|127\.0\.0\.1|.*\.ngrok-free\.(dev|app)|.*\.ngrok\.io|"
    r"(shafsky[a-zA-Z0-9_-]*|shafsky)\.vercel\.app|"
    r"([a-zA-Z0-9_-]+\.)*shafskyaviation\.(in|com)|"
    r"([a-zA-Z0-9_-]+\.)*shafsky\.(in|com)"
    r")(:\d+)?$"
)

# HTTPS-only, no localhost, no tunnels.
_PROD_ORIGIN_REGEX = (
    r"^https://((shafsky[a-zA-Z0-9_-]*|shafsky)\.vercel\.app|"
    r"([a-zA-Z0-9_-]+\.)*shafskyaviation\.(in|com)|"
    r"([a-zA-Z0-9_-]+\.)*shafsky\.(in|com)"
    r")(:\d+)?$"
)

_LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "0.0.0.0", "::1", "[::1]"}

_COMPILED: Dict[bool, "re.Pattern[str]"] = {}
_ALLOWED_CACHE: Dict[bool, List[str]] = {}


def _host_of(origin: str) -> str:
    try:
        return (urlsplit(origin).hostname or "").lower()
    except Exception:
        return ""


def _is_loopback_origin(origin: str) -> bool:
    host = _host_of(origin)
    return host in _LOOPBACK_HOSTS or host.endswith(".localhost")


def _is_tunnel_origin(origin: str) -> bool:
    host = _host_of(origin)
    if host.startswith("*.") :
        host = host[2:]
    return ".ngrok-" in host or host.endswith(".ngrok.io") or ".ngrok" in host


def browser_origin_regex() -> "re.Pattern[str]":
    """Compiled origin regex matching the current runtime environment."""
    key = bool(settings.is_production)
    if not _COMPILED:
        _COMPILED.clear()
    compiled = _COMPILED.get(key)
    if compiled is None:
        compiled = re.compile(_PROD_ORIGIN_REGEX if key else _DEV_ORIGIN_REGEX)
        _COMPILED[key] = compiled
    return compiled


def allowed_origins() -> List[str]:
    """Exact-origin allowlist for the current environment.

    In production, development-only entries (loopback hosts, ngrok tunnels)
    are removed and reported once, so a stale shared .env cannot re-open
    credentialed CORS to developer tooling.
    """
    key = bool(settings.is_production)
    cached = _ALLOWED_CACHE.get(key)
    if cached is not None:
        return list(cached)
    try:
        raw = list(getattr(settings, "ALLOWED_ORIGINS", []) or [])
    except Exception:
        raw = []
    if key:
        kept: List[str] = []
        dropped: List[str] = []
        for item in raw:
            s = str(item).strip()
            if not s:
                continue
            if _is_loopback_origin(s) or _is_tunnel_origin(s):
                dropped.append(s)
            else:
                kept.append(s)
        if dropped:
            _logger.warning(
                "ALLOWED_ORIGINS contains development-only entries; "
                "dropping them in production: %s",
                dropped,
            )
        result = kept
    else:
        result = [str(item).strip() for item in raw if str(item).strip()]
    _ALLOWED_CACHE[key] = list(result)
    return list(result)


def origin_is_trusted(origin: str) -> bool:
    """True when a browser Origin header may make credentialed requests.

    Checked in order: exact (environment-filtered) ``ALLOWED_ORIGINS`` entries,
    then the environment-aware regex.
    """
    if not origin:
        return False
    normalized = origin.rstrip("/")
    if normalized in {item.rstrip("/") for item in allowed_origins()}:
        return True
    return bool(browser_origin_regex().match(origin))
