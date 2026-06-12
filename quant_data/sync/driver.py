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
    # Date columns get a YYYYMMDD -> date coercion. Covers v0.8 S-tier
    # (ann_date, end_date) and v0.9 A-tier (in_date, out_date, audit_date,
    # float_date, suspend_date, resume_date, record_date, ex_date, pay_date,
    # report_date, f_ann_date).
    for c in ("trade_date", "cal_date", "list_date", "delist_date",
              "pretrade_date", "ann_date", "end_date",
              "in_date", "out_date", "audit_date", "float_date",
              "suspend_date", "resume_date", "record_date", "ex_date",
              "pay_date", "report_date", "f_ann_date"):
        if c in df.columns:
            if df[c].dtype == object or pd.api.types.is_string_dtype(df[c]):
                df[c] = pd.to_datetime(df[c], format="%Y%m%d", errors="coerce").dt.date
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
        # upsert into DuckDB backing table (idempotent on schema.primary_key)
        if not df.empty:
            duck.upsert(f"{source}_{topic}", df, schema.version,
                        primary_key=schema.primary_key)
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
# S-tier sync functions (v0.8 — ADM-652)
# ---------------------------------------------------------------------------
def sync_moneyflow(*, source: str = "tushare", start_date: date | None = None,
                   end_date: date | None = None) -> dict[str, Any]:
    """moneyflow: per-day all-market fetch (1 req = whole market).

    Uses the generic ``sync_table`` driver; PK is ``(ts_code, trade_date)``
    so the standard ``trade_date`` partition + ``(ts_code, trade_date)``
    upsert path applies without modification.
    """
    return sync_table("moneyflow", source=source, start_date=start_date, end_date=end_date)


def sync_moneyflow_hsgt(*, source: str = "tushare", start_date: date | None = None,
                        end_date: date | None = None) -> dict[str, Any]:
    """moneyflow_hsgt: per-day 1-row cross-border snapshot.

    2000 积分/次, ~1 row/day. Partitioned by trade_date; PK is
    ``(trade_date)`` only. Reuses ``sync_table`` with partition_key=trade_date
    and a small wrapper that uses the new ``trade_date``-only upsert path
    added in ``DuckDBStore.upsert``.
    """
    return sync_table("moneyflow_hsgt", source=source, start_date=start_date, end_date=end_date)


def sync_hsgt_top10(*, source: str = "tushare", start_date: date | None = None,
                   end_date: date | None = None) -> dict[str, Any]:
    """hsgt_top10: per-day top-10 northbound net-buy stocks.

    1000 积分/次, ≤10 rows/day. PK ``(trade_date, ts_code)`` — fits the
    standard ``(ts_code, trade_date)`` upsert path.
    """
    return sync_table("hsgt_top10", source=source, start_date=start_date, end_date=end_date)


# Default index pool for index_weight. 沪深 300 / 中证 500 / 中证 1000 / 上证 50 /
# 申万 300 are the canonical A-share benchmarks. Operators can override via
# the ``INDEX_POOL`` env var (comma-separated) if their portfolio targets
# a different index family.
_DEFAULT_INDEX_POOL: tuple[str, ...] = (
    "000300.SH",   # 沪深 300
    "000905.SH",   # 中证 500
    "000852.SH",   # 中证 1000
    "000016.SH",   # 上证 50
    "000001.SH",   # 上证指数
    "399001.SZ",   # 深证成指
    "399006.SZ",   # 创业板指
    "399905.SZ",   # 中证 500 (深)
    "000846.SH",   # 申万 300
)


def _load_index_pool() -> tuple[str, ...]:
    """Allow operators to extend/override the index_weight universe."""
    import os
    env = os.getenv("INDEX_POOL", "").strip()
    if not env:
        return _DEFAULT_INDEX_POOL
    return tuple(s.strip() for s in env.split(",") if s.strip())


