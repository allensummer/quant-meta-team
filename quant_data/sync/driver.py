"""Generic sync driver + 5 table-specific entry points.

Contract (v0.4 §4):
  - All time-series tables pull by ``trade_date`` (NOT ts_code) — 1 req = whole market.
  - Cursors live in SQLite ``sync_state``; Parquet is the source of truth; DuckDB views
    read off Parquet globs.
  - Idempotent: re-running the same date replaces the day's rows in DuckDB and
    overwrites the partition's parquet.
"""
from __future__ import annotations

import logging
import time
from datetime import date, datetime, timedelta
from typing import Any, Callable

import duckdb
import pandas as pd

from quant_data.paths import data_dir
from quant_data.registry import get_schema, get_source
from quant_data.store.duckdb_store import DuckDBStore
from quant_data.store.meta_sqlite import MetaSQLite
from quant_data.store.parquet_store import ParquetStore

log = logging.getLogger("quant_data.sync")


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
_DUCK_TYPES = {
    "string": "VARCHAR",
    "float64": "DOUBLE",
    "float32": "FLOAT",
    "int64": "BIGINT",
    "int32": "INTEGER",
    "int16": "SMALLINT",
    "int8": "TINYINT",
    "date": "DATE",
    "bool": "BOOLEAN",
}


def _open_store(topic: str, source: str = "tushare") -> tuple[DuckDBStore, MetaSQLite, ParquetStore]:
    duck = DuckDBStore()
    meta = MetaSQLite()
    pq = ParquetStore(source=source, topic=topic)
    duck.register_parquet(topic, pq)
    # ensure a backing table exists in duckdb for upsert().
    # The parquet tree may be empty (first run) — derive a DDL from the
    # registered TableSchema so we don't depend on read_parquet succeeding.
    schema = get_schema(topic)
    parts = []
    for f in schema.fields.values():
        dt = _DUCK_TYPES.get(f.dtype.lower(), "VARCHAR")
        parts.append(f"{f.name} {dt}")
    cols_ddl = ", ".join(parts)
    backing = f"raw_{source}_{topic}"
    try:
        duck.con.execute(f"CREATE TABLE IF NOT EXISTS {backing} ({cols_ddl})")
    except duckdb.Error:
        # last-resort: try reading from the parquet tree (will work only if files exist)
        duck.con.execute(
            f"CREATE TABLE IF NOT EXISTS {backing} AS "
            f"SELECT * FROM read_parquet('{pq.glob_for_duckdb()}') WHERE 1=0"
        )
    return duck, meta, pq


def _df_to_parquet_columns(df: pd.DataFrame, schema) -> pd.DataFrame:
    """Project df to the schema's field list and cast dates."""
    cols = schema.field_names()
    keep = [c for c in cols if c in df.columns]
    df = df[keep].copy()
    for c in ("trade_date", "cal_date", "list_date", "delist_date", "pretrade_date"):
        if c in df.columns:
            if df[c].dtype == object:
                df[c] = pd.to_datetime(df[c], errors="coerce").dt.date
    return df


def _all_trade_days(meta: MetaSQLite, start: date, end: date) -> list[date]:
    """Use the trade_cal table to enumerate real trading days.

    Falls back to every calendar day if trade_cal isn't synced yet.
    """
    from quant_data.paths import data_dir as _dd
    parquet_root = _dd() / "raw_tushare_trade_cal"
    if not parquet_root.exists() or not any(parquet_root.rglob("*.parquet")):
        log.warning("trade_cal not materialized; falling back to calendar-day enumeration")
        return [start + timedelta(days=i) for i in range((end - start).days + 1)]
    import duckdb
    con = duckdb.connect()
    try:
        rows = con.execute(
            "SELECT DISTINCT CAST(cal_date AS DATE) AS d "
            f"FROM read_parquet('{parquet_root}/**/*.parquet') "
            "WHERE is_open = 1 AND CAST(cal_date AS DATE) BETWEEN ? AND ? "
            "ORDER BY d",
            [start, end],
        ).fetchall()
    finally:
        con.close()
    if not rows:
        log.warning("trade_cal returned 0 open days in [%s, %s]; falling back to calendar", start, end)
        return [start + timedelta(days=i) for i in range((end - start).days + 1)]
    return [r[0] for r in rows]


