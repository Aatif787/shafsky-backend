"""
Unit and Integration Test Suite for Milestone A4: Redis Distributed Locking
and Idempotency Middleware.
"""

import sys
import os
import uuid
import time
import pytest
from datetime import datetime, timezone, timedelta
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.main import app
from app.services.idempotency_service import IdempotencyService
from app.core.redis_lock import InMemoryLockStore

client = TestClient(app)


def test_01_duplicate_request_returns_cached_response():
    """Verify that a duplicate POST request with X-Idempotency-Key returns cached HTTP 201 response with X-Cache: HIT."""
    key = f"idemp_key_{uuid.uuid4().hex[:8]}"
    dep_time = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()
    arr_time = (datetime.now(timezone.utc) + timedelta(days=2, hours=3)).isoformat()

    payload = {
        "passenger_name": "Idempotent Passenger",
        "passenger_email": "idemp@shafskyaviation.com",
        "passenger_phone": "+1234567890",
        "flight_num": "SF707",
        "origin_code": "DEL",
        "dest_code": "DXB",
        "departure_time": dep_time,
        "arrival_time": arr_time,
        "service_type": "VIP_MEET",
        "total_amount": 5000.0,
        "currency": "INR"
    }

    # First Request (Cache Miss)
    res1 = client.post("/api/bookings", json=payload, headers={"X-Idempotency-Key": key})
    assert res1.status_code == 201, res1.text
    data1 = res1.json()["data"]
    assert res1.headers.get("x-cache") == "MISS"

    # Second Request with same X-Idempotency-Key (Cache Hit)
    res2 = client.post("/api/bookings", json=payload, headers={"X-Idempotency-Key": key})
    assert res2.status_code == 201, res2.text
    data2 = res2.json()["data"]
    assert res2.headers.get("x-cache") == "HIT"
    assert data1["id"] == data2["id"]
    assert data1["bookingRef"] == data2["bookingRef"]


def test_02_concurrent_inflight_request_returns_http_409():
    """Verify that sending a request while lock is held returns HTTP 409 Conflict."""
    key = f"in_flight_key_{uuid.uuid4().hex[:8]}"
    dep_time = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()
    arr_time = (datetime.now(timezone.utc) + timedelta(days=2, hours=3)).isoformat()

    payload = {
        "passenger_name": "Lock Passenger",
        "passenger_email": "lock@shafskyaviation.com",
        "passenger_phone": "+1234567890",
        "flight_num": "SF808",
        "origin_code": "BOM",
        "dest_code": "DEL",
        "departure_time": dep_time,
        "arrival_time": arr_time,
        "service_type": "CONCIERGE",
        "total_amount": 3500.0,
        "currency": "INR"
    }

    # Manually acquire lock using IdempotencyService
    token = IdempotencyService.acquire_lock(key, ttl_seconds=30)
    assert token is not None

    try:
        # Submit request while lock is held
        res = client.post("/api/bookings", json=payload, headers={"X-Idempotency-Key": key})
        assert res.status_code == 409, res.text
        assert "currently being processed" in res.json()["error"].lower()
    finally:
        # Release lock
        IdempotencyService.release_lock(key, token)

    # Retry after lock release
    res_retry = client.post("/api/bookings", json=payload, headers={"X-Idempotency-Key": key})
    assert res_retry.status_code == 201


def test_03_redis_fallback_strategy():
    """Verify that in-memory store fallback works seamlessly when Redis is bypassed."""
    key = f"fallback_key_{uuid.uuid4().hex[:8]}"

    # Test lock in memory store directly
    acq1 = InMemoryLockStore.acquire(f"lock:idempotency:{key}", "token1", 10)
    assert acq1 is True

    acq2 = InMemoryLockStore.acquire(f"lock:idempotency:{key}", "token2", 10)
    assert acq2 is False

    rel = InMemoryLockStore.release(f"lock:idempotency:{key}", "token1")
    assert rel is True


def test_04_lock_timeout_and_release():
    """Verify that lock expires after TTL."""
    key = f"ttl_key_{uuid.uuid4().hex[:8]}"

    # Acquire lock with short TTL (1 second)
    acq1 = InMemoryLockStore.acquire(f"lock:idempotency:{key}", "token1", 1)
    assert acq1 is True

    # Immediate second acquire should fail
    acq2 = InMemoryLockStore.acquire(f"lock:idempotency:{key}", "token2", 1)
    assert acq2 is False

    # Wait for TTL expiration
    time.sleep(1.1)

    # Acquire after TTL should succeed
    acq3 = InMemoryLockStore.acquire(f"lock:idempotency:{key}", "token3", 1)
    assert acq3 is True
    InMemoryLockStore.release(f"lock:idempotency:{key}", "token3")


if __name__ == "__main__":
    test_01_duplicate_request_returns_cached_response()
    test_02_concurrent_inflight_request_returns_http_409()
    test_03_redis_fallback_strategy()
    test_04_lock_timeout_and_release()
    print("ALL MILESTONE A4 IDEMPOTENCY & REDIS TESTS PASSED 100%!")