def sync_index_weight(*, source: str = "tushare", start_date: date | None = None,
                      end_date: date | None = None,
                      index_pool: tuple[str, ...] | None = None) -> dict[str, Any]:
    """index_weight: per-(index, day) fetch.

    中证/沪深 indices update monthly; 申万 updates daily. The API requires
    an explicit ``index_code``, so we iterate over ``index_pool`` × days.
    2000 积分/次; 1 batch = 1 index × 1 day.
    """
    t0 = time.monotonic()
    schema = get_schema("index_weight")
    src = get_source(source)
    duck, meta, pq = _open_store("index_weight", source=source)

    pool = index_pool if index_pool is not None else _load_index_pool()
    if not pool:
        raise ValueError("index_weight: index_pool is empty (set INDEX_POOL env or pass explicitly)")

    cursor = meta.get_cursor(f"{source}_index_weight")
    if start_date is None:
        start_date = cursor + timedelta(days=1) if cursor else date(2005, 1, 1)
    if end_date is None:
        end_date = date.today()
    if cursor is not None:
        cursor_plus_one = cursor + timedelta(days=1)
        if cursor_plus_one > start_date:
            start_date = cursor_plus_one
    if start_date > end_date:
        log.info("sync_index_weight: nothing to do (cursor %s covers up to %s)", cursor, end_date)
        return {"topic": f"{source}_index_weight", "rows": 0, "batches": 0,
                "elapsed_s": 0.0, "rate_limit_hit": 0}

    days = _all_trade_days(meta, start_date, end_date)
    if not days:
        log.warning("sync_index_weight: no open trading days in [%s, %s]", start_date, end_date)
        return {"topic": f"{source}_index_weight", "rows": 0, "batches": 0,
                "elapsed_s": 0.0, "rate_limit_hit": 0}

    total_rows = 0
    batches = 0
    last_date: date | None = None
    rate_limit_hit_before = getattr(src._rl, "rate_limit_hit", 0)

    for idx_code in pool:
        for d in days:
            params = {"index_code": idx_code, "trade_date": d.strftime("%Y%m%d")}
            try:
                df = src.fetch("index_weight", **params)
            except Exception as e:
                log.exception("sync index_weight %s %s failed: %s", idx_code, d, e)
                if last_date is not None:
                    meta.set_cursor(f"{source}_index_weight", last_date,
                                    status="failed", error=str(e))
                else:
                    meta.set_cursor(f"{source}_index_weight", d,
                                    status="failed", error=str(e))
                return {"topic": f"{source}_index_weight", "rows": total_rows,
                        "batches": batches, "last_date": str(last_date) if last_date else None,
                        "elapsed_s": round(time.monotonic() - t0, 2),
                        "rate_limit_hit": (getattr(src._rl, "rate_limit_hit", 0)
                                            - rate_limit_hit_before),
                        "ok": False, "error": str(e)}
            if df is None:
                df = pd.DataFrame()
            df = _df_to_parquet_columns(df, schema)
            if not df.empty:
                pq.write(df, partition_value=d)
                duck.upsert(f"{source}_index_weight", df, schema.version,
                            primary_key=schema.primary_key)
                total_rows += len(df)
            meta.set_cursor(f"{source}_index_weight", d, status="ok")
            batches += 1
            last_date = d
        # lineage per index — one record per index to keep file count manageable
        try:
            rec = src.lineage(
                table=f"{source}_index_weight", schema_version=schema.version,
                params={"index_code": idx_code, "trade_date": str(days[-1])},
                rows=total_rows,
            )
            meta.write_lineage(rec)
        except Exception as e:  # pragma: no cover
            log.debug("lineage write failed (non-fatal): %s", e)

    rate_limit_hit = getattr(src._rl, "rate_limit_hit", 0) - rate_limit_hit_before
    return {
        "topic": f"{source}_index_weight",
        "rows": total_rows,
        "batches": batches,
        "last_date": last_date.isoformat() if last_date else None,
        "elapsed_s": round(time.monotonic() - t0, 2),
        "rate_limit_hit": rate_limit_hit,
    }


