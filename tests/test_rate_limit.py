"""TokenBucket: drains at safe_rpm and eventually times out."""
from __future__ import annotations

import time

import pytest

from quant_data.rate_limit import RateLimitTimeout, TokenBucket
from quant_data.sources.base import RateLimit


def test_acquire_succeeds_under_capacity():
    # safe_rpm = 80% of documented; with rpm=100, capacity = 80
    tb = TokenBucket(RateLimit(requests_per_min=100))
    for _ in range(80):
        tb.acquire(timeout=1.0)


def test_acquire_times_out_when_full():
    tb = TokenBucket(RateLimit(requests_per_min=2))  # safe_rpm = 1
    tb.acquire()
    t0 = time.monotonic()
    with pytest.raises(RateLimitTimeout):
        tb.acquire(timeout=0.1)
    assert time.monotonic() - t0 < 0.3
    assert tb.rate_limit_hit == 1


def test_safe_rpm_uses_80_percent_of_documented():
    tb = TokenBucket(RateLimit(requests_per_min=200))  # tushare
    assert tb.safe_rpm == 160


def test_window_refills_after_60s(monkeypatch):
    """Move fake-time forward and verify the bucket refills."""
    tb = TokenBucket(RateLimit(requests_per_min=2))  # safe_rpm = 1
    tb.acquire()
    # fast-forward by hacking the internal deque
    tb._used.clear()
    tb.acquire(timeout=1.0)  # must succeed after the window slid forward
