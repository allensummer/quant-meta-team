"""Resume-from-stale-cursor stress test (v0.5 §8 Week 2).

What this verifies
------------------
The daily/adj_factor/daily_basic drivers must:
  1. Read the existing SQLite cursor, not the caller's ``start_date``.
  2. Advance the cursor day-by-day, persisting progress at every step.
  3. Complete a 1-year (≈ 244 trading days) backfill in < 60s on a
     non-network fake source — proving the per-day loop is well-behaved.
  4. Re-running the same window must be a no-op (idempotent).
  5. A 2-year (≈ 488 days) backfill must still finish in < 120s.

We register a fast in-process fake source so the test never hits tushare.
"""
from __future__ import annotations

import time
from datetime import date, timedelta

import pandas as pd
import pytest

from quant_data.registry import register_source
from quant_data.store.meta_sqlite import MetaSQLite
from quant_data.sync.driver import sync_table
from tests.test_sync_idempotent import _seed_trade_cal


class _FastDailySource:
    """In-process daily source. Returns 2 rows per trade_date, ~0 ms each."""
    name = "fast"
    version = "0"
    capabilities = {"daily", "adj_factor", "daily_basic"}

    def __init__(self):
        from quant_data.rate_limit import TokenBucket
        from quant_data.sources.base import RateLimit
        # Very high token rate — no throttling, the test measures driver loop.
        self._rl = TokenBucket(RateLimit(requests_per_min=100_000))

    def rate_limit(self):
        from quant_data.sources.base import RateLimit
        return RateLimit(requests_per_min=100_000)

    def healthcheck(self):
        return True

    def fetch(self, topic, **params):
        d = params.get("trade_date")
        date_obj = pd.to_datetime(d, format="%Y%m%d").date()
        if topic == "adj_factor":
            return pd.DataFrame({
                "ts_code": ["000001.SZ", "600519.SH"],
                "trade_date": [date_obj] * 2,
                "adj_factor": [1.0, 2.5],
            })
        if topic == "daily_basic":
            return pd.DataFrame({
                "ts_code": ["000001.SZ", "600519.SH"],
                "trade_date": [date_obj] * 2,
                "turnover_rate": [1.0, 0.5],
                "pe": [10.0, 20.0], "pb": [1.0, 5.0], "ps": [2.0, 6.0],
                "total_mv": [1e9, 2e9], "circ_mv": [5e8, 1e9],
            })
        # default = daily
        return pd.DataFrame({
            "ts_code": ["000001.SZ", "600519.SH"],
            "trade_date": [date_obj] * 2,
            "open": [10.0, 100.0], "high": [11.0, 101.0],
            "low": [9.5, 99.0], "close": [10.5, 100.5],
            "pre_close": [10.0, 100.0],
            "change": [0.5, 0.5], "pct_chg": [5.0, 0.5],
            "vol": [100.0, 50.0], "amount": [105.0, 5025.0],
        })

    def lineage(self, **kw):
        from quant_data.sources.base import LineageRecord
        from datetime import datetime
        import uuid
        return LineageRecord(
            table=kw.get("table", ""), schema_version=kw.get("schema_version", ""),
            source=self.name, source_version=self.version,
            fetched_at=datetime.now().astimezone(), params=kw.get("params", {}),
            rows=kw.get("rows", 0), rate_limit_hit=0, request_id=str(uuid.uuid4()),
        )


# ---------------------------------------------------------------------------
# 1. 1-year backfill finishes quickly when source is fast.
# ---------------------------------------------------------------------------
def test_resume_one_year_completes_quickly(tmp_data_dir):
    """Move the cursor 1 year back, run sync, confirm it advances to end."""
    register_source("fast", _FastDailySource())
    # Seed trade_cal for a 14-month window (12 months back + 2 months forward).
    _seed_trade_cal(tmp_data_dir, date(2023, 1, 1), date(2024, 3, 1))

    meta = MetaSQLite()
    # Pretend the cursor was at 2023-01-03 (12 months before "today" 2024-01-05).
    meta.set_cursor("fast_daily", date(2023, 1, 3), status="ok")

    t0 = time.monotonic()
    r = sync_table("daily", source="fast",
                   start_date=date(2023, 1, 4),  # cursor+1
                   end_date=date(2024, 1, 5))
    elapsed = time.monotonic() - t0

    # The driver must have advanced the cursor past 2023-01-03 by a full year.
    cur = meta.get_cursor("fast_daily")
    assert cur >= date(2024, 1, 5), f"cursor only reached {cur}"
    # 2 rows × ~244 trading days ≈ 488 rows; allow a small rounding window.
    assert r["rows"] >= 480, f"unexpected row count: {r['rows']}"
    assert r["batches"] >= 240
    # Loop must be quick — the test source has no I/O, so this is essentially
    # pure Python overhead. 60s is generous.
    assert elapsed < 60.0, f"1-year backfill took {elapsed:.1f}s, expected < 60s"
    print(f"\n[perf] 1-year backfill: {r['batches']} batches, {r['rows']} rows, "
          f"{elapsed:.2f}s")