def sync_fund_holdings(*, source: str = "tushare", start_date: date | None = None,
                       end_date: date | None = None) -> dict[str, Any]:
    """fund_holdings: quarterly snapshot keyed on ``end_date`` (季报期).

    2000 积分/次; the API exposes ``ann_date`` and ``end_date`` query params
    but not ``trade_date``. We iterate over quarter-end dates within the
    window instead of trading days. The cursor tracks the last ``end_date``
    synced, so re-runs are no-ops.

    Per issue constraint (ADM-652): no ``import tushare`` / ``akshare`` in
    sync drivers — tushare is reached only via the registered adapter.
    """
    t0 = time.monotonic()
    schema = get_schema("fund_holdings")
    src = get_source(source)
    duck, meta, pq = _open_store("fund_holdings", source=source)

    cursor = meta.get_cursor(f"{source}_fund_holdings")
    if start_date is None:
        start_date = cursor + timedelta(days=1) if cursor else date(2005, 1, 1)
    if end_date is None:
        end_date = date.today()
    if cursor is not None:
        cursor_plus_one = cursor + timedelta(days=1)
        if cursor_plus_one > start_date:
            start_date = cursor_plus_one
    if start_date > end_date:
        log.info("sync_fund_holdings: nothing to do (cursor %s covers up to %s)", cursor, end_date)
        return {"topic": f"{source}_fund_holdings", "rows": 0, "batches": 0,
                "elapsed_s": 0.0, "rate_limit_hit": 0}

    # Quarter-end dates in [start, end]: Mar 31, Jun 30, Sep 30, Dec 31.
    quarter_ends: list[date] = []
    year = start_date.year
    while year <= end_date.year:
        for qd in (date(year, 3, 31), date(year, 6, 30),
                   date(year, 9, 30), date(year, 12, 31)):
            if start_date <= qd <= end_date:
                quarter_ends.append(qd)
        year += 1
    if not quarter_ends:
        log.warning("sync_fund_holdings: no quarter-ends in [%s, %s]", start_date, end_date)
        return {"topic": f"{source}_fund_holdings", "rows": 0, "batches": 0,
                "elapsed_s": 0.0, "rate_limit_hit": 0}

    total_rows = 0
    batches = 0
    last_date: date | None = None
    rate_limit_hit_before = getattr(src._rl, "rate_limit_hit", 0)

    for qe in quarter_ends:
        params = {"end_date": qe.strftime("%Y%m%d")}
        try:
            df = src.fetch("fund_holdings", **params)
        except Exception as e:
            log.exception("sync fund_holdings %s failed: %s", qe, e)
            if last_date is not None:
                meta.set_cursor(f"{source}_fund_holdings", last_date,
                                status="failed", error=str(e))
            else:
                meta.set_cursor(f"{source}_fund_holdings", qe,
                                status="failed", error=str(e))
            return {"topic": f"{source}_fund_holdings", "rows": total_rows,
                    "batches": batches, "last_date": str(last_date) if last_date else None,
                    "elapsed_s": round(time.monotonic() - t0, 2),
                    "rate_limit_hit": (getattr(src._rl, "rate_limit_hit", 0)
                                        - rate_limit_hit_before),
                    "ok": False, "error": str(e)}
        if df is None:
            df = pd.DataFrame()
        df = _df_to_parquet_columns(df, schema)
        if not df.empty:
            pq.write(df, partition_value=qe)
            duck.upsert(f"{source}_fund_holdings", df, schema.version,
                        primary_key=schema.primary_key)
            total_rows += len(df)
        meta.set_cursor(f"{source}_fund_holdings", qe, status="ok")
        batches += 1
        last_date = qe
        try:
            rec = src.lineage(
                table=f"{source}_fund_holdings", schema_version=schema.version,
                params=params, rows=len(df),
            )
            meta.write_lineage(rec)
        except Exception as e:  # pragma: no cover
            log.debug("lineage write failed (non-fatal): %s", e)

    rate_limit_hit = getattr(src._rl, "rate_limit_hit", 0) - rate_limit_hit_before
    return {
        "topic": f"{source}_fund_holdings",
        "rows": total_rows,
        "batches": batches,
        "last_date": last_date.isoformat() if last_date else None,
        "elapsed_s": round(time.monotonic() - t0, 2),
        "rate_limit_hit": rate_limit_hit,
    }


# ---------------------------------------------------------------------------
# A-tier Batch 1 — 基础 + 事件 (v0.9 — ADM-653)
# ---------------------------------------------------------------------------
def sync_index_classify(*, source: str = "tushare") -> dict[str, Any]:
    """index_classify: snapshot of the index tree. Single call, no date window."""
    t0 = time.monotonic()
    schema = get_schema("index_classify")
    src = get_source(source)
    duck, meta, pq = _open_store("index_classify", source=source)
    df = src.fetch("index_classify")
    df = _df_to_parquet_columns(df, schema)
    if not df.empty:
        pq.write(df, partition_value=None)
        duck.upsert(f"{source}_index_classify", df, schema.version)
    meta.set_cursor(f"{source}_index_classify", date.today(), status="ok")
    rec = src.lineage(table=f"{source}_index_classify", schema_version=schema.version,
                      params={}, rows=len(df))
    meta.write_lineage(rec)
    return {
        "topic": f"{source}_index_classify",
        "rows": len(df), "batches": 1, "last_date": date.today().isoformat(),
        "elapsed_s": round(time.monotonic() - t0, 2),
        "rate_limit_hit": getattr(src._rl, "rate_limit_hit", 0),
    }


