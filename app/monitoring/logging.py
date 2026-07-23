import json
import logging
import socket
from datetime import datetime, timezone
from typing import Dict, Any
from app.config import settings
from app.monitoring.tracing import TracingEngine

SENSITIVE_KEYS = {"password", "password_hash", "accesstoken", "refreshtoken", "authorization", "x-rapidapi-key", "secret", "cvv"}

def sanitize_data(data: Any) -> Any:
    if isinstance(data, dict):
        cleaned = {}
        for k, v in data.items():
            if k.lower() in SENSITIVE_KEYS:
                cleaned[k] = "[REDACTED]"
            else:
                cleaned[k] = sanitize_data(v)
        return cleaned
    elif isinstance(data, list):
        return [sanitize_data(item) for item in data]
    return data

class JSONFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        log_entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "request_id": getattr(record, "request_id", TracingEngine.get_request_id()),
            "correlation_id": getattr(record, "correlation_id", TracingEngine.get_correlation_id()),
            "trace_id": getattr(record, "trace_id", TracingEngine.generate_id("trace")),
            "service_name": settings.PROJECT_NAME,
            "environment": settings.ENVIRONMENT,
            "hostname": socket.gethostname(),
            "user_id": getattr(record, "user_id", None),
            "booking_id": getattr(record, "booking_id", None),
            "endpoint": getattr(record, "endpoint", None),
            "http_method": getattr(record, "http_method", None),
            "status_code": getattr(record, "status_code", None),
            "response_time_ms": getattr(record, "response_time_ms", None),
            "client_ip": getattr(record, "client_ip", None),
            "log_level": record.levelname,
            "message": record.getMessage()
        }
        if record.exc_info:
            log_entry["exception"] = self.formatException(record.exc_info)

        return json.dumps(sanitize_data(log_entry))

def setup_structured_logger():
    logger = logging.getLogger("shafsky")
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(JSONFormatter())
        logger.addHandler(handler)
    return logger

structured_logger = setup_structured_logger()
