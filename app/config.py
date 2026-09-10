import os
from urllib.parse import quote
from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

load_dotenv()


def _default_environment() -> str:
    """On Render, development/test/unset become production so pre-deploy cannot fail-open."""
    explicit = (os.getenv("ENVIRONMENT") or "").strip().lower()
    on_render = bool(os.getenv("RENDER") or os.getenv("RENDER_SERVICE_ID"))
    if on_render:
        if explicit in ("staging",):
            return "staging"
        return "production"
    return explicit or "development"


def _build_redis_url() -> str:
    """Prefer explicit REDIS_URL; otherwise build from host/port/password."""
    explicit = (os.getenv("REDIS_URL") or "").strip()
    if explicit:
        return explicit
    host = os.getenv("REDIS_HOST", "localhost")
    port = os.getenv("REDIS_PORT", "6379")
    password = os.getenv("REDIS_PASSWORD", "")
    if password:
        return f"redis://:{quote(password, safe='')}@{host}:{port}/0"
    return f"redis://{host}:{port}/0"


class Settings(BaseSettings):
    PROJECT_NAME: str = "Shafsky Aviation FastAPI Backend Engine"
    ENVIRONMENT: str = _default_environment()
    DATABASE_URL: str = os.getenv("DATABASE_URL", "")
    DB_POOL_SIZE: int = int(os.getenv("DB_POOL_SIZE", "20"))
    DB_MAX_OVERFLOW: int = int(os.getenv("DB_MAX_OVERFLOW", "30"))
    DB_POOL_TIMEOUT: int = int(os.getenv("DB_POOL_TIMEOUT", "30"))
    REDIS_HOST: str = os.getenv("REDIS_HOST", "localhost")
    REDIS_PORT: int = int(os.getenv("REDIS_PORT", "6379"))
    REDIS_PASSWORD: str = os.getenv("REDIS_PASSWORD", "")
    # When true (recommended for multi-instance AWS), Redis must be reachable or readiness fails.
    REQUIRE_REDIS: bool = os.getenv("REQUIRE_REDIS", "false").lower() in ("1", "true", "yes")

    JWT_SECRET: str = os.getenv("JWT_SECRET", "")
    JWT_REFRESH_SECRET: str = os.getenv("JWT_REFRESH_SECRET", "")
    JWT_ALGORITHM: str = os.getenv("JWT_ALGORITHM", "RS256")
    JWT_PRIVATE_KEY: str = os.getenv("JWT_PRIVATE_KEY", "")
    JWT_PUBLIC_KEY: str = os.getenv("JWT_PUBLIC_KEY", "")
    JWT_PREVIOUS_PUBLIC_KEYS: str = os.getenv("JWT_PREVIOUS_PUBLIC_KEYS", "")
    JWT_ISSUER: str = os.getenv("JWT_ISSUER", "shafsky-backend")
    JWT_AUDIENCE: str = os.getenv("JWT_AUDIENCE", "shafsky-api")
    ACCESS_TOKEN_EXPIRE_MINUTES: int = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "15"))
    REFRESH_TOKEN_EXPIRE_DAYS: int = int(os.getenv("REFRESH_TOKEN_EXPIRE_DAYS", "7"))
    # Allow legacy HS256 fallback for JWT verification. Defaults to true in non-production, false in production.
    ALLOW_HS256_LEGACY_FALLBACK: bool = os.getenv("ALLOW_HS256_LEGACY_FALLBACK", "true" if os.getenv("ENVIRONMENT", "development") != "production" else "false").lower() in ("1", "true", "yes")

    REDIS_URL: str = _build_redis_url()

    AVIATION_EDGE_API_KEY: str = os.getenv("AVIATION_EDGE_API_KEY", "")
    AVIATION_EDGE_BASE_URL: str = os.getenv("AVIATION_EDGE_BASE_URL", "https://aviation-edge.com/v2/public")
    AVIATION_EDGE_TIMEOUT: float = float(os.getenv("AVIATION_EDGE_TIMEOUT", "12"))
    AVIATION_EDGE_MAX_RETRIES: int = int(os.getenv("AVIATION_EDGE_MAX_RETRIES", "2"))
    # `flightsFuture` refuses dates nearer than roughly a week out. Queries inside
    # that window are skipped; the exact boundary is also learned at runtime.
    AVIATION_EDGE_FUTURE_MIN_DAYS: int = int(os.getenv("AVIATION_EDGE_FUTURE_MIN_DAYS", "8"))

    # AviationStack — secondary provider (WhatsApp flight confirmation only)
    AVIATIONSTACK_API_KEY: str = os.getenv("AVIATIONSTACK_API_KEY", "")
    AVIATIONSTACK_BASE_URL: str = os.getenv("AVIATIONSTACK_BASE_URL", "https://api.aviationstack.com/v1")
    AVIATIONSTACK_TIMEOUT: float = float(os.getenv("AVIATIONSTACK_TIMEOUT", "10"))
    AVIATIONSTACK_MAX_RETRIES: int = int(os.getenv("AVIATIONSTACK_MAX_RETRIES", "2"))
    AVIATIONSTACK_CACHE_TTL_SECONDS: int = int(os.getenv("AVIATIONSTACK_CACHE_TTL_SECONDS", "300"))

    # Meta WhatsApp Cloud API
    WHATSAPP_ACCESS_TOKEN: str = os.getenv("WHATSAPP_ACCESS_TOKEN", "")
    WHATSAPP_PHONE_NUMBER_ID: str = os.getenv("WHATSAPP_PHONE_NUMBER_ID", "")
    WHATSAPP_BUSINESS_ACCOUNT_ID: str = os.getenv("WHATSAPP_BUSINESS_ACCOUNT_ID", "")
    WHATSAPP_WEBHOOK_VERIFY_TOKEN: str = os.getenv("WHATSAPP_WEBHOOK_VERIFY_TOKEN", "")
    WHATSAPP_API_VERSION: str = os.getenv("WHATSAPP_API_VERSION", "v21.0")
    WHATSAPP_APP_SECRET: str = os.getenv("WHATSAPP_APP_SECRET", os.getenv("META_APP_SECRET", os.getenv("APP_SECRET", "")))
    WHATSAPP_OFFICER_NOTIFY_PHONE: str = os.getenv("WHATSAPP_OFFICER_NOTIFY_PHONE", "")

    # Razorpay Payment Gateway
    RAZORPAY_KEY_ID: str = os.getenv("RAZORPAY_KEY_ID", "")
    RAZORPAY_KEY_SECRET: str = os.getenv("RAZORPAY_KEY_SECRET", "")

    RESEND_API_KEY: str = os.getenv("RESEND_API_KEY", "")
    EMAIL_FROM: str = os.getenv("EMAIL_FROM", os.getenv("RESEND_FROM_EMAIL", ""))
    RESEND_FROM_EMAIL: str = os.getenv("RESEND_FROM_EMAIL", os.getenv("EMAIL_FROM", ""))
    EMAIL_REPLY_TO: str = os.getenv("EMAIL_REPLY_TO", "")
    ADMIN_NOTIFICATION_EMAILS: str = os.getenv("ADMIN_NOTIFICATION_EMAILS", "")

    ALLOWED_ORIGINS_STR: str = os.getenv(
        "ALLOWED_ORIGINS",
        "http://localhost:5173,http://127.0.0.1:5173,http://localhost:5174,http://127.0.0.1:5174,http://localhost:3000,http://127.0.0.1:3000",
    )
    CORS_ALLOW_CREDENTIALS: bool = os.getenv("CORS_ALLOW_CREDENTIALS", "true").lower() in ("1", "true", "yes")
    # Behind ALB/CloudFront/Cloudflare set TRUST_PROXY=true so rate limits use X-Forwarded-For.
    TRUST_PROXY: bool = os.getenv("TRUST_PROXY", "false").lower() in ("1", "true", "yes")
    # Only immediate peers in these CIDRs may supply forwarded client-IP headers.
    # Add official Cloudflare CIDRs explicitly when Cloudflare connects directly.
    TRUSTED_PROXY_CIDRS: str = os.getenv(
        "TRUSTED_PROXY_CIDRS",
        "127.0.0.1/32,::1/128,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16",
    )
    # Cookie SameSite: use "none" for cross-site Vercel frontend + Azure/API on another domain.
    # Allowed: lax | strict | none  (none requires Secure in production browsers)
    COOKIE_SAMESITE: str = (os.getenv("COOKIE_SAMESITE") or "").strip().lower() or (
        "none" if _default_environment().lower() not in ("development", "dev", "test", "testing") else "lax"
    )

    IDEMPOTENCY_LOCK_TTL: int = int(os.getenv("IDEMPOTENCY_LOCK_TTL", "120"))
    IDEMPOTENCY_CACHE_TTL: int = int(os.getenv("IDEMPOTENCY_CACHE_TTL", "86400"))
    IDEMPOTENCY_MAX_KEY_LENGTH: int = int(os.getenv("IDEMPOTENCY_MAX_KEY_LENGTH", "256"))

    @property
    def is_production(self) -> bool:
        return self.ENVIRONMENT.lower() not in ("development", "dev", "test", "testing")

    @property
    def ALLOWED_ORIGINS(self) -> list:
        local_dev = [
            "http://localhost:5173",
            "http://127.0.0.1:5173",
            "http://localhost:5174",
            "http://127.0.0.1:5174",
            "http://localhost:3000",
            "http://127.0.0.1:3000",
        ]
        raw = os.getenv("ALLOWED_ORIGINS", self.ALLOWED_ORIGINS_STR)
        parsed: list[str] = []
        if raw and raw.strip().startswith("["):
            try:
                import json
                loaded = json.loads(raw)
                if isinstance(loaded, list):
                    parsed = [str(o).strip() for o in loaded if str(o).strip()]
            except Exception:
                parsed = []
        elif raw:
            parsed = [o.strip() for o in raw.split(",") if o.strip()]
        merged = []
        for origin in parsed:
            if origin and origin not in merged:
                merged.append(origin)
        if not self.is_production:
            for origin in local_dev:
                if origin not in merged:
                    merged.append(origin)
        return merged

    model_config = SettingsConfigDict(env_file=".env", case_sensitive=True, extra="ignore")

settings = Settings()
