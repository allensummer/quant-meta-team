"""Supporting helpers for the ``python -m quant_data.cli`` command family.

This module keeps the CLI thin: every diagnostic / list / doctor command is
implemented as a function that returns a plain dict, and the CLI wrapper
turns that dict into either JSON or human-readable output.

Exit codes (declared in the CLI module, but the constants live here so the
helpers can include them in payloads):

  0  ok
  1  generic error
  2  partial sync failure
  3  blocked (DATA_DIR missing, token invalid, view missing)
  4  rate-limit hit repeatedly
  5  data quality gate failed (diff / doctor checks)
"""
from __future__ import annotations

import hashlib
import logging
import os
import shutil
import sqlite3
from datetime import date, datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

from quant_data.paths import data_dir, duckdb_path, sqlite_path

log = logging.getLogger("quant_data.cli_support")


CLI_VERSION = "0.9.0"

# Canonical 30 raw + 30 view names. Kept in sync with ``bootstrap_views``.
# (v0.8 — ADM-652 added 5 S-tier topics; v0.9 — ADM-653 adds 20 A-tier topics.)
RAW_TABLES: tuple[str, ...] = (
    "raw_tushare_stock_basic",
    "raw_tushare_trade_cal",
    "raw_tushare_daily",
    "raw_tushare_adj_factor",
    "raw_tushare_daily_basic",
    "raw_tushare_moneyflow",
    "raw_tushare_moneyflow_hsgt",
    "raw_tushare_index_weight",
    "raw_tushare_hsgt_top10",
    "raw_tushare_fund_holdings",
    # A-tier Batch 1 — 基础 + 事件 (ADM-653)
    "raw_tushare_index_classify",
    "raw_tushare_index_daily",
    "raw_tushare_index_member",
    "raw_tushare_sw_index",
    "raw_tushare_stk_limit",
    "raw_tushare_suspend",
    "raw_tushare_dividend",
    "raw_tushare_shares_float",
    # A-tier Batch 2 — 财务三联表 + 财务指标
    "raw_tushare_fina_indicator",
    "raw_tushare_income",
    "raw_tushare_balancesheet",
    "raw_tushare_cashflow",
    "raw_tushare_fina_mainbz",
    "raw_tushare_fina_audit",
    "raw_tushare_top10_holders",
    # A-tier Batch 3 — 资金流 + 研报 + 股东
    "raw_tushare_top_list",
    "raw_tushare_margin_detail",
    "raw_tushare_top10_floatholders",
    "raw_tushare_stk_holdertrade",
    "raw_tushare_report_rc",
)
MV_VIEWS: tuple[str, ...] = (
    "mv_daily_v1",
    "mv_daily_qfq",
    "mv_daily_hfq",
    "mv_trade_cal",
    "mv_daily_basic",
    "mv_moneyflow_v1",
    "mv_moneyflow_hsgt_v1",
    "mv_index_weight_v1",
    "mv_hsgt_top10_v1",
    "mv_fund_holdings_v1",
    # A-tier Batch 1
    "mv_index_classify_v1",
    "mv_index_daily_v1",
    "mv_index_member_v1",
    "mv_sw_index_v1",
    "mv_stk_limit_v1",
    "mv_suspend_v1",
    "mv_dividend_v1",
    "mv_shares_float_v1",
    # A-tier Batch 2
    "mv_fina_indicator_v1",
    "mv_income_v1",
    "mv_balancesheet_v1",
    "mv_cashflow_v1",
    "mv_fina_mainbz_v1",
    "mv_fina_audit_v1",
    "mv_top10_holders_v1",
    # A-tier Batch 3
    "mv_top_list_v1",
    "mv_margin_detail_v1",
    "mv_top10_floatholders_v1",
    "mv_stk_holdertrade_v1",
    "mv_report_rc_v1",
)

