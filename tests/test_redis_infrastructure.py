"""
Unit and Integration Test Suite for Milestone A4 - Part 1: Centralized Redis Infrastructure
and Distributed Lock Service.
"""

import sys
import os
import time
import uuid
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.core.redis import check_redis_health, get_redis_client, reset_redis_client
from app.core.redis_lock import RedisDistributedLock, InMemoryLockStore


def test_01_redis_connection_and_health_check():
    """Verify centralized Redis connection and health check diagnostics."""
    health = check_redis_health()
    assert health is not None
    assert "status" in health
    assert "connected" in health
    assert "host" in health
    assert "port" in health
    if health["connected"]:
        assert health["status"] == "healthy"
        assert health["latency_ms"] is not None
        assert health["latency_ms"] >= 0


def test_02_acquire_and_release_lock_with_owner_validation():
    """Verify lock acquisition and owner-validated release."""
    lock_name = f"test_lock_{uuid.uuid4().hex[:8]}"

    # Acquire Lock
    token = RedisDistributedLock.acquire_lock(lock_name, ttl_seconds=10)
    assert token is not None
    assert isinstance(token, str)
    assert RedisDistributedLock.is_locked(lock_name) is True

    # Attempt Release with WRONG token -> Should fail (Owner Validation)
    wrong_token = "wrong_owner_token_123"
    released_wrong = RedisDistributedLock.release_lock(lock_name, wrong_token)
    assert released_wrong is False
    assert RedisDistributedLock.is_locked(lock_name) is True

    # Release with CORRECT token -> Should succeed
    released_correct = RedisDistributedLock.release_lock(lock_name, token)
    assert released_correct is True
    assert RedisDistributedLock.is_locked(lock_name) is False


def test_03_multiple_client_lock_collision():
    """Verify collision handling when multiple clients attempt to acquire the same lock."""
    lock_name = f"collision_lock_{uuid.uuid4().hex[:8]}"

    # Client A acquires lock
    token_a = RedisDistributedLock.acquire_lock(lock_name, ttl_seconds=10)
    assert token_a is not None

    # Client B attempts acquire on same lock -> Should be rejected
    token_b = RedisDistributedLock.acquire_lock(lock_name, ttl_seconds=10)
    assert token_b is None

    # Client A releases lock
    rel_a = RedisDistributedLock.release_lock(lock_name, token_a)
    assert rel_a is True

    # Client B re-attempts acquire -> Should succeed now
    token_b2 = RedisDistributedLock.acquire_lock(lock_name, ttl_seconds=10)
    assert token_b2 is not None

    # Cleanup
    RedisDistributedLock.release_lock(lock_name, token_b2)


def test_04_lock_ttl_timeout_expiration():
    """Verify that lock expires after TTL timeout."""
    lock_name = f"ttl_lock_{uuid.uuid4().hex[:8]}"

    # Acquire lock with 1 second TTL
    token1 = RedisDistributedLock.acquire_lock(lock_name, ttl_seconds=1)
    assert token1 is not None

    # Immediate second acquire should fail
    token2 = RedisDistributedLock.acquire_lock(lock_name, ttl_seconds=1)
    assert token2 is None

    # Sleep past TTL expiration
    time.sleep(1.1)

    # Lock should now be expired and acquireable by new owner
    token3 = RedisDistributedLock.acquire_lock(lock_name, ttl_seconds=5)
    assert token3 is not None
    assert token3 != token1

    # Cleanup
    RedisDistributedLock.release_lock(lock_name, token3)


def test_05_in_memory_fallback_store():
    """Verify in-memory lock store behavior when Redis is bypassed."""
    lock_name = f"mem_lock_{uuid.uuid4().hex[:8]}"
    token = uuid.uuid4().hex

    # Acquire in memory
    acq1 = InMemoryLockStore.acquire(lock_name, token, ttl_seconds=10)
    assert acq1 is True
    assert InMemoryLockStore.is_locked(lock_name) is True

    # Second acquire should fail
    acq2 = InMemoryLockStore.acquire(lock_name, "other_token", ttl_seconds=10)
    assert acq2 is False

    # Release with wrong token fails
    rel_bad = InMemoryLockStore.release(lock_name, "other_token")
    assert rel_bad is False

    # Release with correct token succeeds
    rel_good = InMemoryLockStore.release(lock_name, token)
    assert rel_good is True
    assert InMemoryLockStore.is_locked(lock_name) is False


if __name__ == "__main__":
    test_01_redis_connection_and_health_check()
    test_02_acquire_and_release_lock_with_owner_validation()
    test_03_multiple_client_lock_collision()
    test_04_lock_ttl_timeout_expiration()
    test_05_in_memory_fallback_store()
    print("ALL MILESTONE A4 PART 1 REDIS INFRASTRUCTURE TESTS PASSED 100%!")
