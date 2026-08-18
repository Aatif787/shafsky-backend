def get_security_headers() -> dict:
    return {
        "Strict-Transport-Security": "max-age=31536000; includeSubDomains; preload",
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
        "X-XSS-Protection": "1; mode=block",
        "X-Permitted-Cross-Domain-Policies": "none",
        "Cross-Origin-Opener-Policy": "same-origin-allow-popups",
        "Cross-Origin-Resource-Policy": "same-origin",
        "Referrer-Policy": "strict-origin-when-cross-origin",
        "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
        # Prefer nonces or strict hashing for inline scripts/styles in production.
        "Content-Security-Policy": (
            "default-src 'self'; "
            "worker-src 'self' blob: data:; "
            "img-src 'self' data: https: blob:; "
            "script-src 'self'; "
            "style-src 'self';"
        )
    }