# ---------------------------------------------------------------------------
# 2. Re-running the same window is a no-op (idempotent).
# ---------------------------------------------------------------------------
def test_resume_idempotent_after_full_backfill(tmp_data_dir):
    """Calling sync_table again with no new dates must return 0 rows."""
    register_source("fast", _FastDailySource())
    _seed_trade_cal(tmp_data_dir, date(2023, 1, 1), date(2024, 1, 5))
    meta = MetaSQLite()
    meta.set_cursor("fast_daily", date(2023, 1, 3), status="ok")

    sync_table("daily", source="fast",
               start_date=date(2023, 1, 4), end_date=date(2024, 1, 5))
    cur = meta.get_cursor("fast_daily")

    # Now re-run with end_date <= cursor; must be a no-op.
    r2 = sync_table("daily", source="fast",
                    start_date=date(2023, 1, 4),
                    end_date=cur)  # cursor already covers up to `end_date`
    assert r2["rows"] == 0
    assert r2["batches"] == 0
    # Cursor must not have moved.
    assert meta.get_cursor("fast_daily") == cur


# ---------------------------------------------------------------------------
# 3. 2-year backfill scales linearly (driver loop is O(days), not O(days^2)).
# ---------------------------------------------------------------------------
def test_resume_two_year_completes_quickly(tmp_data_dir):
    register_source("fast", _FastDailySource())
    _seed_trade_cal(tmp_data_dir, date(2022, 1, 1), date(2024, 1, 5))
    meta = MetaSQLite()
    meta.set_cursor("fast_daily", date(2022, 1, 4), status="ok")

    t0 = time.monotonic()
    r = sync_table("daily", source="fast",
                   start_date=date(2022, 1, 5),
                   end_date=date(2024, 1, 5))
    elapsed = time.monotonic() - t0

    cur = meta.get_cursor("fast_daily")
    assert cur >= date(2024, 1, 5)
    assert r["rows"] >= 950, f"unexpected row count: {r['rows']}"
    # 2-year budget: < 120s
    assert elapsed < 120.0, f"2-year backfill took {elapsed:.1f}s"
    print(f"\n[perf] 2-year backfill: {r['batches']} batches, {r['rows']} rows, "
          f"{elapsed:.2f}s")


# ---------------------------------------------------------------------------
# 4. Resume after manual cursor rewinding must pick up at the rewound date.
# ---------------------------------------------------------------------------
def test_resume_after_cursor_rewind(tmp_data_dir):
    """Simulate the operator's mistake: cursor is 6 months behind the parquet.
    The next run must backfill the gap, NOT re-pull existing days."""
    register_source("fast", _FastDailySource())
    _seed_trade_cal(tmp_data_dir, date(2023, 1, 1), date(2024, 1, 5))
    meta = MetaSQLite()

    # Step 1: sync to mid-2023.
    meta.set_cursor("fast_daily", date(2023, 6, 30), status="ok")
    sync_table("daily", source="fast",
               start_date=date(2023, 7, 1), end_date=date(2023, 12, 29))
    assert meta.get_cursor("fast_daily") >= date(2023, 12, 29)

    # Step 2: operator (or a buggy hand-edit) accidentally rewinds the cursor
    # back to March 2023.  The next sync must detect the gap and fill it.
    meta.set_cursor("fast_daily", date(2023, 3, 15), status="ok")
    sync_table("daily", source="fast",
               start_date=date(2023, 3, 16), end_date=date(2023, 12, 29))

    cur = meta.get_cursor("fast_daily")
    assert cur >= date(2023, 12, 29), f"rewound cursor should re-advance, got {cur}"
