import time
import threading
from typing import Dict

class PrometheusMetricsCollector:
    _lock = threading.Lock()
    _http_requests_total: Dict[str, int] = {}
    _http_request_duration_sum: Dict[str, float] = {}
    _http_request_duration_count: Dict[str, int] = {}
    _bookings_created_total = 0
    _revenue_generated_total = 0.0
    _notifications_dispatched_total = 0
    _flight_api_calls_total = 0

    @classmethod
    def record_request(cls, method: str, endpoint: str, status_code: int, duration_seconds: float):
        key = f'{method}:{endpoint}:{status_code}'
        with cls._lock:
            cls._http_requests_total[key] = cls._http_requests_total.get(key, 0) + 1
            cls._http_request_duration_sum[endpoint] = cls._http_request_duration_sum.get(endpoint, 0.0) + duration_seconds
            cls._http_request_duration_count[endpoint] = cls._http_request_duration_count.get(endpoint, 0) + 1

    @classmethod
    def record_booking_created(cls, amount: float):
        with cls._lock:
            cls._bookings_created_total += 1
            cls._revenue_generated_total += amount

    @classmethod
    def record_notification_sent(cls):
        with cls._lock:
            cls._notifications_dispatched_total += 1

    @classmethod
    def record_flight_api_call(cls):
        with cls._lock:
            cls._flight_api_calls_total += 1

    _flight_api_requests_total = 0
    _flight_api_failures_total = 0
    _flight_cache_hits_total = 0
    _flight_cache_misses_total = 0
    _flight_api_latency_sum = 0.0

    @classmethod
    def record_flight_api_request(cls):
        with cls._lock:
            cls._flight_api_requests_total += 1

    @classmethod
    def record_flight_api_failure(cls):
        with cls._lock:
            cls._flight_api_failures_total += 1

    @classmethod
    def record_flight_cache_hit(cls):
        with cls._lock:
            cls._flight_cache_hits_total += 1

    @classmethod
    def record_flight_cache_miss(cls):
        with cls._lock:
            cls._flight_cache_misses_total += 1

    @classmethod
    def record_flight_api_latency(cls, latency_seconds: float):
        with cls._lock:
            cls._flight_api_latency_sum += latency_seconds

    @classmethod
    def generate_metrics_text(cls) -> str:
        lines = [
            "# HELP http_requests_total Total HTTP requests processed",
            "# TYPE http_requests_total counter"
        ]
        with cls._lock:
            for key, count in cls._http_requests_total.items():
                parts = key.split(":")
                lines.append(f'http_requests_total{{method="{parts[0]}",endpoint="{parts[1]}",status="{parts[2]}"}} {count}')

            lines.append("# HELP http_request_duration_seconds_sum Total request latency sum")
            lines.append("# TYPE http_request_duration_seconds_sum counter")
            for ep, d_sum in cls._http_request_duration_sum.items():
                lines.append(f'http_request_duration_seconds_sum{{endpoint="{ep}"}} {round(d_sum, 4)}')

            lines.append("# HELP bookings_created_total Total bookings created")
            lines.append("# TYPE bookings_created_total counter")
            lines.append(f'bookings_created_total {cls._bookings_created_total}')

            lines.append("# HELP revenue_generated_total Cumulative revenue generated in INR")
            lines.append("# TYPE revenue_generated_total counter")
            lines.append(f'revenue_generated_total {round(cls._revenue_generated_total, 2)}')

            lines.append("# HELP notifications_dispatched_total Total notifications dispatched")
            lines.append("# TYPE notifications_dispatched_total counter")
            lines.append(f'notifications_dispatched_total {cls._notifications_dispatched_total}')

            lines.append("# HELP flight_api_calls_total Total external flight API calls")
            lines.append("# TYPE flight_api_calls_total counter")
            lines.append(f'flight_api_calls_total {cls._flight_api_calls_total}')

            lines.append("# HELP flight_api_requests_total Total AeroDataBox requests")
            lines.append("# TYPE flight_api_requests_total counter")
            lines.append(f'flight_api_requests_total {cls._flight_api_requests_total}')

            lines.append("# HELP flight_api_failures_total Total AeroDataBox failures")
            lines.append("# TYPE flight_api_failures_total counter")
            lines.append(f'flight_api_failures_total {cls._flight_api_failures_total}')

            lines.append("# HELP flight_cache_hits_total Total flight cache hits")
            lines.append("# TYPE flight_cache_hits_total counter")
            lines.append(f'flight_cache_hits_total {cls._flight_cache_hits_total}')

            lines.append("# HELP flight_cache_misses_total Total flight cache misses")
            lines.append("# TYPE flight_cache_misses_total counter")
            lines.append(f'flight_cache_misses_total {cls._flight_cache_misses_total}')

        return "\n".join(lines) + "\n"