def sync_index_daily(*, source: str = "tushare", start_date: date | None = None,
                     end_date: date | None = None,
                     index_pool: tuple[str, ...] | None = None) -> dict[str, Any]:
    """index_daily: per-(index, day). Unlike ``daily`` this API requires ts_code.

    We iterate over ``index_pool`` × trading days. Default pool is the
    canonical A-share market indices (沪深300 / 中证500 / 上证50 / 创业板指 / etc).
    Operators can override the pool via INDEX_POOL env var or the
    ``index_pool`` kwarg.
    """
    t0 = time.monotonic()
    schema = get_schema("index_daily")
    src = get_source(source)
    duck, meta, pq = _open_store("index_daily", source=source)

    # Default pool: top-15 A-share market indices. Full 359 from index_classify
    # would be 359 × days × 250 = ~360k req/year — too many for 2000 积分档.
    DEFAULT_INDEX_POOL: tuple[str, ...] = (
        "000001.SH",  # 上证指数
        "000300.SH",  # 沪深300
        "000905.SH",  # 中证500
        "000016.SH",  # 上证50
        "000852.SH",  # 中证1000
        "399001.SZ",  # 深证成指
        "399006.SZ",  # 创业板指
        "399905.SZ",  # 中证500(SZ)
        "000688.SH",  # 科创50
        "399016.SZ",  # 深证创新
        "000009.SH",  # 上证380
        "000010.SH",  # 上证180
        "399300.SZ",  # 沪深300(SZ)
        "000015.SH",  # 红利指数
        "000903.SH",  # 中证100
    )
    pool = index_pool if index_pool is not None else DEFAULT_INDEX_POOL

    cursor = meta.get_cursor(f"{source}_index_daily")
    if start_date is None:
        start_date = cursor + timedelta(days=1) if cursor else date(2005, 1, 1)
    if end_date is None:
        end_date = date.today()
    if cursor is not None:
        cursor_plus_one = cursor + timedelta(days=1)
        if cursor_plus_one > start_date:
            start_date = cursor_plus_one
    if start_date > end_date:
        return {"topic": f"{source}_index_daily", "rows": 0, "batches": 0,
                "elapsed_s": 0.0, "rate_limit_hit": 0}
    days = _all_trade_days(meta, start_date, end_date)
    if not days:
        return {"topic": f"{source}_index_daily", "rows": 0, "batches": 0,
                "elapsed_s": 0.0, "rate_limit_hit": 0}

    total_rows = 0
    batches = 0
    last_date: date | None = None
    rl_before = getattr(src._rl, "rate_limit_hit", 0)
    for idx_code in pool:
        for d in days:
            try:
                df = src.fetch("index_daily", ts_code=idx_code,
                               trade_date=d.strftime("%Y%m%d"))
            except Exception as e:
                log.exception("sync index_daily %s %s failed: %s", idx_code, d, e)
                if last_date is not None:
                    meta.set_cursor(f"{source}_index_daily", last_date, status="failed", error=str(e))
                return {"topic": f"{source}_index_daily", "rows": total_rows,
                        "batches": batches, "last_date": str(last_date) if last_date else None,
                        "elapsed_s": round(time.monotonic() - t0, 2),
                        "rate_limit_hit": getattr(src._rl, "rate_limit_hit", 0) - rl_before,
                        "ok": False, "error": str(e)}
            df = _df_to_parquet_columns(df if df is not None else pd.DataFrame(), schema)
            if not df.empty:
                pq.write(df, partition_value=d)
                duck.upsert(f"{source}_index_daily", df, schema.version,
                            primary_key=schema.primary_key)
                total_rows += len(df)
            meta.set_cursor(f"{source}_index_daily", d, status="ok")
            batches += 1
            last_date = d
    return {
        "topic": f"{source}_index_daily", "rows": total_rows, "batches": batches,
        "last_date": last_date.isoformat() if last_date else None,
        "elapsed_s": round(time.monotonic() - t0, 2),
        "rate_limit_hit": getattr(src._rl, "rate_limit_hit", 0) - rl_before,
    }


