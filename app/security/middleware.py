from starlette.middleware.base import BaseHTTPMiddleware
from fastapi import Request
from app.security.headers import get_security_headers
from app.security.rate_limit import RateLimiter

class SecurityMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # 1. Safely resolve client IP across reverse proxies and load balancers
        cf_ip = request.headers.get("CF-Connecting-IP")
        forwarded = request.headers.get("X-Forwarded-For")
        if cf_ip:
            client_ip = cf_ip.strip()
        elif forwarded:
            client_ip = forwarded.split(",")[0].strip()
        else:
            client_ip = request.client.host if request.client else "127.0.0.1"

        endpoint_path = request.url.path

        # 2. Apply Rate Limiting
        if endpoint_path.startswith("/api/auth/login") or endpoint_path.startswith("/api/auth/register"):
            RateLimiter.check_rate_limit(f"rate_limit_auth:{client_ip}", max_requests=10, window_seconds=60)
        elif endpoint_path.startswith("/api/"):
            RateLimiter.check_rate_limit(f"rate_limit_api:{client_ip}", max_requests=200, window_seconds=60)

        # 3. Process Request
        response = await call_next(request)

        # 4. Inject OWASP Security Headers
        for key, val in get_security_headers().items():
            response.headers[key] = val

        return response