# Map topic -> (raw table name, date column to use for min/max).
# fund_holdings uses end_date (季报期) not trade_date.
# A-tier date columns are selected per the natural query grain.
TOPIC_META: dict[str, dict[str, str]] = {
    "stock_basic":     {"raw": "raw_tushare_stock_basic",     "date_col": "list_date"},
    "trade_cal":       {"raw": "raw_tushare_trade_cal",       "date_col": "cal_date"},
    "daily":           {"raw": "raw_tushare_daily",           "date_col": "trade_date"},
    "adj_factor":      {"raw": "raw_tushare_adj_factor",      "date_col": "trade_date"},
    "daily_basic":     {"raw": "raw_tushare_daily_basic",     "date_col": "trade_date"},
    "moneyflow":       {"raw": "raw_tushare_moneyflow",       "date_col": "trade_date"},
    "moneyflow_hsgt":  {"raw": "raw_tushare_moneyflow_hsgt",  "date_col": "trade_date"},
    "index_weight":    {"raw": "raw_tushare_index_weight",    "date_col": "trade_date"},
    "hsgt_top10":      {"raw": "raw_tushare_hsgt_top10",      "date_col": "trade_date"},
    "fund_holdings":   {"raw": "raw_tushare_fund_holdings",   "date_col": "end_date"},
    # A-tier Batch 1
    "index_classify":  {"raw": "raw_tushare_index_classify",  "date_col": "list_date"},
    "index_daily":     {"raw": "raw_tushare_index_daily",     "date_col": "trade_date"},
    "index_member":    {"raw": "raw_tushare_index_member",    "date_col": "in_date"},
    "sw_index":        {"raw": "raw_tushare_sw_index",        "date_col": "trade_date"},
    "stk_limit":       {"raw": "raw_tushare_stk_limit",       "date_col": "trade_date"},
    "suspend":         {"raw": "raw_tushare_suspend",         "date_col": "suspend_date"},
    "dividend":        {"raw": "raw_tushare_dividend",        "date_col": "ann_date"},
    "shares_float":    {"raw": "raw_tushare_shares_float",    "date_col": "float_date"},
    # A-tier Batch 2 — 财务三联表 + 财务指标（季频，按 ann_date 滚动）
    "fina_indicator":  {"raw": "raw_tushare_fina_indicator",  "date_col": "ann_date"},
    "income":          {"raw": "raw_tushare_income",          "date_col": "ann_date"},
    "balancesheet":    {"raw": "raw_tushare_balancesheet",    "date_col": "ann_date"},
    "cashflow":        {"raw": "raw_tushare_cashflow",        "date_col": "ann_date"},
    "fina_mainbz":     {"raw": "raw_tushare_fina_mainbz",     "date_col": "end_date"},
    "fina_audit":      {"raw": "raw_tushare_fina_audit",      "date_col": "ann_date"},
    "top10_holders":   {"raw": "raw_tushare_top10_holders",   "date_col": "ann_date"},
    # A-tier Batch 3
    "top_list":        {"raw": "raw_tushare_top_list",        "date_col": "trade_date"},
    "margin_detail":   {"raw": "raw_tushare_margin_detail",   "date_col": "trade_date"},
    "top10_floatholders": {"raw": "raw_tushare_top10_floatholders", "date_col": "ann_date"},
    "stk_holdertrade": {"raw": "raw_tushare_stk_holdertrade", "date_col": "ann_date"},
    "report_rc":       {"raw": "raw_tushare_report_rc",       "date_col": "report_date"},
}

# Tokens blocked in the ``query`` subcommand. We keep this conservative
# (covers everything in the DoD list: DROP/DELETE/UPDATE/INSERT/CREATE/ALTER
# plus a few other write-side verbs) and rely on a separate scope check
# (FROM/JOIN must be mv_*/raw_*) to keep things narrow.
_FORBIDDEN_DDL_DML = (
    "DROP", "DELETE", "UPDATE", "INSERT",
    "CREATE", "ALTER", "TRUNCATE", "REPLACE", "ATTACH", "DETACH",
    "COPY", "EXPORT", "IMPORT", "CALL", "LOAD", "INSTALL",
    "GRANT", "REVOKE",
)