def sync_index_member(*, source: str = "tushare", start_date: date | None = None,
                      end_date: date | None = None,
                      index_pool: tuple[str, ...] | None = None) -> dict[str, Any]:
    """index_member: per-(index, day). 200 积分/次; needs explicit index_code.

    The API requires an index_code param; we iterate over index_pool × days.
    Operators can override the pool via the INDEX_POOL env var.
    """
    t0 = time.monotonic()
    schema = get_schema("index_member")
    src = get_source(source)
    duck, meta, pq = _open_store("index_member", source=source)

    pool = index_pool if index_pool is not None else _load_index_pool()
    cursor = meta.get_cursor(f"{source}_index_member")
    if start_date is None:
        start_date = cursor + timedelta(days=1) if cursor else date(2005, 1, 1)
    if end_date is None:
        end_date = date.today()
    if cursor is not None:
        cursor_plus_one = cursor + timedelta(days=1)
        if cursor_plus_one > start_date:
            start_date = cursor_plus_one
    if start_date > end_date:
        return {"topic": f"{source}_index_member", "rows": 0, "batches": 0,
                "elapsed_s": 0.0, "rate_limit_hit": 0}
    days = _all_trade_days(meta, start_date, end_date)
    if not days:
        return {"topic": f"{source}_index_member", "rows": 0, "batches": 0,
                "elapsed_s": 0.0, "rate_limit_hit": 0}

    total_rows = 0
    batches = 0
    last_date: date | None = None
    rl_before = getattr(src._rl, "rate_limit_hit", 0)
    for idx_code in pool:
        for d in days:
            try:
                # tushare upstream uses ``idx_code`` (not ``index_code``).
                df = src.fetch("index_member", idx_code=idx_code,
                               trade_date=d.strftime("%Y%m%d"))
            except Exception as e:
                log.exception("sync index_member %s %s failed: %s", idx_code, d, e)
                if last_date is not None:
                    meta.set_cursor(f"{source}_index_member", last_date, status="failed", error=str(e))
                return {"topic": f"{source}_index_member", "rows": total_rows,
                        "batches": batches, "last_date": str(last_date) if last_date else None,
                        "elapsed_s": round(time.monotonic() - t0, 2),
                        "rate_limit_hit": getattr(src._rl, "rate_limit_hit", 0) - rl_before,
                        "ok": False, "error": str(e)}
            df = _df_to_parquet_columns(df if df is not None else pd.DataFrame(), schema)
            if not df.empty:
                pq.write(df, partition_value=d)
                duck.upsert(f"{source}_index_member", df, schema.version,
                            primary_key=schema.primary_key)
                total_rows += len(df)
            meta.set_cursor(f"{source}_index_member", d, status="ok")
            batches += 1
            last_date = d
    return {
        "topic": f"{source}_index_member", "rows": total_rows, "batches": batches,
        "last_date": last_date.isoformat() if last_date else None,
        "elapsed_s": round(time.monotonic() - t0, 2),
        "rate_limit_hit": getattr(src._rl, "rate_limit_hit", 0) - rl_before,
    }


def sync_sw_index(*, source: str = "tushare", start_date: date | None = None,
                  end_date: date | None = None,
                  level: int = 1) -> dict[str, Any]:
    """sw_index: 申万行业指数日线(level=1/2/3). 500 积分/次."""
    t0 = time.monotonic()
    schema = get_schema("sw_index")
    src = get_source(source)
    duck, meta, pq = _open_store("sw_index", source=source)
    cursor = meta.get_cursor(f"{source}_sw_index")
    if start_date is None:
        start_date = cursor + timedelta(days=1) if cursor else date(2000, 1, 1)
    if end_date is None:
        end_date = date.today()
    if cursor is not None:
        cursor_plus_one = cursor + timedelta(days=1)
        if cursor_plus_one > start_date:
            start_date = cursor_plus_one
    if start_date > end_date:
        return {"topic": f"{source}_sw_index", "rows": 0, "batches": 0,
                "elapsed_s": 0.0, "rate_limit_hit": 0}
    days = _all_trade_days(meta, start_date, end_date)
    if not days:
        return {"topic": f"{source}_sw_index", "rows": 0, "batches": 0,
                "elapsed_s": 0.0, "rate_limit_hit": 0}

    total_rows = 0
    batches = 0
    last_date: date | None = None
    rl_before = getattr(src._rl, "rate_limit_hit", 0)
    for d in days:
        try:
            df = src.fetch("sw_index", trade_date=d.strftime("%Y%m%d"), level=level)
        except RuntimeError as e:
            # Tier-blocked (sw_index needs ≥5000 积分档) — record and return
            # gracefully so the batch runner can mark this topic as blocked
            # rather than failed.
            if "tier-blocked" in str(e):
                log.warning("sync sw_index %s tier-blocked on %s 积分档 — skipping",
                            d, src._tier)
                return {"topic": f"{source}_sw_index", "rows": 0, "batches": 0,
                        "last_date": None, "elapsed_s": round(time.monotonic() - t0, 2),
                        "rate_limit_hit": getattr(src._rl, "rate_limit_hit", 0) - rl_before,
                        "ok": False, "blocked": True, "tier_blocked": True,
                        "error": f"tier-blocked (need ≥5000 积分档, on {src._tier})"}
            log.exception("sync sw_index %s level=%s failed: %s", d, level, e)
            if last_date is not None:
                meta.set_cursor(f"{source}_sw_index", last_date, status="failed", error=str(e))
            return {"topic": f"{source}_sw_index", "rows": total_rows,
                    "batches": batches, "last_date": str(last_date) if last_date else None,
                    "elapsed_s": round(time.monotonic() - t0, 2),
                    "rate_limit_hit": getattr(src._rl, "rate_limit_hit", 0) - rl_before,
                    "ok": False, "error": str(e)}
        df = _df_to_parquet_columns(df if df is not None else pd.DataFrame(), schema)
        if not df.empty:
            pq.write(df, partition_value=d)
            duck.upsert(f"{source}_sw_index", df, schema.version,
                        primary_key=schema.primary_key)
            total_rows += len(df)
        meta.set_cursor(f"{source}_sw_index", d, status="ok")
        batches += 1
        last_date = d
    return {
        "topic": f"{source}_sw_index", "rows": total_rows, "batches": batches,
        "last_date": last_date.isoformat() if last_date else None,
        "elapsed_s": round(time.monotonic() - t0, 2),
        "rate_limit_hit": getattr(src._rl, "rate_limit_hit", 0) - rl_before,
    }


