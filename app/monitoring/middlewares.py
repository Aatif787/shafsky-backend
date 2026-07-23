import time
import logging
from starlette.middleware.base import BaseHTTPMiddleware
from fastapi import Request
from app.monitoring.tracing import TracingEngine
from app.monitoring.metrics import PrometheusMetricsCollector
from app.monitoring.logging import structured_logger

class ObservabilityMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        start_time = time.time()

        # 1. Propagate / Generate Request & Correlation IDs
        incoming_cid = request.headers.get("X-Correlation-ID") or request.headers.get("X-Request-ID")
        correlation_id = incoming_cid if incoming_cid else TracingEngine.generate_id("corr")
        request_id = TracingEngine.generate_id("req")

        TracingEngine.set_correlation_id(correlation_id)
        TracingEngine.set_request_id(request_id)

        # 2. Process Request
        response = await call_next(request)

        # 3. Calculate Latency & Update Metrics
        duration = time.time() - start_time
        duration_ms = round(duration * 1000, 2)

        client_ip = request.client.host if request.client else "127.0.0.1"
        endpoint = request.url.path
        method = request.method
        status_code = response.status_code

        PrometheusMetricsCollector.record_request(method, endpoint, status_code, duration)

        # 4. Inject Correlation Headers in Response
        response.headers["X-Correlation-ID"] = correlation_id
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Response-Time-Ms"] = str(duration_ms)

        # 5. Emit Structured JSON Log
        extra_log = {
            "request_id": request_id,
            "correlation_id": correlation_id,
            "endpoint": endpoint,
            "http_method": method,
            "status_code": status_code,
            "response_time_ms": duration_ms,
            "client_ip": client_ip
        }
        structured_logger.info(
            f"HTTP {method} {endpoint} -> {status_code} in {duration_ms}ms",
            extra=extra_log
        )

        return response
