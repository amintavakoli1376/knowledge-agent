"""CircuitBreaker (Epic 2 Task C2).

Protects the Odoo client from storming an unhealthy service. States:
CLOSED (normal) -> OPEN (after `fail_threshold` consecutive failures, calls
fail fast for `cooldown` seconds) -> HALF_OPEN (one trial request) ->
CLOSED on success / OPEN on failure.
"""
from __future__ import annotations

import time


class CircuitOpenError(Exception):
    """Raised when the circuit is open and a call is attempted."""


class CircuitBreaker:
    def __init__(self, fail_threshold: int = 5, cooldown: float = 30.0) -> None:
        self.fail_threshold = int(fail_threshold)
        self.cooldown = float(cooldown)
        self._failures = 0
        self._opened_at = 0.0
        self._state = "CLOSED"

    @property
    def state(self) -> str:
        self._maybe_half_open()
        return self._state

    def _maybe_half_open(self) -> None:
        if self._state == "OPEN" and time.monotonic() - self._opened_at >= self.cooldown:
            self._state = "HALF_OPEN"
            self._failures = 0

    def allow(self, strict: bool = False) -> bool:
        """Return True if a call may proceed."""
        self._maybe_half_open()
        if self._state == "OPEN":
            if strict:
                raise CircuitOpenError("circuit is open")
            return False
        return True

    def record_success(self) -> None:
        self._state = "CLOSED"
        self._failures = 0

    def record_failure(self) -> None:
        if self._state == "HALF_OPEN":
            self._state = "OPEN"
            self._opened_at = time.monotonic()
            return
        self._failures += 1
        if self._failures >= self.fail_threshold:
            self._state = "OPEN"
            self._opened_at = time.monotonic()