# ---------------------------------------------------------------------------
# core driver
# ---------------------------------------------------------------------------
def sync_table(
    topic: str,
    *,
    source: str = "tushare",
    start_date: date | None = None,
    end_date: date | None = None,
    fetch_fn: Callable[..., pd.DataFrame] | None = None,
    partition_key: str = "trade_date",
    lookback_days: int = 0,
) -> dict[str, Any]:
    """Sync a single table. Returns a report dict for `make report` / issue comments.

    Algorithm
    ---------
    1. Resolve source + schema.
    2. Read cursor (last successful trade_date from SQLite).
    3. Enumerate target dates = open trading days in [max(cursor+1, start), end].
    4. For each date, call fetch_fn (or source.fetch) and write Parquet + DuckDB.
    5. Persist lineage; advance cursor.
    """
    t0 = time.monotonic()
    schema = get_schema(topic)
    src = get_source(source)
    duck, meta, pq = _open_store(topic, source=source)

    cursor = meta.get_cursor(f"{source}_{topic}")
    if start_date is None:
        start_date = cursor + timedelta(days=1) if cursor else date(2010, 1, 1)
    if end_date is None:
        end_date = date.today()
    # If we already have a cursor, the user-supplied start_date is only a
    # LOWER BOUND — we never re-pull dates that have already been synced.
    # This is the property that makes the sync idempotent.
    if cursor is not None:
        cursor_plus_one = cursor + timedelta(days=1)
        if cursor_plus_one > start_date:
            start_date = cursor_plus_one

    if start_date > end_date:
        log.info("sync_table %s: nothing to do (cursor %s already covers up to %s)",
                 topic, cursor, end_date)
        return {"topic": topic, "rows": 0, "batches": 0, "elapsed_s": 0.0, "rate_limit_hit": 0}

    days = _all_trade_days(meta, start_date, end_date)
    if not days:
        log.warning("sync_table %s: no open trading days in [%s, %s]", topic, start_date, end_date)
        return {"topic": topic, "rows": 0, "batches": 0, "elapsed_s": 0.0, "rate_limit_hit": 0}

    total_rows = 0
    batches = 0
    last_date: date | None = None

    fetch = fetch_fn or (lambda **p: src.fetch(topic, **p))
    rate_limit_hit_before = getattr(src._rl, "rate_limit_hit", 0)

    for d in days:
        params = {partition_key: d.strftime("%Y%m%d")}
        # ``trade_cal`` accepts exchange param; default SSE
        if topic == "trade_cal":
            params.setdefault("exchange", "SSE")
        try:
            df = fetch(**params)
        except Exception as e:
            log.exception("sync %s %s failed: %s", topic, d, e)
            # Record the failure against the last successfully-synced date.
            # The cursor is NOT advanced past a failed date, so resume picks
            # up from last_good + 1 (matching v0.4 §4.2).
            if last_date is not None:
                meta.set_cursor(f"{source}_{topic}", last_date, status="failed", error=str(e))
            else:
                # Nothing was synced yet — write a stub row so the operator
                # can see the failure in sync_state.
                meta.set_cursor(f"{source}_{topic}", d, status="failed", error=str(e))
            break

        if df is None:
            df = pd.DataFrame()
        df = _df_to_parquet_columns(df, schema)

        # write Parquet (partition by date col)
        pq.write(df, partition_value=d)
        # upsert into DuckDB backing table (idempotent on (ts_code, trade_date))
        if not df.empty:
            duck.upsert(f"{source}_{topic}", df, schema.version)
        meta.set_cursor(f"{source}_{topic}", d, status="ok")

        # lineage
        try:
            rec = src.lineage(
                table=f"{source}_{topic}",
                schema_version=schema.version,
                params=params,
                rows=len(df),
            )
            meta.write_lineage(rec)
        except Exception as e:  # pragma: no cover
            log.debug("lineage write failed (non-fatal): %s", e)

        total_rows += len(df)
        batches += 1
        last_date = d

    elapsed = time.monotonic() - t0
    rate_limit_hit = getattr(src._rl, "rate_limit_hit", 0) - rate_limit_hit_before
    return {
        "topic": f"{source}_{topic}",
        "rows": total_rows,
        "batches": batches,
        "last_date": last_date.isoformat() if last_date else None,
        "elapsed_s": round(elapsed, 2),
        "rate_limit_hit": rate_limit_hit,
    }


# ---------------------------------------------------------------------------
# per-table entry points (v0.4 §3.2)
# ---------------------------------------------------------------------------
def sync_stock_basic(*, source: str = "tushare") -> dict[str, Any]:
    """Stock universe is a snapshot — re-pull fully on each call."""
    t0 = time.monotonic()
    schema = get_schema("stock_basic")
    src = get_source(source)
    duck, meta, pq = _open_store("stock_basic", source=source)
    df = src.fetch("stock_basic", list_status="L,D,P")
    df = _df_to_parquet_columns(df, schema)
    pq.write(df, partition_value=None)
    duck.upsert(f"{source}_stock_basic", df, schema.version)
    meta.set_cursor(f"{source}_stock_basic", date.today(), status="ok")
    rec = src.lineage(table=f"{source}_stock_basic", schema_version=schema.version,
                      params={"list_status": "L,D,P"}, rows=len(df))
    meta.write_lineage(rec)
    return {
        "topic": f"{source}_stock_basic",
        "rows": len(df),
        "batches": 1,
        "last_date": date.today().isoformat(),
        "elapsed_s": round(time.monotonic() - t0, 2),
        "rate_limit_hit": getattr(src._rl, "rate_limit_hit", 0),
    }