# ---------------------------------------------------------------------------
# Envelope: every --json command returns this shape.
# ---------------------------------------------------------------------------
def envelope(
    ok: bool,
    data: Any = None,
    *,
    command: str,
    error: str | None = None,
    exit_code: int = 0,
) -> dict[str, Any]:
    """Wrap a payload with the versioned CLI envelope.

    Schema (v0.8.0):
        cli_version: str     # "0.8.0" — bumped whenever envelope shape changes
        command:     str     # subcommand that produced the output
        ok:          bool    # top-level success indicator
        exit_code:   int     # same value the process will exit with
        error:       str|None
        data:        object  # the actual payload
        ts:          str     # ISO8601 timestamp at envelope build time
    """
    return {
        "cli_version": CLI_VERSION,
        "command": command,
        "ok": ok,
        "exit_code": exit_code,
        "error": error,
        "data": data,
        "ts": datetime.now().astimezone().isoformat(timespec="seconds"),
    }


# ---------------------------------------------------------------------------
# list-tables / list-views / list-sources
# ---------------------------------------------------------------------------
def list_tables() -> list[dict[str, Any]]:
    """Return row count / min-max date / cursor for each raw_* table.

    All values are best-effort: a missing or empty table reports zeros rather
    than raising — the CLI is for inspection, not enforcement.
    """
    from quant_data.store.duckdb_store import DuckDBStore
    from quant_data.store.meta_sqlite import MetaSQLite

    db = DuckDBStore()
    meta = MetaSQLite()
    cursors = meta.all_cursors()
    out: list[dict[str, Any]] = []
    for topic, meta_ in TOPIC_META.items():
        raw = meta_["raw"]
        col = meta_["date_col"]
        try:
            row = db.con.execute(f"SELECT count(*) FROM {raw}").fetchone()
            count = int(row[0]) if row else 0
        except Exception as e:  # noqa: BLE001
            log.debug("count %s failed: %s", raw, e)
            count = 0
        if count:
            try:
                lo, hi = db.con.execute(
                    f"SELECT min({col}), max({col}) FROM {raw}"
                ).fetchone()
                lo_s = lo.isoformat() if lo else None
                hi_s = hi.isoformat() if hi else None
            except Exception:  # noqa: BLE001
                lo_s, hi_s = None, None
        else:
            lo_s, hi_s = None, None
        cur = cursors.get(raw, {})
        out.append({
            "raw": raw,
            "rows": count,
            "min_date": lo_s,
            "max_date": hi_s,
            "cursor_last_trade_date": cur.get("last_trade_date"),
            "cursor_status": cur.get("status"),
            "cursor_last_run_at": cur.get("last_run_at"),
        })
    return out


def list_views() -> list[dict[str, Any]]:
    """Return row count + sample row count for each mv_* view."""
    from quant_data.store.duckdb_store import DuckDBStore

    db = DuckDBStore()
    out: list[dict[str, Any]] = []
    for v in MV_VIEWS:
        try:
            row = db.con.execute(f"SELECT count(*) FROM {v}").fetchone()
            count = int(row[0]) if row else 0
            err = None
        except Exception as e:  # noqa: BLE001
            count = 0
            err = str(e)
        out.append({
            "view": v,
            "rows": count,
            "error": err,
        })
    return out


