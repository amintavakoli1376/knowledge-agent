"""Tests for TokenBucket rate limiter (Epic 2 Task C4)."""
import time

import pytest

from src.storage.rate_limiter import TokenBucket


def test_token_bucket_allows_initial_burst():
    b = TokenBucket(rate=10, capacity=5)
    # capacity tokens available immediately
    for _ in range(5):
        assert b.acquire() is True
    # now empty
    assert b.acquire() is False


def test_token_bucket_refills_over_time():
    b = TokenBucket(rate=100, capacity=1)
    assert b.acquire() is True
    assert b.acquire() is False
    # refill after ~1/rate second
    time.sleep(0.02)
    assert b.acquire() is True


def test_token_bucket_never_exceeds_capacity():
    b = TokenBucket(rate=1000, capacity=3)
    # capacity is capped at 3 even if we wait long
    time.sleep(0.05)
    got = sum(1 for _ in range(10) if b.acquire())
    assert got == 3


def test_token_bucket_blocking_acquire():
    b = TokenBucket(rate=200, capacity=1)
    assert b.acquire() is True
    start = time.time()
    # second acquire should block until refill
    assert b.acquire(timeout=1.0) is True
    assert time.time() - start >= 0.003
