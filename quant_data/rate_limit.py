"""Token-bucket rate limiter (v0.4 §4.3).

We drive each source at 80% of its documented ceiling — leaves 20% headroom
for sudden bursts. Sleeps cooperatively, no busy-wait.
"""
from __future__ import annotations

import logging
import threading
import time
from collections import deque

from quant_data.sources.base import RateLimit

log = logging.getLogger("quant_data.rate_limit")


class TokenBucket:
    """Thread-safe per-minute token bucket with hit counter for observability."""

    def __init__(self, limit: RateLimit):
        self._limit = limit
        self._capacity = max(1, limit.safe_rpm)
        # 60s sliding window of timestamps when we actually used a token.
        self._used: deque[float] = deque()
        self._lock = threading.Lock()
        self.rate_limit_hit = 0  # incremented when the bucket throttles us

    @property
    def safe_rpm(self) -> int:
        return self._capacity

    def acquire(self, *, timeout: float = 90.0) -> None:
        """Block until a token is available, or raise ``RateLimitTimeout``.

        Default timeout 90s is enough to ride out a freshly-drained 60s window
        left over from a previous sync in the same process (e.g. when the
        ``sync_full`` driver moves on to the next topic).
        """
        deadline = time.monotonic() + timeout
        while True:
            with self._lock:
                now = time.monotonic()
                # drop timestamps older than 60s
                while self._used and self._used[0] < now - 60.0:
                    self._used.popleft()
                if len(self._used) < self._capacity:
                    self._used.append(now)
                    return
                # else: how long until oldest exits the window?
                wait = 60.0 - (now - self._used[0])
            if time.monotonic() + wait > deadline:
                self.rate_limit_hit += 1
                raise RateLimitTimeout(self._limit, wait)
            log.debug("rate-limit: %s sleeping %.2fs", self._limit.notes, wait)
            time.sleep(max(0.01, wait))


class RateLimitTimeout(RuntimeError):
    pass