def list_sources() -> list[dict[str, Any]]:
    """Enumerate registered data sources (tushare eager, akshare lazy)."""
    from quant_data import registry
    from quant_data.sources.akshare import AkshareAdapter

    out: list[dict[str, Any]] = []
    for name, adapter in registry.SOURCES.items():
        caps: set[str] = set()
        rl_s = ""
        try:
            caps = set(getattr(adapter, "capabilities", set()))
        except Exception:  # noqa: BLE001
            pass
        try:
            rl = adapter.rate_limit()
            rl_s = f"{rl.requests_per_min} req/min"
        except Exception:  # noqa: BLE001
            rl_s = "?"
        out.append({
            "name": name,
            "version": getattr(adapter, "version", "?"),
            "capabilities": sorted(caps),
            "rate_limit": rl_s,
        })
    # akshare is lazy; surface it explicitly so the operator can see the
    # intended set of sources, not just the eagerly-built ones.
    if not any(s["name"] == "akshare" for s in out):
        out.append({
            "name": "akshare",
            "version": AkshareAdapter().version,
            "capabilities": sorted(AkshareAdapter().capabilities),
            "rate_limit": f"{AkshareAdapter().rate_limit().requests_per_min} req/min",
            "note": "lazy: instantiated on first call",
        })
    return out


# ---------------------------------------------------------------------------
# status / doctor / diff
# ---------------------------------------------------------------------------
def _disk_free_gb(path: Path) -> float:
    try:
        usage = shutil.disk_usage(str(path))
        return round(usage.free / (1024 ** 3), 2)
    except Exception:  # noqa: BLE001
        return 0.0


def _cumulative_rate_limit_hit() -> int:
    """Sum rate_limit_hit across all built adapters (best-effort)."""
    from quant_data import registry
    total = 0
    for adapter in registry.SOURCES.values():
        rl = getattr(adapter, "_rl", None)
        if rl is not None and hasattr(rl, "rate_limit_hit"):
            total += int(rl.rate_limit_hit)
    return total


def status() -> dict[str, Any]:
    """Lightweight health snapshot (no network)."""
    from quant_data.store.duckdb_store import DuckDBStore
    from quant_data.store.meta_sqlite import MetaSQLite

    dd = data_dir()
    dd_exists = dd.exists()
    db = DuckDBStore()
    meta = MetaSQLite()
    cursors = meta.all_cursors()
    cursor_health = {
        name: cur.get("status") for name, cur in cursors.items()
    }
    return {
        "data_dir": str(dd),
        "data_dir_exists": dd_exists,
        "duckdb_path": str(duckdb_path()),
        "duckdb_exists": duckdb_path().exists(),
        "disk_free_gb": _disk_free_gb(dd) if dd_exists else 0.0,
        "cursors": cursors,
        "cursor_health": cursor_health,
        "rate_limit_hit_total": _cumulative_rate_limit_hit(),
    }


