"""Tests for CircuitBreaker (Epic 2 Task C2)."""
import time

import pytest

from src.storage.circuit_breaker import CircuitBreaker, CircuitOpenError


def test_starts_closed():
    cb = CircuitBreaker(fail_threshold=3, cooldown=30)
    assert cb.state == "CLOSED"
    assert cb.allow() is True


def test_opens_after_threshold_failures():
    cb = CircuitBreaker(fail_threshold=3, cooldown=30)
    cb.record_failure()
    cb.record_failure()
    assert cb.state == "CLOSED"
    assert cb.allow() is True
    cb.record_failure()  # third failure -> OPEN
    assert cb.state == "OPEN"
    assert cb.allow() is False


def test_half_open_after_cooldown_then_closes_on_success():
    cb = CircuitBreaker(fail_threshold=2, cooldown=0.05)
    cb.record_failure()
    cb.record_failure()
    assert cb.state == "OPEN"
    time.sleep(0.07)
    assert cb.state == "HALF_OPEN"
    assert cb.allow() is True  # trial request
    cb.record_success()
    assert cb.state == "CLOSED"


def test_half_open_reopens_on_failure():
    cb = CircuitBreaker(fail_threshold=2, cooldown=0.05)
    cb.record_failure()
    cb.record_failure()
    time.sleep(0.07)
    assert cb.state == "HALF_OPEN"
    cb.record_failure()  # trial fails -> OPEN again
    assert cb.state == "OPEN"


def test_allow_raises_when_open():
    cb = CircuitBreaker(fail_threshold=1, cooldown=30)
    cb.record_failure()
    with pytest.raises(CircuitOpenError):
        cb.allow(strict=True)
