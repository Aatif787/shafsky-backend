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

    JWT_SECRET: str = os.getenv("JWT_SECRET", "shafsky-dev-secret-key-change-in-prod")
    JWT_REFRESH_SECRET: str = os.getenv("JWT_REFRESH_SECRET", "shafsky-dev-refresh-secret-key-change-in-prod")
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 15
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    AMADEUS_CLIENT_ID: str = os.getenv("AMADEUS_CLIENT_ID", "")
    AMADEUS_CLIENT_SECRET: str = os.getenv("AMADEUS_CLIENT_SECRET", "")
    AERODATABOX_API_KEY: str = os.getenv("AERODATABOX_API_KEY", "")
    RAPIDAPI_KEY: str = os.getenv("RAPIDAPI_KEY", os.getenv("AERODATABOX_API_KEY", ""))
    RAPIDAPI_HOST: str = os.getenv("RAPIDAPI_HOST", "aerodatabox.p.rapidapi.com")
    AERODATABOX_BASE_URL: str = os.getenv("AERODATABOX_BASE_URL", "https://aerodatabox.p.rapidapi.com")
    AVIATIONSTACK_API_KEY: str = os.getenv("AVIATIONSTACK_API_KEY", "")

    class Config:
        env_file = ".env"
        case_sensitive = True
        extra = "ignore"

settings = Settings()