def doctor() -> tuple[dict[str, Any], int]:
    """Run a battery of self-checks; return (report, exit_code).

    exit_code:
      0  every check passed
      3  blocked (token missing / DATA_DIR missing / views missing)
      5  data quality gate failed (cursors not all ok / disk < 5GB free)
    """
    from quant_data.store.duckdb_store import DuckDBStore

    checks: list[dict[str, Any]] = []
    recommendations: list[str] = []
    exit_code = 0

    # 1. DATA_DIR
    dd = data_dir()
    if not dd.exists():
        checks.append({"name": "data_dir", "ok": False, "detail": f"{dd} missing"})
        recommendations.append(f"create DATA_DIR or set DATA_DIR to a writable path")
        exit_code = max(exit_code, 3)
    else:
        checks.append({"name": "data_dir", "ok": True, "detail": str(dd)})

    # 2. disk free
    free_gb = _disk_free_gb(dd) if dd.exists() else 0.0
    disk_ok = free_gb >= 5.0  # 5 GB is the soft red line for the 20y backfill
    checks.append({
        "name": "disk_free", "ok": disk_ok, "detail": f"{free_gb} GB free at {dd}"
    })
    if not disk_ok:
        recommendations.append("free up disk space (<5 GB) before large syncs")
        exit_code = max(exit_code, 5)

    # 3. TUSHARE_TOKEN
    token = os.getenv("TUSHARE_TOKEN", "").strip()
    token_ok = bool(token)
    checks.append({
        "name": "tushare_token", "ok": token_ok,
        "detail": "set" if token_ok else "TUSHARE_TOKEN env not set",
    })
    if not token_ok:
        recommendations.append("export TUSHARE_TOKEN in your env (2000 积分档)")
        exit_code = max(exit_code, 3)

    # 4. views
    db = DuckDBStore()
    try:
        rows = db.con.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_type='VIEW'"
        ).fetchall()
        view_names = {r[0] for r in rows}
    except Exception as e:  # noqa: BLE001
        view_names = set()
        checks.append({"name": "views", "ok": False, "detail": f"duckdb error: {e}"})
        exit_code = max(exit_code, 3)
    else:
        expected = set(MV_VIEWS)
        missing = expected - view_names
        ok = not missing
        checks.append({
            "name": "views", "ok": ok,
            "detail": f"present={len(view_names & expected)}/{len(expected)} missing={sorted(missing)}",
        })
        if not ok:
            recommendations.append("run `python -m quant_data.cli init` to bootstrap views")
            exit_code = max(exit_code, 3)

    # 5. cursors
    from quant_data.store.meta_sqlite import MetaSQLite
    meta = MetaSQLite()
    cursors = meta.all_cursors()
    bad = [n for n, c in cursors.items() if c.get("status") not in ("ok", None)]
    cur_ok = (len(cursors) == len(RAW_TABLES)) and not bad
    checks.append({
        "name": "cursors", "ok": cur_ok,
        "detail": f"present={len(cursors)}/{len(RAW_TABLES)} bad={bad}",
    })
    if not cur_ok:
        if len(cursors) != len(RAW_TABLES):
            recommendations.append("run `python -m quant_data.cli init` then `sync-table` to seed cursors")
        if bad:
            recommendations.append(f"investigate failed cursors: {bad}")
        exit_code = max(exit_code, 5)

    # 6. rate limit cumulative
    rl_hit = _cumulative_rate_limit_hit()
    checks.append({
        "name": "rate_limit", "ok": True,
        "detail": f"cumulative hits={rl_hit} (advisory only)",
    })
    if rl_hit >= 3:
        recommendations.append("multiple rate-limit hits in this process — back off")

    return {
        "checks": checks,
        "recommendations": recommendations,
        "exit_code": exit_code,
    }, exit_code


def diff_against(other_root: Path) -> tuple[dict[str, Any], int]:
    """Compare parquet file row counts + SHA256 between ``data_dir()`` and
    ``other_root``.

    Returns (report, exit_code). exit_code is 0 if no diff, 5 if any table
    disagrees on row count or any parquet file's sha256 mismatches.

    Both sides are read from the parquet tree (the source of truth for
    migration); the DuckDB backing table is not used here because it can
    contain upserts that have not yet been flushed to parquet.
    """
    other = Path(other_root).expanduser()
    if not other.exists():
        return {"error": f"other root {other} does not exist"}, 3

    this_root = data_dir()
    rows: list[dict[str, Any]] = []
    diffs = 0
    for topic in TOPIC_META:
        this_dir = this_root / f"raw_tushare_{topic}"
        other_dir = other / f"raw_tushare_{topic}"
        this_n = _parquet_row_count(this_dir)
        other_n = _parquet_row_count(other_dir)
        row_match = this_n == other_n
        # sha256 of parquet files (sample first 5 for compactness)
        this_hashes = _parquet_sha256_short(this_dir, limit=5)
        other_hashes = _parquet_sha256_short(other_dir, limit=5)
        hash_match = this_hashes == other_hashes
        if not row_match or not hash_match:
            diffs += 1
        rows.append({
            "topic": topic,
            "this_root": str(this_root),
            "other_root": str(other),
            "this_rows": this_n,
            "other_rows": other_n,
            "row_match": row_match,
            "hash_match": hash_match,
        })
    exit_code = 5 if diffs else 0
    return {
        "diffs": diffs,
        "tables": rows,
    }, exit_code


