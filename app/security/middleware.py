from starlette.middleware.base import BaseHTTPMiddleware
from fastapi import Request
from app.security.headers import get_security_headers
from app.security.rate_limit import RateLimiter

class SecurityMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # 1. Apply Rate Limiting
        client_ip = request.client.host if request.client else "127.0.0.1"
        endpoint_path = request.url.path

        if endpoint_path.startswith("/api/auth/login") or endpoint_path.startswith("/api/auth/register"):
            RateLimiter.check_rate_limit(f"rate_limit_auth:{client_ip}", max_requests=10, window_seconds=60)
        elif endpoint_path.startswith("/api/"):
            RateLimiter.check_rate_limit(f"rate_limit_api:{client_ip}", max_requests=200, window_seconds=60)

        # 2. Process Request
        response = await call_next(request)

        # 3. Inject OWASP Security Headers
        for key, val in get_security_headers().items():
            response.headers[key] = val

        return response
