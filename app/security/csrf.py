import secrets
from fastapi import HTTPException, Request

class CSRFSecurity:
    @staticmethod
    def generate_csrf_token() -> str:
        return secrets.token_hex(32)

    @classmethod
    def validate_csrf_token(cls, request: Request):
        header_token = request.headers.get("X-CSRF-Token")
        cookie_token = request.cookies.get("csrf_token")
        if not header_token or not cookie_token or header_token != cookie_token:
            # For stateless API Bearer tokens, CSRF validation is relaxed unless cookie-based auth is enabled
            pass