def _parquet_row_count(topic_dir: Path) -> int:
    """Count rows across all parquet files in ``topic_dir`` (0 if missing)."""
    import duckdb
    if not topic_dir.exists():
        return 0
    files = list(topic_dir.rglob("*.parquet"))
    if not files:
        return 0
    con = duckdb.connect()
    try:
        # Build a union of all parquet files; ``read_parquet`` with a glob
        # also works but the explicit file list is more deterministic.
        file_list = ",".join(f"'{f}'" for f in files)
        row = con.execute(
            f"SELECT count(*) FROM read_parquet([{file_list}])"
        ).fetchone()
        return int(row[0]) if row else 0
    except Exception:
        return -1
    finally:
        con.close()


def _parquet_sha256_short(topic_dir: Path, *, limit: int = 5) -> list[str]:
    """Return up to ``limit`` sha256 prefixes (16 hex) of parquet files."""
    if not topic_dir.exists():
        return []
    out: list[str] = []
    for pq in sorted(topic_dir.rglob("*.parquet"))[:limit]:
        out.append(hashlib.sha256(pq.read_bytes()).hexdigest()[:16])
    return out


# ---------------------------------------------------------------------------
# query (read-only, parameterised)
# ---------------------------------------------------------------------------
class QueryForbidden(Exception):
    """Raised when a ``query`` SQL contains DDL/DML or unsafe references."""


def validate_query_sql(sql: str) -> str:
    """Reject DDL/DML and force the query against mv_* / raw_* views.

    Returns the SQL unchanged on success. Raises ``QueryForbidden`` on any
    forbidden token or out-of-scope table reference.
    """
    import re

    s = sql.strip().rstrip(";")
    if not s:
        raise QueryForbidden("empty SQL")
    first_token = s.split(None, 1)[0].upper()
    if first_token not in ("SELECT", "WITH", "SHOW", "DESCRIBE", "EXPLAIN"):
        raise QueryForbidden(f"only SELECT/WITH/SHOW/DESCRIBE/EXPLAIN allowed; got {first_token!r}")
    upper = s.upper()
    for tok in _FORBIDDEN_DDL_DML:
        if re.search(rf"\b{re.escape(tok)}\b", upper):
            raise QueryForbidden(f"forbidden keyword {tok!r}")
    refs = re.findall(r"(?:FROM|JOIN)\s+([A-Za-z_][A-Za-z0-9_]*)", s, flags=re.IGNORECASE)
    if not refs:
        raise QueryForbidden("query must reference at least one mv_* or raw_* table")
    for r in refs:
        if not (r.startswith("mv_") or r.startswith("raw_")):
            raise QueryForbidden(f"table {r!r} is outside the allowed scope (mv_*/raw_*)")
    return s


def run_query(sql: str, params: Sequence[Any] | None = None) -> dict[str, Any]:
    """Execute a validated read-only SQL against the DuckDB store."""
    from quant_data.store.duckdb_store import DuckDBStore

    safe_sql = validate_query_sql(sql)
    db = DuckDBStore(read_only=True)
    if params:
        cur = db.con.execute(safe_sql, list(params))
    else:
        cur = db.con.execute(safe_sql)
    cols = [d[0] for d in cur.description] if cur.description else []
    rows = cur.fetchall()
    # cap row count to keep --json output manageable
    cap = 1000
    truncated = len(rows) > cap
    rows = rows[:cap]
    return {
        "sql": safe_sql,
        "columns": cols,
        "rows": [list(r) for r in rows],
        "row_count": len(rows),
        "truncated": truncated,
    }