def sync_stk_limit(*, source: str = "tushare", start_date: date | None = None,
                   end_date: date | None = None) -> dict[str, Any]:
    """stk_limit: per-day full-market limit prices (1 req = whole market)."""
    return sync_table("stk_limit", source=source, start_date=start_date, end_date=end_date)


def sync_suspend(*, source: str = "tushare", start_date: date | None = None,
                 end_date: date | None = None) -> dict[str, Any]:
    """suspend: per-day suspend/resume events. Sparse; may return 0 rows many days."""
    return sync_table("suspend", source=source, start_date=start_date, end_date=end_date,
                      partition_key="suspend_date")


def sync_dividend(*, source: str = "tushare", start_date: date | None = None,
                  end_date: date | None = None) -> dict[str, Any]:
    """dividend: per-ann_date event. Partitioned by ann_date."""
    return sync_table("dividend", source=source, start_date=start_date, end_date=end_date,
                      partition_key="ann_date")


def sync_shares_float(*, source: str = "tushare", start_date: date | None = None,
                      end_date: date | None = None) -> dict[str, Any]:
    """shares_float: per-float_date unlock events."""
    return sync_table("shares_float", source=source, start_date=start_date, end_date=end_date,
                      partition_key="float_date")


# ---------------------------------------------------------------------------
# A-tier Batch 2 — 财务三联表 + 财务指标 (v0.9 — ADM-653)
# ---------------------------------------------------------------------------
def _sync_financial_quarterly(
    topic: str,
    *,
    source: str = "tushare",
    start_date: date | None = None,
    end_date: date | None = None,
    ann_date_floor: tuple[int, int, int] = (2010, 1, 1),
    ts_codes: tuple[str, ...] | None = None,
) -> dict[str, Any]:
    """Shared driver for the financial three-statement family.

    Iterates over ``ts_code`` × quarter-end dates in [start, end]. The cursor
    tracks the last ``end_date`` synced (so a re-run resumes, doesn't repeat).

    tushare's ``income`` / ``balancesheet`` / ``cashflow`` / ``fina_indicator``
    / ``fina_mainbz`` / ``fina_audit`` all require ``ts_code`` (or list of
    codes) and accept ``period`` (quarter-end date as YYYYMMDD).

    For ``top10_floatholders`` / ``top10_holders`` the period-only path works
    (no ts_code needed) — see the standalone ``_sync_top10_floatholders``
    helper below.

    Universe source: by default we pull from ``stock_basic`` once and cache.
    On the 2000 积分档 ceiling (200 req/min, 100k req/day), a full 5,400
    ts_codes × 4 quarter-ends = 21.6k req. We do best-effort and rely on
    the cursor + retry-on-resume to backfill the rest.
    """
    t0 = time.monotonic()
    schema = get_schema(topic)
    src = get_source(source)
    duck, meta, pq = _open_store(topic, source=source)

    cursor = meta.get_cursor(f"{source}_{topic}")
    floor_date = date(*ann_date_floor)
    if start_date is None:
        start_date = cursor + timedelta(days=1) if cursor else floor_date
    if start_date < floor_date:
        start_date = floor_date
    if end_date is None:
        end_date = date.today()
    if cursor is not None:
        cursor_plus_one = cursor + timedelta(days=1)
        if cursor_plus_one > start_date:
            start_date = cursor_plus_one
    if start_date > end_date:
        return {"topic": f"{source}_{topic}", "rows": 0, "batches": 0,
                "elapsed_s": 0.0, "rate_limit_hit": 0}

    quarter_ends: list[date] = []
    year = start_date.year
    while year <= end_date.year:
        for qd in (date(year, 3, 31), date(year, 6, 30),
                   date(year, 9, 30), date(year, 12, 31)):
            if start_date <= qd <= end_date:
                quarter_ends.append(qd)
        year += 1
    if not quarter_ends:
        return {"topic": f"{source}_{topic}", "rows": 0, "batches": 0,
                "elapsed_s": 0.0, "rate_limit_hit": 0}

    # Resolve universe: pull stock_basic once if not provided.
    if ts_codes is None:
        try:
            sb = src.fetch("stock_basic", list_status="L",
                           fields="ts_code,list_date,delist_date")
            # Dedupe — defensive against mock sources that may return the
            # same code multiple times.
            ts_codes = tuple(sorted(set(sb["ts_code"].tolist())))
            log.info("sync %s: pulled %d ts_codes from stock_basic", topic, len(ts_codes))
        except Exception as e:
            log.exception("sync %s: failed to load stock_basic universe: %s", topic, e)
            return {"topic": f"{source}_{topic}", "rows": 0, "batches": 0,
                    "elapsed_s": round(time.monotonic() - t0, 2),
                    "rate_limit_hit": 0, "ok": False,
                    "error": f"failed to load stock_basic universe: {e}"}

    total_rows = 0
    batches = 0
    last_qe: date | None = None
    rl_before = getattr(src._rl, "rate_limit_hit", 0)
    # Iterate (ts_code × quarter-end). On error, return partial state so the
    # caller can resume from the cursor.
    for qe in quarter_ends:
        qe_rows = 0
        qe_batches = 0
        for code in ts_codes:
            try:
                df = src.fetch(topic, ts_code=code, period=qe.strftime("%Y%m%d"))
            except Exception as e:
                log.exception("sync %s %s period=%s failed: %s", topic, code, qe, e)
                if last_qe is not None:
                    meta.set_cursor(f"{source}_{topic}", last_qe, status="partial",
                                    error=str(e))
                return {"topic": f"{source}_{topic}", "rows": total_rows,
                        "batches": batches, "last_date": str(last_qe) if last_qe else None,
                        "elapsed_s": round(time.monotonic() - t0, 2),
                        "rate_limit_hit": getattr(src._rl, "rate_limit_hit", 0) - rl_before,
                        "ok": False, "error": str(e),
                        "stopped_at": f"{code}/{qe}"}
            df = _df_to_parquet_columns(df if df is not None else pd.DataFrame(), schema)
            if not df.empty:
                pq.write(df, partition_value=qe)
                duck.upsert(f"{source}_{topic}", df, schema.version,
                            primary_key=schema.primary_key)
                qe_rows += len(df)
                total_rows += len(df)
            batches += 1
            qe_batches += 1
        meta.set_cursor(f"{source}_{topic}", qe, status="ok")
        last_qe = qe
    return {
        "topic": f"{source}_{topic}", "rows": total_rows, "batches": batches,
        "last_date": last_qe.isoformat() if last_qe else None,
        "elapsed_s": round(time.monotonic() - t0, 2),
        "rate_limit_hit": getattr(src._rl, "rate_limit_hit", 0) - rl_before,
    }


