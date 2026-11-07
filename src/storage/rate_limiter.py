"""TokenBucket rate limiter (Epic 2 Task C4).

A simple in-process token bucket. Requests consume tokens; tokens refill
linearly at ``rate`` per second up to ``capacity``. Used to protect the
Odoo JSON-RPC endpoint from bursts.
"""
from __future__ import annotations

import threading
import time


class TokenBucket:
    """Token bucket with linear refill."""

    def __init__(self, rate: float, capacity: int) -> None:
        if rate <= 0 or capacity <= 0:
            raise ValueError("rate and capacity must be > 0")
        self._rate = float(rate)
        self._capacity = int(capacity)
        self._tokens = float(capacity)
        self._last = time.monotonic()
        self._lock = threading.Lock()

    def _refill(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last
        if elapsed > 0:
            self._tokens = min(
                self._capacity, self._tokens + elapsed * self._rate
            )
            self._last = now

    def acquire(self, timeout: float = 0.0) -> bool:
        """Try to take one token; block up to ``timeout`` if needed."""
        deadline = time.monotonic() + timeout if timeout > 0 else None
        with self._lock:
            while True:
                self._refill()
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return True
                if deadline is None:
                    return False
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                time.sleep(min(0.001, remaining))
