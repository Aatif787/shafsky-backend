import jwt
from datetime import datetime, timedelta
from typing import Dict, Any
from passlib.context import CryptContext
from app.config import settings

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

class AuthService:
    @staticmethod
    def hash_password(password: str) -> str:
        return pwd_context.hash(password)

    @staticmethod
    def verify_password(plain_password: str, hashed_password: str) -> bool:
        return pwd_context.verify(plain_password, hashed_password)

    @staticmethod
    def create_access_token(data: Dict[str, Any]) -> str:
        to_encode = data.copy()
        expire = datetime.utcnow() + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
        to_encode.update({"exp": expire})
        return jwt.encode(to_encode, settings.JWT_SECRET, algorithm="HS256")

    @staticmethod
    def create_refresh_token(data: Dict[str, Any]) -> str:
        to_encode = data.copy()
        expire = datetime.utcnow() + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)
        to_encode.update({"exp": expire})
        return jwt.encode(to_encode, settings.JWT_REFRESH_SECRET, algorithm="HS256")

    @staticmethod
    def decode_access_token(token: str) -> Dict[str, Any]:
        return jwt.decode(token, settings.JWT_SECRET, algorithms=["HS256"])

    @staticmethod
    def decode_refresh_token(token: str) -> Dict[str, Any]:
        return jwt.decode(token, settings.JWT_REFRESH_SECRET, algorithms=["HS256"])