def _sync_top10_floatholders(
    *,
    source: str = "tushare",
    start_date: date | None = None,
    end_date: date | None = None,
    ann_date_floor: tuple[int, int, int] = (2010, 1, 1),
) -> dict[str, Any]:
    """top10_floatholders: period-only (no ts_code needed). Uses quarter-end iteration."""
    return _sync_financial_quarterly(
        "top10_floatholders", source=source,
        start_date=start_date, end_date=end_date,
        ann_date_floor=ann_date_floor,
    )


def sync_fina_indicator(*, source: str = "tushare", start_date: date | None = None,
                        end_date: date | None = None) -> dict[str, Any]:
    """fina_indicator: 80 字段/季. ROE/ROA/ROIC 经典因子."""
    return _sync_financial_quarterly("fina_indicator", source=source,
                                     start_date=start_date, end_date=end_date)


def sync_income(*, source: str = "tushare", start_date: date | None = None,
                end_date: date | None = None) -> dict[str, Any]:
    """income: 利润表 85 字段/季."""
    return _sync_financial_quarterly("income", source=source,
                                     start_date=start_date, end_date=end_date)


def sync_balancesheet(*, source: str = "tushare", start_date: date | None = None,
                      end_date: date | None = None) -> dict[str, Any]:
    """balancesheet: 资产负债表 150 字段/季."""
    return _sync_financial_quarterly("balancesheet", source=source,
                                     start_date=start_date, end_date=end_date)


def sync_cashflow(*, source: str = "tushare", start_date: date | None = None,
                  end_date: date | None = None) -> dict[str, Any]:
    """cashflow: 现金流量表 95 字段/季. Sloan 因子输入."""
    return _sync_financial_quarterly("cashflow", source=source,
                                     start_date=start_date, end_date=end_date)


def sync_fina_mainbz(*, source: str = "tushare", start_date: date | None = None,
                     end_date: date | None = None) -> dict[str, Any]:
    """fina_mainbz: 主营业务构成;按 period 拉取 (业务结构/产品/区域)."""
    return _sync_financial_quarterly("fina_mainbz", source=source,
                                     start_date=start_date, end_date=end_date)


