import os
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    PROJECT_NAME: str = "Shafsky Aviation FastAPI Backend Engine"
    ENVIRONMENT: str = os.getenv("ENVIRONMENT", "production")
    DATABASE_URL: str = os.getenv(
        "DATABASE_URL",
        "postgresql://shafsky_admin:ShafskySecretPass2026!@localhost:5432/shafsky_prod"
    )
    REDIS_HOST: str = os.getenv("REDIS_HOST", "localhost")
    REDIS_PORT: int = int(os.getenv("REDIS_PORT", "6379"))
    REDIS_PASSWORD: str = os.getenv("REDIS_PASSWORD", "ShafskyRedisPass2026!")

    JWT_SECRET: str = os.getenv("JWT_SECRET", "shafsky-enterprise-jwt-secret-key-2026")
    JWT_REFRESH_SECRET: str = os.getenv("JWT_REFRESH_SECRET", "shafsky-enterprise-refresh-secret-key-2026")
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 15
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    AMADEUS_CLIENT_ID: str = os.getenv("AMADEUS_CLIENT_ID", "")
    AMADEUS_CLIENT_SECRET: str = os.getenv("AMADEUS_CLIENT_SECRET", "")
    AERODATABOX_API_KEY: str = os.getenv("AERODATABOX_API_KEY", "60574a0b26msh0ed79f408fc4760p1a9e23jsnb2eccb3f41d4")
    AVIATIONSTACK_API_KEY: str = os.getenv("AVIATIONSTACK_API_KEY", "")

    class Config:
        case_sensitive = True

settings = Settings()
