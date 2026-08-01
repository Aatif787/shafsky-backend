"""
Production Test Suite for Milestone A4 Part 2: Idempotency Middleware.

Coverage:
1. Duplicate request (Cache Hit replay with X-Cache: HIT)
2. Concurrent request (HTTP 409 Conflict)
3. Retry after completion
4. Redis unavailable (graceful fallback)
5. Cache expiration
6. Lock timeout
7. Lock release
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
from app.services.idempotency_service import IdempotencyService, InMemoryResponseStore
from app.core.redis_lock import RedisDistributedLock, InMemoryLockStore
from app.core.redis import get_redis_client

client = TestClient(app)


@pytest.fixture(autouse=True)
def setup_teardown():
    IdempotencyService.clear_stores()
    yield
    IdempotencyService.clear_stores()


def test_01_duplicate_request_returns_cached_response():
    """Verify that a duplicate POST request with X-Idempotency-Key returns cached response with X-Cache: HIT."""
    key = f"idemp_key_{uuid.uuid4().hex[:8]}"
    dep_time = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()
    arr_time = (datetime.now(timezone.utc) + timedelta(days=2, hours=3)).isoformat()

    payload = {
        "passenger_name": "Duplicate Passenger",
        "passenger_email": "dup@shafskyaviation.com",
        "passenger_phone": "+1234567890",
        "flight_num": "SF101",
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
    assert res1.headers.get("x-cache") == "MISS"
    data1 = res1.json()["data"]

    # Second Request with same X-Idempotency-Key (Cache Hit)
    res2 = client.post("/api/bookings", json=payload, headers={"X-Idempotency-Key": key})
    assert res2.status_code == 201, res2.text
    assert res2.headers.get("x-cache") == "HIT"
    data2 = res2.json()["data"]
    assert data1["id"] == data2["id"]
    assert data1["bookingRef"] == data2["bookingRef"]


def test_02_concurrent_request_returns_409_conflict():
    """Verify that a concurrent request while lock is held returns HTTP 409 Conflict."""
    key = f"concurrent_key_{uuid.uuid4().hex[:8]}"
    dep_time = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()
    arr_time = (datetime.now(timezone.utc) + timedelta(days=2, hours=3)).isoformat()

    payload = {
        "passenger_name": "Concurrent Passenger",
        "passenger_email": "conc@shafskyaviation.com",
        "passenger_phone": "+1234567890",
        "flight_num": "SF202",
        "origin_code": "BOM",
        "dest_code": "LHR",
        "departure_time": dep_time,
        "arrival_time": arr_time,
        "service_type": "CONCIERGE",
        "total_amount": 7500.0,
        "currency": "INR"
    }

    # Acquire lock manually to simulate in-flight processing request
    lock_token = IdempotencyService.acquire_lock(key, ttl_seconds=30)
    assert lock_token is not None

    try:
        # Request submitted while lock is held
        res = client.post("/api/bookings", json=payload, headers={"X-Idempotency-Key": key})
        assert res.status_code == 409, res.text
        assert "currently being processed" in res.json()["error"].lower()
    finally:
        # Release lock
        IdempotencyService.release_lock(key, lock_token)


def test_03_retry_after_completion():
    """Verify that a retry after request completion returns the cached response."""
    key = f"retry_key_{uuid.uuid4().hex[:8]}"
    dep_time = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()
    arr_time = (datetime.now(timezone.utc) + timedelta(days=2, hours=3)).isoformat()

    payload = {
        "passenger_name": "Retry Passenger",
        "passenger_email": "retry@shafskyaviation.com",
        "passenger_phone": "+1234567890",
        "flight_num": "SF303",
        "origin_code": "DEL",
        "dest_code": "JFK",
        "departure_time": dep_time,
        "arrival_time": arr_time,
        "service_type": "VIP_MEET",
        "total_amount": 9000.0,
        "currency": "INR"
    }

    # Initial request
    res1 = client.post("/api/bookings", json=payload, headers={"X-Idempotency-Key": key})
    assert res1.status_code == 201

    # Retry after completion
    res2 = client.post("/api/bookings", json=payload, headers={"X-Idempotency-Key": key})
    assert res2.status_code == 201
    assert res2.headers.get("x-cache") == "HIT"


def test_04_redis_unavailable_fallback():
    """Verify that in-memory store fallback works smoothly when Redis is bypassed."""
    key = f"fallback_key_{uuid.uuid4().hex[:8]}"

    # Test lock and response cache in in-memory store directly
    acq1 = InMemoryLockStore.acquire(f"lock:idempotency:{key}", "token1", 10)
    assert acq1 is True

    acq2 = InMemoryLockStore.acquire(f"lock:idempotency:{key}", "token2", 10)
    assert acq2 is False

    rel = InMemoryLockStore.release(f"lock:idempotency:{key}", "token1")
    assert rel is True

    InMemoryResponseStore.set_cached_response(key, {"status_code": 200, "headers": {}, "body": "ok"}, 10)
    cached = InMemoryResponseStore.get_cached_response(key)
    assert cached is not None
    assert cached["body"] == "ok"


def test_05_cache_expiration():
    """Verify that cached response expires after TTL."""
    key = f"exp_key_{uuid.uuid4().hex[:8]}"
    
    # Store response with 1 second TTL
    IdempotencyService.set_cached_response(
        key,
        status_code=200,
        headers={"Content-Type": "application/json"},
        body='{"status":"ok"}',
        ttl_seconds=1
    )

    # Immediate fetch should succeed
    cached = IdempotencyService.get_cached_response(key)
    assert cached is not None

    # Wait for TTL to expire
    time.sleep(1.1)

    # Fetch after TTL should return None
    cached_exp = IdempotencyService.get_cached_response(key)
    assert cached_exp is None


def test_06_lock_timeout():
    """Verify that distributed lock expires after TTL."""
    key = f"lock_ttl_key_{uuid.uuid4().hex[:8]}"

    # Acquire lock with 1 second TTL
    token1 = IdempotencyService.acquire_lock(key, ttl_seconds=1)
    assert token1 is not None

    # Immediate second acquire should fail
    token2 = IdempotencyService.acquire_lock(key, ttl_seconds=1)
    assert token2 is None

    # Wait for lock TTL to expire
    time.sleep(1.1)

    # Acquire after TTL should succeed
    token3 = IdempotencyService.acquire_lock(key, ttl_seconds=1)
    assert token3 is not None
    IdempotencyService.release_lock(key, token3)


def test_07_lock_release():
    """Verify owner-validated lock release mechanism."""
    key = f"rel_key_{uuid.uuid4().hex[:8]}"

    token = IdempotencyService.acquire_lock(key, ttl_seconds=30)
    assert token is not None
    assert IdempotencyService.is_locked(key) is True

    # Releasing with invalid token should fail
    fail_rel = IdempotencyService.release_lock(key, "wrong_token")
    assert fail_rel is False
    assert IdempotencyService.is_locked(key) is True

    # Releasing with valid owner token should succeed
    succ_rel = IdempotencyService.release_lock(key, token)
    assert succ_rel is True
    assert IdempotencyService.is_locked(key) is False


if __name__ == "__main__":
    pytest.main(["-v", __file__])