def sync_fina_audit(*, source: str = "tushare", start_date: date | None = None,
                    end_date: date | None = None) -> dict[str, Any]:
    """fina_audit: 审计意见(年报). PK (ts_code, end_date)."""
    return _sync_financial_quarterly("fina_audit", source=source,
                                     start_date=start_date, end_date=end_date,
                                     ann_date_floor=(2000, 1, 1))


def sync_top10_holders(*, source: str = "tushare", start_date: date | None = None,
                       end_date: date | None = None) -> dict[str, Any]:
    """top10_holders: 前十大股东(总股本口径). 季报期."""
    return _sync_financial_quarterly("top10_holders", source=source,
                                     start_date=start_date, end_date=end_date)


# ---------------------------------------------------------------------------
# A-tier Batch 3 — 资金流 + 研报 + 股东 (v0.9 — ADM-653)
# ---------------------------------------------------------------------------
def sync_top_list(*, source: str = "tushare", start_date: date | None = None,
                  end_date: date | None = None) -> dict[str, Any]:
    """top_list: 龙虎榜日榜. PK (trade_date, ts_code)."""
    return sync_table("top_list", source=source, start_date=start_date, end_date=end_date)


def sync_margin_detail(*, source: str = "tushare", start_date: date | None = None,
                       end_date: date | None = None) -> dict[str, Any]:
    """margin_detail: 融资融券明细. 日频 × 5千只 × 5年 = ~6M 行落盘.

    大数据量接口 — token bucket 严守 150 req/min 留 25% 余量。
    """
    return sync_table("margin_detail", source=source, start_date=start_date, end_date=end_date)


def sync_top10_floatholders(*, source: str = "tushare", start_date: date | None = None,
                            end_date: date | None = None) -> dict[str, Any]:
    """top10_floatholders: 前十大流通股东 (period-only, no ts_code needed)."""
    return _sync_top10_floatholders(source=source, start_date=start_date, end_date=end_date)


def sync_stk_holdertrade(*, source: str = "tushare", start_date: date | None = None,
                         end_date: date | None = None) -> dict[str, Any]:
    """stk_holdertrade: 股东增减持. PK (ts_code, ann_date, holder_name, trade_date).

    Per-ann_date iteration; tushare returns all holders reporting on a given day.
    """
    return sync_table("stk_holdertrade", source=source, start_date=start_date, end_date=end_date,
                      partition_key="ann_date")


def sync_report_rc(*, source: str = "tushare", start_date: date | None = None,
                   end_date: date | None = None) -> dict[str, Any]:
    """report_rc: 研报内容. PK (ts_code, report_date, org_name, author_name).

    Per-report_date iteration.落盘前需清洗 HTML/长文本字段 — view 层只保留结构化字段。
    """
    return sync_table("report_rc", source=source, start_date=start_date, end_date=end_date,
                      partition_key="report_date")


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

    # S-tier additions (v0.8 — ADM-652): same 2010-01-01 horizon for daily
    # topics, and the per-topic floor is set by ``sync_moneyflow`` etc. (which
    # use the schema-driven defaults if the cursor is empty).
    for fn in (sync_moneyflow, sync_moneyflow_hsgt, sync_hsgt_top10,
               sync_index_weight, sync_fund_holdings):
        try:
            reports.append(fn(source=source, start_date=horizon_start, end_date=today))
        except Exception as e:  # noqa: BLE001
            log.exception("sync_full %s failed: %s", fn.__name__, e)
            reports.append({"topic": fn.__name__, "ok": False, "error": str(e)})

    # A-tier additions (v0.9 — ADM-653): 20 new sync functions, grouped by batch.
    # Each batch is wrapped in its own try/except so a batch failure does not
    # block the others.
    for batch_name, batch_fns in (
        ("batch1_basic_events", (
            sync_index_classify, sync_index_daily, sync_index_member, sync_sw_index,
            sync_stk_limit, sync_suspend, sync_dividend, sync_shares_float,
        )),
        ("batch2_financial", (
            sync_fina_indicator, sync_income, sync_balancesheet, sync_cashflow,
            sync_fina_mainbz, sync_fina_audit, sync_top10_holders,
        )),
        ("batch3_capital_research_holders", (
            sync_top_list, sync_margin_detail, sync_top10_floatholders,
            sync_stk_holdertrade, sync_report_rc,
        )),
    ):
        for fn in batch_fns:
            try:
                # Snapshot-style syncers accept no date window.
                if fn is sync_index_classify:
                    reports.append(fn(source=source))
                else:
                    reports.append(fn(source=source, start_date=horizon_start, end_date=today))
            except Exception as e:  # noqa: BLE001
                log.exception("sync_full %s/%s failed: %s", batch_name, fn.__name__, e)
                reports.append({"topic": fn.__name__, "ok": False, "error": str(e)})

    log.info("== sync_full done: %s ==", reports)
    return reports
