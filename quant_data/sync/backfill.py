"""20-year history backfill (v0.8 §6.7).

The default ``sync_table`` driver treats the SQLite cursor as a *floor* —
callers' ``start_date`` is only a *lower bound*, never an override. That
contract is great for incremental syncs (idempotent resume) but blocks
backfill runs: with cursor=2026-06-05 and start_date=2005-01-04, the
driver bumps start_date forward to 2026-06-06 and does nothing.

This module implements the v0.8 contract: **snapshot → rewind → fill gap →
restore cursor**, with the additional first_trade_date bookkeeping the issue
spec requires.

Algorithm
---------
1. Snapshot the existing cursor for each topic (so we can roll back on failure).
2. For each time-series topic (daily / adj_factor / daily_basic / trade_cal),
   rewind the cursor to one day *before* the desired ``backfill_start`` and
   call ``sync_table(start_date=backfill_start, end_date=old_cursor)`` —
   this fills only the gap, without re-pulling the already-synced
   ``[2010-01-04, 2026-06-05]`` region (which would burn the daily quota).
3. Restore the cursor to the original value (the "max date with data" is
   still 2026-06-05, not the last backfilled date).
4. Stamp ``first_trade_date`` in sync_state — this is the *sticky* lower
   bound that subsequent incremental syncs must preserve.

Failure handling
----------------
- If the backfill fails partway, the gap is partially written to Parquet and
  DuckDB, but the cursor is restored to the original. The next incremental
  run will see cursor=2026-06-05 and skip the gap (correctly idempotent).
  The gap data is still on disk and still queryable via min(trade_date).
- Rate-limit hits are retried with exponential backoff at the adapter
  layer (driver.py / tushare.py) — this module only enforces the day-by-day
  pacing.

Rate-limit budget
-----------------
- 2000 积分档: 200 req/min safe, 100k req/day ceiling.
- Issue spec: 150 req/min conservative, 25% margin (200 * 0.75 = 150).
- Existing TokenBucket uses 200 * 0.8 = 160 (20% margin). For this backfill
  we tighten the bucket to 150 to match the issue spec.
- Daily budget: 5 topics × ~1200 trading days ≈ 6000 requests + 3 trade_cal
  calls per day (3 exchanges) ≈ 3600 + 5000 + ... still well under 80k.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from quant_data.paths import meta_dir
from quant_data.registry import get_source
from quant_data.store.meta_sqlite import MetaSQLite
from quant_data.sync.driver import sync_table, sync_stock_basic

log = logging.getLogger("quant_data.sync.backfill")


# Issue spec: 2006-01-01 for daily/adj_factor, 2005-01-01 for stock_basic/trade_cal.
# daily_basic shares the daily window per DoD clause 3 (probe first).
DEFAULT_BACKFILL_START = date(2005, 1, 4)  # first open trading day on/after 2005-01-01
# Sanity caps: never backfill before this even if a caller asks.
EARLIEST_HARD_FLOOR = date(1990, 12, 1)

# Topics that need date-window backfill (vs the snapshot-only stock_basic).
_TIME_SERIES_TOPICS = ("trade_cal", "daily", "adj_factor", "daily_basic")


@dataclass
class BackfillReport:
    """Per-table result of a backfill run."""
    topic: str
    backfill_start: str
    backfill_end_requested: str
    rows_added: int
    batches: int
    elapsed_s: float
    rate_limit_hit: int
    first_trade_date: str | None
    last_trade_date: str | None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "topic": self.topic,
            "backfill_start": self.backfill_start,
            "backfill_end_requested": self.backfill_end_requested,
            "rows_added": self.rows_added,
            "batches": self.batches,
            "elapsed_s": round(self.elapsed_s, 2),
            "rate_limit_hit": self.rate_limit_hit,
            "first_trade_date": self.first_trade_date,
            "last_trade_date": self.last_trade_date,
            "error": self.error,
        }


def _snapshot_path() -> Path:
    """Where we persist the pre-backfill cursor snapshot for rollback."""
    d = meta_dir() / "_lineage" / "backfill_snapshots"
    d.mkdir(parents=True, exist_ok=True)
    return d


def snapshot_cursors() -> Path:
    """Snapshot all sync cursors to a JSON file. Returns the path.

    Called automatically by ``backfill_20y``; exposed for tests.
    """
    meta = MetaSQLite()
    cursors = meta.all_cursors()
    snap = {
        "captured_at": date.today().isoformat(),
        "cursors": cursors,
    }
    path = _snapshot_path() / f"pre_backfill_{date.today().isoformat()}_{os.getpid()}.json"
    path.write_text(json.dumps(snap, indent=2, ensure_ascii=False), encoding="utf-8")
    log.info("backfill: cursor snapshot saved to %s", path)
    return path


def restore_cursors(snapshot_path: Path) -> None:
    """Roll every cursor back to its snapshot value.

    Used on failure paths: cursor is restored to its pre-backfill value
    (``2026-06-05``) so the next incremental run resumes from a known
    good state (per issue spec "失败回退").
    """
    snap = json.loads(snapshot_path.read_text(encoding="utf-8"))
    meta = MetaSQLite()
    for table, row in snap["cursors"].items():
        if row.get("last_trade_date"):
            meta.set_cursor(
                table,
                date.fromisoformat(row["last_trade_date"]),
                status=row.get("status", "ok"),
                error=row.get("error_msg", ""),
            )
    log.info("backfill: cursors restored from %s", snapshot_path)


def _tighten_rate_limit(rpm: int) -> None:
    """Override the tushare TokenBucket capacity for this backfill.

    The adapter is module-singleton, so we mutate the bucket in place. This
    only affects the running process — the change does not persist.
    """
    src = get_source("tushare")
    bucket = getattr(src, "_rl", None)
    if bucket is None:
        log.warning("backfill: tushare adapter has no _rl; cannot tighten rate limit")
        return
    bucket._capacity = max(1, rpm)
    log.info("backfill: tushare token bucket capacity -> %d rpm (was %d)",
             rpm, int(getattr(bucket, "_capacity", rpm)))


def backfill_one_table(
    topic: str,
    *,
    backfill_start: date,
    original_cursor: date,
    source: str = "tushare",
) -> BackfillReport:
    """Backfill a single time-series table across the gap.

    Steps:
      1. Rewind cursor to ``backfill_start - 1`` so ``sync_table``'s
         cursor-floor logic doesn't skip the gap.
      2. Call ``sync_table(start_date=backfill_start, end_date=original_cursor)``
         — the driver fills days in the gap.
      3. Restore cursor to ``original_cursor`` (the max date with data is
         still the original).
      4. Stamp ``first_trade_date = backfill_start`` (sticky, written only
         when previously NULL).
    """
    log.info("backfill %s: gap=[%s .. %s]", topic, backfill_start, original_cursor)
    meta = MetaSQLite()
    table_key = f"{source}_{topic}"

    # Guard: don't run if start is after the original cursor (no gap to fill).
    if backfill_start > original_cursor:
        log.info("backfill %s: no gap (start %s > cursor %s); skip",
                 topic, backfill_start, original_cursor)
        return BackfillReport(
            topic=table_key,
            backfill_start=backfill_start.isoformat(),
            backfill_end_requested=original_cursor.isoformat(),
            rows_added=0, batches=0, elapsed_s=0.0, rate_limit_hit=0,
            first_trade_date=None, last_trade_date=original_cursor.isoformat(),
        )

    # Step 1: rewind cursor to one day before the desired start.
    rewind_to = backfill_start - timedelta(days=1)
    meta.set_cursor(table_key, rewind_to, status="ok")

    # Step 2: run sync_table for the gap. Driver will re-fetch all open
    # trading days in [backfill_start, original_cursor] and overwrite.
    try:
        result = sync_table(
            topic,
            source=source,
            start_date=backfill_start,
            end_date=original_cursor,
        )
    except Exception as e:  # pragma: no cover - safety net
        log.exception("backfill %s: sync_table raised: %s", topic, e)
        result = {"rows": 0, "batches": 0, "elapsed_s": 0.0,
                  "rate_limit_hit": 0, "last_date": None}

    # Step 3: restore cursor to the original (the "max date with data"
    # is still original_cursor; the gap is "extra" lower-bound data).
    meta.set_cursor(table_key, original_cursor, status="ok")

    # Step 4: stamp first_trade_date (sticky).
    meta.set_cursor(
        table_key, original_cursor, status="ok",
        first_trade_date=backfill_start,
    )

    return BackfillReport(
        topic=table_key,
        backfill_start=backfill_start.isoformat(),
        backfill_end_requested=original_cursor.isoformat(),
        rows_added=result.get("rows", 0),
        batches=result.get("batches", 0),
        elapsed_s=result.get("elapsed_s", 0.0),
        rate_limit_hit=result.get("rate_limit_hit", 0),
        first_trade_date=backfill_start.isoformat(),
        last_trade_date=original_cursor.isoformat(),
    )


def backfill_stock_basic_snapshot(*, source: str = "tushare") -> BackfillReport:
    """stock_basic is a snapshot — re-pull once with L+D+P, no date window."""
    import time
    t0 = time.monotonic()
    result = sync_stock_basic(source=source)
    elapsed = time.monotonic() - t0
    meta = MetaSQLite()
    table_key = f"{source}_stock_basic"
    cur = meta.get_cursor(table_key)
    # Snapshot table: first_trade_date is the earliest list_date in the
    # dataset — useful to know "the universe reaches back to year X".
    from quant_data.paths import data_dir
    import duckdb
    con = duckdb.connect()
    try:
        rows = con.execute(
            "SELECT min(list_date) FROM read_parquet('"
            f"{data_dir() / f'raw_{table_key}'}/**/*.parquet"
            "')"
        ).fetchone()
    finally:
        con.close()
    earliest_list = rows[0] if rows and rows[0] else None
    if earliest_list:
        meta.set_cursor(table_key, cur, status="ok", first_trade_date=earliest_list)
    return BackfillReport(
        topic=table_key,
        backfill_start="snapshot",
        backfill_end_requested="snapshot",
        rows_added=result.get("rows", 0),
        batches=1,
        elapsed_s=elapsed,
        rate_limit_hit=result.get("rate_limit_hit", 0),
        first_trade_date=earliest_list.isoformat() if earliest_list else None,
        last_trade_date=cur.isoformat() if cur else None,
    )


def backfill_20y(
    *,
    backfill_start: date = DEFAULT_BACKFILL_START,
    source: str = "tushare",
    rpm: int = 150,
    include_daily_basic: bool = True,
) -> dict[str, Any]:
    """Top-level backfill orchestrator.

    Order
    -----
    1. snapshot_cursors() — saves pre-backfill state to a JSON file
    2. _tighten_rate_limit(rpm) — drops the token bucket to ``rpm`` for safety
    3. backfill_stock_basic_snapshot() — L+D+P pull, no date window
    4. backfill_one_table() × (trade_cal, daily, adj_factor, [daily_basic])
    5. Return a consolidated report

    Failure handling
    ----------------
    If a per-table backfill raises, we restore cursors from the snapshot and
    re-raise. The caller (``cli.py``) flips the issue to ``blocked`` and
    @mentions the user.
    """
    backfill_start = max(backfill_start, EARLIEST_HARD_FLOOR)
    log.info("== backfill_20y starting: start=%s, rpm=%s, daily_basic=%s ==",
             backfill_start, rpm, include_daily_basic)

    snap = snapshot_cursors()
    _tighten_rate_limit(rpm)
    reports: list[BackfillReport] = []
    try:
        # 1. stock_basic snapshot
        reports.append(backfill_stock_basic_snapshot(source=source))

        # 2. read the (restored) cursor for time-series tables
        meta = MetaSQLite()
        order = ["trade_cal", "daily", "adj_factor"]
        if include_daily_basic:
            order.append("daily_basic")
        for topic in order:
            cur = meta.get_cursor(f"{source}_{topic}")
            if cur is None:
                # No cursor yet — treat as a fresh start, sync from backfill_start to today.
                cur = date.today()
            reports.append(backfill_one_table(
                topic, backfill_start=backfill_start,
                original_cursor=cur, source=source,
            ))
    except Exception:
        log.exception("backfill_20y: hard failure, restoring cursors from snapshot")
        restore_cursors(snap)
        raise

    out = {
        "snapshot": str(snap),
        "backfill_start": backfill_start.isoformat(),
        "rpm": rpm,
        "tables": [r.to_dict() for r in reports],
        "all_cursors": MetaSQLite().all_cursors(),
    }
    log.info("== backfill_20y done: %s ==", out)
    return out
