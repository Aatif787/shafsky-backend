import os
from dotenv import load_dotenv
from pydantic_settings import BaseSettings

load_dotenv()

class Settings(BaseSettings):
    PROJECT_NAME: str = "Shafsky Aviation FastAPI Backend Engine"
    ENVIRONMENT: str = os.getenv("ENVIRONMENT", "development")
    DATABASE_URL: str = os.getenv("DATABASE_URL", "")
    REDIS_HOST: str = os.getenv("REDIS_HOST", "localhost")
    REDIS_PORT: int = int(os.getenv("REDIS_PORT", "6379"))
    REDIS_PASSWORD: str = os.getenv("REDIS_PASSWORD", "")

    JWT_SECRET: str = os.getenv("JWT_SECRET", "")
    JWT_REFRESH_SECRET: str = os.getenv("JWT_REFRESH_SECRET", "")
    JWT_ALGORITHM: str = os.getenv("JWT_ALGORITHM", "RS256")
    JWT_PRIVATE_KEY: str = os.getenv("JWT_PRIVATE_KEY", "")
    JWT_PUBLIC_KEY: str = os.getenv("JWT_PUBLIC_KEY", "")
    JWT_PREVIOUS_PUBLIC_KEYS: str = os.getenv("JWT_PREVIOUS_PUBLIC_KEYS", "")
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 15
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7
    # Allow legacy HS256 fallback for JWT verification. Disable in production by default.
    ALLOW_HS256_LEGACY_FALLBACK: bool = os.getenv("ALLOW_HS256_LEGACY_FALLBACK", "False").lower() in ("1", "true", "yes")

    # Optional: explicit Redis URL; falls back to host/port values above
    REDIS_URL: str = os.getenv("REDIS_URL", f"redis://{os.getenv('REDIS_HOST','localhost')}:{os.getenv('REDIS_PORT','6379')}")

    AVIATION_EDGE_API_KEY: str = os.getenv("AVIATION_EDGE_API_KEY", "")
    AVIATION_EDGE_BASE_URL: str = os.getenv("AVIATION_EDGE_BASE_URL", "https://aviation-edge.com/v2/public")

    # Meta WhatsApp Cloud API
    WHATSAPP_ACCESS_TOKEN: str = os.getenv("WHATSAPP_ACCESS_TOKEN", "")
    WHATSAPP_PHONE_NUMBER_ID: str = os.getenv("WHATSAPP_PHONE_NUMBER_ID", "")
    WHATSAPP_BUSINESS_ACCOUNT_ID: str = os.getenv("WHATSAPP_BUSINESS_ACCOUNT_ID", "")
    WHATSAPP_WEBHOOK_VERIFY_TOKEN: str = os.getenv("WHATSAPP_WEBHOOK_VERIFY_TOKEN", "")
    WHATSAPP_API_VERSION: str = os.getenv("WHATSAPP_API_VERSION", "v21.0")

    RESEND_API_KEY: str = os.getenv("RESEND_API_KEY", "")
    EMAIL_FROM: str = os.getenv("EMAIL_FROM", os.getenv("RESEND_FROM_EMAIL", ""))
    RESEND_FROM_EMAIL: str = os.getenv("RESEND_FROM_EMAIL", os.getenv("EMAIL_FROM", ""))
    EMAIL_REPLY_TO: str = os.getenv("EMAIL_REPLY_TO", "")
    ADMIN_NOTIFICATION_EMAILS: str = os.getenv("ADMIN_NOTIFICATION_EMAILS", "")

    ALLOWED_ORIGINS_STR: str = os.getenv(
        "ALLOWED_ORIGINS",
        "http://localhost:5173,http://127.0.0.1:5173,http://localhost:5174,http://127.0.0.1:5174,http://localhost:3000,http://127.0.0.1:3000",
    )
    CORS_ALLOW_CREDENTIALS: bool = True
    TRUST_PROXY: bool = os.getenv("TRUST_PROXY", "false").lower() in ("1", "true", "yes")

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

    class Config:
        env_file = ".env"
        case_sensitive = True
        extra = "ignore"

settings = Settings()
