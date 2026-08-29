"""Unit tests for idempotency fingerprinting and middleware (no database required)."""

import uuid
from unittest.mock import patch

import pytest
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from app.middleware.idempotency import IdempotencyMiddleware
from app.services.idempotency_service import IdempotencyService, InMemoryResponseStore
from app.core.redis_lock import InMemoryLockStore


async def echo(request):
    body = await request.body()
    return JSONResponse({"echo": body.decode("utf-8")}, status_code=200)


def _client():
    app = Starlette(routes=[Route("/echo", echo, methods=["POST"])])
    app.add_middleware(IdempotencyMiddleware)
    return TestClient(app)


@pytest.fixture(autouse=True)
def _clear_stores():
    IdempotencyService.clear_stores()
    InMemoryLockStore.clear()
    InMemoryResponseStore.clear()
    yield
    IdempotencyService.clear_stores()
    InMemoryLockStore.clear()
    InMemoryResponseStore.clear()


def test_fingerprint_is_stable_and_body_sensitive():
    a = IdempotencyService.request_fingerprint("POST", "/api/bookings", b'{"a":1}')
    b = IdempotencyService.request_fingerprint("POST", "/api/bookings", b'{"a":1}')
    c = IdempotencyService.request_fingerprint("POST", "/api/bookings", b'{"a":2}')
    d = IdempotencyService.request_fingerprint("POST", "/api/other", b'{"a":1}')
    assert a == b
    assert a != c
    assert a != d


def test_fingerprints_conflict_ignores_legacy_cache_without_fingerprint():
    cached = {"status_code": 200, "headers": {}, "body": "{}", "fingerprint": None}
    assert IdempotencyService.fingerprints_conflict(cached, "abc") is False
    cached["fingerprint"] = "abc"
    assert IdempotencyService.fingerprints_conflict(cached, "abc") is False
    assert IdempotencyService.fingerprints_conflict(cached, "zzz") is True


def test_duplicate_post_replays_cached_response():
    client = _client()
    key = f"k_{uuid.uuid4().hex[:8]}"
    res1 = client.post("/echo", content=b"hello", headers={"X-Idempotency-Key": key})
    assert res1.status_code == 200
    assert res1.headers.get("x-cache") == "MISS"
    res2 = client.post("/echo", content=b"hello", headers={"X-Idempotency-Key": key})
    assert res2.status_code == 200
    assert res2.headers.get("x-cache") == "HIT"
    assert res2.json() == {"echo": "hello"}


def test_same_key_different_body_returns_422():
    client = _client()
    key = f"k_{uuid.uuid4().hex[:8]}"
    res1 = client.post("/echo", content=b"one", headers={"X-Idempotency-Key": key})
    assert res1.status_code == 200
    res2 = client.post("/echo", content=b"two", headers={"X-Idempotency-Key": key})
    assert res2.status_code == 422
    assert res2.json()["code"] == "ERR_IDEMPOTENCY_KEY_REUSE"


def test_in_flight_lock_returns_409():
    client = _client()
    key = f"k_{uuid.uuid4().hex[:8]}"
    token = IdempotencyService.acquire_lock(key, ttl_seconds=30)
    assert token is not None
    try:
        res = client.post("/echo", content=b"hello", headers={"X-Idempotency-Key": key})
        assert res.status_code == 409
        assert res.json()["code"] == "ERR_CONCURRENT_SUBMISSION"
    finally:
        IdempotencyService.release_lock(key, token)


def test_oversized_key_returns_400():
    client = _client()
    res = client.post("/echo", content=b"hello", headers={"X-Idempotency-Key": "x" * 300})
    assert res.status_code == 400
    assert res.json()["code"] == "ERR_IDEMPOTENCY_KEY_INVALID"


def test_without_redis_still_serves_from_memory_fallback():
    client = _client()
    key = f"k_{uuid.uuid4().hex[:8]}"
    with patch("app.services.idempotency_service.get_redis_client", return_value=None), patch(
        "app.core.redis_lock.get_redis_client", return_value=None
    ), patch(
        "app.services.idempotency_service._PostgresIdempotencyStore._is_postgres",
        return_value=False,
    ):
        res1 = client.post("/echo", content=b"hello", headers={"X-Idempotency-Key": key})
        assert res1.status_code == 200
        res2 = client.post("/echo", content=b"hello", headers={"X-Idempotency-Key": key})
        assert res2.status_code == 200
        assert res2.headers.get("x-cache") == "HIT"


def test_post_lock_cache_hit_does_not_reexecute():
    client = _client()
    key = f"k_{uuid.uuid4().hex[:8]}"
    fingerprint = IdempotencyService.request_fingerprint("POST", "/echo", b"hello")
    IdempotencyService.set_cached_response(
        key,
        status_code=200,
        headers={"content-type": "application/json"},
        body='{"echo":"hello"}',
        ttl_seconds=60,
        fingerprint=fingerprint,
    )
    res = client.post("/echo", content=b"hello", headers={"X-Idempotency-Key": key})
    assert res.status_code == 200
    assert res.headers.get("x-cache") == "HIT"
