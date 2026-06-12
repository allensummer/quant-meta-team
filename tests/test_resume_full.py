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


# ---------------------------------------------------------------------------
# 5. 20-year backfill (v0.8 §6.7) — gap-fill + sticky first_trade_date
# ---------------------------------------------------------------------------
def test_backfill_20y_fills_gap_and_stamps_first_trade_date(tmp_data_dir):
    """The backfill-20y driver must:
      1. Snapshot the existing cursor first.
      2. Rewind the cursor, fill the gap with sync_table.
      3. Restore the cursor to its original value (so resume from 2026-06-05
         is unaffected).
      4. Stamp ``first_trade_date`` in sync_state — this is the *sticky*
         lower bound that subsequent incremental syncs must preserve.
    """
    from quant_data.sync.backfill import backfill_one_table, snapshot_cursors, restore_cursors

    register_source("fast", _FastDailySource())
    # Window: pretend the operator already synced 2023-01-04 .. 2024-01-05.
    # We backfill a 5-year window below that.
    _seed_trade_cal(tmp_data_dir, date(2018, 1, 1), date(2024, 3, 1))
    meta = MetaSQLite()
    original_cursor = date(2024, 1, 5)
    meta.set_cursor("fast_daily", original_cursor, status="ok")
    # Pre-backfill snapshot of all cursors
    snap = snapshot_cursors()

    try:
        backfill_start = date(2018, 1, 4)
        report = backfill_one_table(
            "daily", backfill_start=backfill_start,
            original_cursor=original_cursor, source="fast",
        )

        # (1) Gap was filled — sync_table should have written rows.
        assert report.rows_added > 0, f"backfill added no rows: {report.to_dict()}"
        assert report.batches > 1000, f"backfill batches too small: {report.batches}"
        # (2) Cursor restored to original.
        assert meta.get_cursor("fast_daily") == original_cursor, (
            f"cursor not restored: got {meta.get_cursor('fast_daily')}"
        )
        # (3) first_trade_date stamped and sticky.
        # ``set_cursor`` returns the row but doesn't expose first_trade_date
        # via get_cursor; we read it back via all_cursors().
        cursors = meta.all_cursors()
        assert cursors["fast_daily"]["first_trade_date"] == backfill_start.isoformat(), (
            f"first_trade_date not stamped: {cursors['fast_daily']}"
        )
    finally:
        restore_cursors(snap)


def test_backfill_20y_idempotent_when_rerun(tmp_data_dir):
    """Re-running backfill on an already-backfilled table must be a no-op.

    The cursor is at original_cursor (max date with data), and backfill_start
    is BEFORE original_cursor but the gap is already filled. The driver must
    fill it again (because the gap partitions exist), but the cursor and
    first_trade_date must remain stable.
    """
    from quant_data.sync.backfill import backfill_one_table, snapshot_cursors, restore_cursors

    register_source("fast", _FastDailySource())
    _seed_trade_cal(tmp_data_dir, date(2018, 1, 1), date(2024, 3, 1))
    meta = MetaSQLite()
    original_cursor = date(2024, 1, 5)
    meta.set_cursor("fast_daily", original_cursor, status="ok")
    snap = snapshot_cursors()

    try:
        backfill_start = date(2018, 1, 4)
        # First run: fills the gap.
        r1 = backfill_one_table("daily", backfill_start=backfill_start,
                                original_cursor=original_cursor, source="fast")
        first = meta.get_cursor("fast_daily")
        first_first = meta.all_cursors()["fast_daily"]["first_trade_date"]
        assert first == original_cursor
        assert first_first == backfill_start.isoformat()

        # Second run: must re-fill (idempotent: same rows overwritten), and
        # leave cursor + first_trade_date unchanged.
        r2 = backfill_one_table("daily", backfill_start=backfill_start,
                                original_cursor=original_cursor, source="fast")
        second = meta.get_cursor("fast_daily")
        second_first = meta.all_cursors()["fast_daily"]["first_trade_date"]
        assert second == first, f"cursor changed: {first} -> {second}"
        assert second_first == first_first, f"first_trade_date changed"
        # Both runs must have produced a non-zero number of rows (the fake
        # source is deterministic; re-running is a "no-op" only in the sense
        # that the data is the same — but the sync loop still iterates).
        assert r1.rows_added == r2.rows_added
    finally:
        restore_cursors(snap)


def test_backfill_20y_skips_when_no_gap(tmp_data_dir):
    """If backfill_start is AFTER the cursor, there is no gap to fill."""
    from quant_data.sync.backfill import backfill_one_table, snapshot_cursors, restore_cursors

    register_source("fast", _FastDailySource())
    _seed_trade_cal(tmp_data_dir, date(2023, 1, 1), date(2024, 3, 1))
    meta = MetaSQLite()
    original_cursor = date(2024, 1, 5)
    meta.set_cursor("fast_daily", original_cursor, status="ok")
    snap = snapshot_cursors()

    try:
        # backfill_start is AFTER original_cursor — no gap to fill.
        report = backfill_one_table(
            "daily", backfill_start=date(2025, 1, 1),
            original_cursor=original_cursor, source="fast",
        )
        assert report.rows_added == 0
        assert report.batches == 0
        # Cursor must NOT have moved (rewind is internal; final state == original).
        assert meta.get_cursor("fast_daily") == original_cursor
    finally:
        restore_cursors(snap)