def sync_trade_cal(*, start: date | None = None, end: date | None = None,
                   source: str = "tushare") -> dict[str, Any]:
    """trade_cal is keyed by (exchange, cal_date) — one batch is cheap.

    Uses the cursor as the lower bound so repeated calls are no-ops for already-
    synced ranges.
    """
    t0 = time.monotonic()
    schema = get_schema("trade_cal")
    src = get_source(source)
    duck, meta, pq = _open_store("trade_cal", source=source)

    cursor = meta.get_cursor(f"{source}_trade_cal")
    if end is None:
        end = date.today()
    if start is None:
        start = (cursor + timedelta(days=1)) if cursor else date(2010, 1, 1)
    if cursor is not None:
        cur_plus_one = cursor + timedelta(days=1)
        if cur_plus_one > start:
            start = cur_plus_one
    if start > end:
        log.info("sync_trade_cal: nothing to do (cursor %s covers up to %s)", cursor, end)
        return {"topic": f"{source}_trade_cal", "rows": 0, "batches": 0, "elapsed_s": 0.0,
                "last_date": str(end), "rate_limit_hit": 0}

    rows_total = 0
    batches = 0
    for exch in ("SSE", "SZSE", "BSE"):
        df = src.fetch("trade_cal", exchange=exch,
                       start_date=start.strftime("%Y%m%d"),
                       end_date=end.strftime("%Y%m%d"))
        df = _df_to_parquet_columns(df, schema)
        if not df.empty:
            for d_val, sub in df.groupby("cal_date"):
                pq.write(sub.reset_index(drop=True), partition_value=d_val)
                duck.upsert(f"{source}_trade_cal", sub, schema.version)
                meta.set_cursor(f"{source}_trade_cal", d_val, status="ok")
                rows_total += len(sub)
                batches += 1

    rec = src.lineage(table=f"{source}_trade_cal", schema_version=schema.version,
                      params={"start": str(start), "end": str(end), "exchanges": ["SSE", "SZSE", "BSE"]},
                      rows=rows_total)
    meta.write_lineage(rec)
    return {
        "topic": f"{source}_trade_cal",
        "rows": rows_total,
        "batches": batches,
        "last_date": str(end),
        "elapsed_s": round(time.monotonic() - t0, 2),
        "rate_limit_hit": getattr(src._rl, "rate_limit_hit", 0),
    }


def sync_daily(*, source: str = "tushare", start_date: date | None = None,
               end_date: date | None = None) -> dict[str, Any]:
    return sync_table("daily", source=source, start_date=start_date, end_date=end_date)


def sync_adj_factor(*, source: str = "tushare", start_date: date | None = None,
                    end_date: date | None = None) -> dict[str, Any]:
    return sync_table("adj_factor", source=source, start_date=start_date, end_date=end_date)


def sync_daily_basic(*, source: str = "tushare", start_date: date | None = None,
                     end_date: date | None = None) -> dict[str, Any]:
    return sync_table("daily_basic", source=source, start_date=start_date, end_date=end_date)


# ---------------------------------------------------------------------------
# full sync (used by `make sync-full`)
# ---------------------------------------------------------------------------
def sync_full(*, source: str = "tushare") -> list[dict[str, Any]]:
    """Full history backfill. Order: stock_basic -> trade_cal -> daily -> adj_factor -> daily_basic.

    daily / adj_factor / daily_basic each pull by trade_date, so the same trade_cal
    cursor naturally amortizes across them.
    """
    log.info("== sync_full starting (source=%s) ==", source)
    reports: list[dict[str, Any]] = []
    reports.append(sync_stock_basic(source=source))

    # trade_cal for the full A-share history horizon
    today = date.today()
    horizon_start = date(2010, 1, 1)
    reports.append(sync_trade_cal(start=horizon_start, end=today, source=source))

    for topic in ("daily", "adj_factor", "daily_basic"):
        reports.append(sync_table(topic, source=source,
                                  start_date=horizon_start, end_date=today))

    log.info("== sync_full done: %s ==", reports)
    return reports