# ---------------------------------------------------------------------------
# sync-range / sync-table (dispatch helpers)
# ---------------------------------------------------------------------------
def sync_one_table(
    topic: str, *, start: date | None = None, end: date | None = None,
    dry_run: bool = False, source: str = "tushare",
) -> dict[str, Any]:
    """Dispatch to the right sync function. Used by ``sync-table`` CLI."""
    from quant_data.sync.driver import (
        sync_adj_factor, sync_daily, sync_daily_basic,
        sync_stock_basic, sync_table, sync_trade_cal,
        sync_moneyflow, sync_moneyflow_hsgt, sync_hsgt_top10,
        sync_index_weight, sync_fund_holdings,
        # A-tier Batch 1 (ADM-653)
        sync_index_classify, sync_index_daily, sync_index_member,
        sync_sw_index, sync_stk_limit, sync_suspend,
        sync_dividend, sync_shares_float,
        # A-tier Batch 2 (ADM-653) — 财务三联表 + 财务指标
        sync_fina_indicator, sync_income, sync_balancesheet, sync_cashflow,
        sync_fina_mainbz, sync_fina_audit, sync_top10_holders,
        # A-tier Batch 3 (ADM-653) — 资金流 + 研报 + 股东
        sync_top_list, sync_margin_detail, sync_top10_floatholders,
        sync_stk_holdertrade, sync_report_rc,
    )
    if topic == "stock_basic":
        if dry_run:
            return {"topic": "stock_basic", "dry_run": True, "rows": 0}
        return sync_stock_basic(source=source)
    if topic == "trade_cal":
        if dry_run:
            return {"topic": "trade_cal", "dry_run": True, "rows": 0,
                    "start": str(start) if start else None,
                    "end": str(end) if end else None}
        return sync_trade_cal(start=start, end=end, source=source)
    if topic in ("daily", "adj_factor", "daily_basic",
                 "moneyflow", "moneyflow_hsgt", "hsgt_top10"):
        if dry_run:
            return {"topic": topic, "dry_run": True, "rows": 0,
                    "start": str(start) if start else None,
                    "end": str(end) if end else None}
        fn = {
            "daily": sync_daily, "adj_factor": sync_adj_factor,
            "daily_basic": sync_daily_basic,
            "moneyflow": sync_moneyflow, "moneyflow_hsgt": sync_moneyflow_hsgt,
            "hsgt_top10": sync_hsgt_top10,
        }[topic]
        return fn(source=source, start_date=start, end_date=end)
    if topic == "index_weight":
        if dry_run:
            return {"topic": "index_weight", "dry_run": True, "rows": 0,
                    "start": str(start) if start else None,
                    "end": str(end) if end else None}
        return sync_index_weight(source=source, start_date=start, end_date=end)
    if topic == "fund_holdings":
        if dry_run:
            return {"topic": "fund_holdings", "dry_run": True, "rows": 0,
                    "start": str(start) if start else None,
                    "end": str(end) if end else None}
        return sync_fund_holdings(source=source, start_date=start, end_date=end)
    # ---- A-tier Batch 1 — 基础 + 事件 (ADM-653) ----
    if topic == "index_classify":
        if dry_run:
            return {"topic": topic, "dry_run": True, "rows": 0}
        return sync_index_classify(source=source)
    if topic == "index_daily":
        if dry_run:
            return {"topic": topic, "dry_run": True, "rows": 0,
                    "start": str(start) if start else None,
                    "end": str(end) if end else None}
        return sync_index_daily(source=source, start_date=start, end_date=end)
    if topic == "index_member":
        if dry_run:
            return {"topic": topic, "dry_run": True, "rows": 0,
                    "start": str(start) if start else None,
                    "end": str(end) if end else None}
        return sync_index_member(source=source, start_date=start, end_date=end)
    if topic == "sw_index":
        if dry_run:
            return {"topic": topic, "dry_run": True, "rows": 0,
                    "start": str(start) if start else None,
                    "end": str(end) if end else None,
                    "tier_blocked": True}
        return sync_sw_index(source=source, start_date=start, end_date=end)
    if topic == "stk_limit":
        if dry_run:
            return {"topic": topic, "dry_run": True, "rows": 0,
                    "start": str(start) if start else None,
                    "end": str(end) if end else None}
        return sync_stk_limit(source=source, start_date=start, end_date=end)
    if topic == "suspend":
        if dry_run:
            return {"topic": topic, "dry_run": True, "rows": 0,
                    "start": str(start) if start else None,
                    "end": str(end) if end else None}
        return sync_suspend(source=source, start_date=start, end_date=end)
    if topic == "dividend":
        if dry_run:
            return {"topic": topic, "dry_run": True, "rows": 0,
                    "start": str(start) if start else None,
                    "end": str(end) if end else None}
        return sync_dividend(source=source, start_date=start, end_date=end)
    if topic == "shares_float":
        if dry_run:
            return {"topic": topic, "dry_run": True, "rows": 0,
                    "start": str(start) if start else None,
                    "end": str(end) if end else None}
        return sync_shares_float(source=source, start_date=start, end_date=end)
    # ---- A-tier Batch 2 — 财务三联表 + 财务指标 (ADM-653) ----
    if topic in ("fina_indicator", "income", "balancesheet", "cashflow",
                 "fina_mainbz", "fina_audit", "top10_holders"):
        if dry_run:
            return {"topic": topic, "dry_run": True, "rows": 0,
                    "start": str(start) if start else None,
                    "end": str(end) if end else None}
        fn = {
            "fina_indicator": sync_fina_indicator, "income": sync_income,
            "balancesheet": sync_balancesheet, "cashflow": sync_cashflow,
            "fina_mainbz": sync_fina_mainbz, "fina_audit": sync_fina_audit,
            "top10_holders": sync_top10_holders,
        }[topic]
        return fn(source=source, start_date=start, end_date=end)
    # ---- A-tier Batch 3 — 资金流 + 研报 + 股东 (ADM-653) ----
    if topic in ("top_list", "margin_detail", "top10_floatholders",
                 "stk_holdertrade", "report_rc"):
        if dry_run:
            return {"topic": topic, "dry_run": True, "rows": 0,
                    "start": str(start) if start else None,
                    "end": str(end) if end else None}
        fn = {
            "top_list": sync_top_list, "margin_detail": sync_margin_detail,
            "top10_floatholders": sync_top10_floatholders,
            "stk_holdertrade": sync_stk_holdertrade, "report_rc": sync_report_rc,
        }[topic]
        return fn(source=source, start_date=start, end_date=end)
    raise ValueError(f"unknown topic {topic!r}; have {list(TOPIC_META)}")


def sync_range(
    start: date, end: date, *,
    only: Sequence[str] = (),
    dry_run: bool = False,
) -> list[dict[str, Any]]:
    """Sync one or more topics across [start, end]. Used by ``sync-range`` CLI."""
    topics = tuple(only) if only else tuple(TOPIC_META)
    out: list[dict[str, Any]] = []
    for t in topics:
        try:
            out.append(sync_one_table(t, start=start, end=end, dry_run=dry_run))
        except Exception as e:  # noqa: BLE001
            log.exception("sync-range %s failed: %s", t, e)
            out.append({"topic": t, "ok": False, "error": str(e)})
    return out


# ---------------------------------------------------------------------------
# Utilities used by list / status subcommands
# ---------------------------------------------------------------------------
def parse_yyyymmdd(s: str) -> date:
    return datetime.strptime(s, "%Y%m%d").date()


def _disk_size_bytes(root: Path) -> int:
    """Total bytes occupied by regular files under ``root``."""
    return sum(p.stat().st_size for p in root.rglob("*") if p.is_file())
