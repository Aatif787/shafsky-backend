def get_security_headers() -> dict:
    return {
        "Strict-Transport-Security": "max-age=31536000; includeSubDomains; preload",
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "SAMEORIGIN",
        "X-XSS-Protection": "1; mode=block",
        "X-Permitted-Cross-Domain-Policies": "none",
        "Cross-Origin-Opener-Policy": "same-origin-allow-popups",
        # cross-origin: API is called from Vercel/Hostinger/admin portal on another origin
        "Cross-Origin-Resource-Policy": "cross-origin",
        "Referrer-Policy": "strict-origin-when-cross-origin",
        "Permissions-Policy": (
            "accelerometer=(), "
            "autoplay=(), "
            "camera=(), "
            "display-capture=(), "
            "encrypted-media=(), "
            "fullscreen=(self), "
            "geolocation=(), "
            "gyroscope=(), "
            "magnetometer=(), "
            "microphone=(), "
            "midi=(), "
            'payment=(self "https://checkout.razorpay.com" "https://api.razorpay.com"), '
            "usb=(), "
            "interest-cohort=(), "
            "screen-wake-lock=()"
        ),
        "Content-Security-Policy": (
            "default-src 'self'; "
            "base-uri 'self'; "
            "object-src 'none'; "
            "frame-ancestors 'self'; "
            "worker-src 'self' blob: data:; "
            "img-src 'self' data: https: blob:; "
            "script-src 'self' 'unsafe-inline' https://checkout.razorpay.com https://api.razorpay.com; "
            "frame-src 'self' https://api.razorpay.com https://checkout.razorpay.com; "
            "connect-src 'self' https://api.razorpay.com https://lumberjack.razorpay.com https://*.razorpay.com https:; "
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
            "font-src 'self' https://fonts.gstatic.com data:; "
            "upgrade-insecure-requests;"
        ),
        "X-Robots-Tag": "noindex, nofollow, noarchive",
        "Server": "Shafsky-Shield",
    }
