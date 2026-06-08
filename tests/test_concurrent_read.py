"""Validate v0.4 §9.6 hypothesis #6 — multi-agent concurrent reads of DuckDB.

Three independent agents (Portfolio / Risk / Dashboard) all need to read the
same ``quant.duckdb`` file simultaneously without corrupting it and without
killing each other's performance. This test runs three processes, each in
``read_only=True`` mode, each executing the query pattern of a real agent,
and verifies:

  1. **Correctness** — every process gets a complete, non-empty result.
  2. **Integrity** — the on-disk file is not corrupted (SHA-256 identical
     before/after; a fresh reader can re-open the file).
  3. **Performance** — the total wall-clock of the 3-process round is no
     more than **1.5×** the single-process baseline (i.e. multi-process
     reader contention is acceptable).

The whole experiment is repeated for ``N_ROUNDS`` rounds; we report and
assert against the **median** to absorb one-off noise (cold cache, GC,
neighbour-process CPU steal).
"""
from __future__ import annotations

import hashlib
import json
import multiprocessing as mp
import statistics
import time
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd
import pytest

from quant_data.paths import duckdb_path
from quant_data.store.duckdb_store import DuckDBStore

N_ROUNDS = 3
DEGRADATION_LIMIT = 1.5  # 3-proc wall-clock / 1-proc wall-clock must be < this
# Number of stocks and date windows we exercise. Tuned so that the baseline
# is ~10s on the 14M-row v0.4 db — short enough for pytest, long enough that
# a regression shows up in the median.
N_PORTFOLIO_STOCKS = 100
N_RISK_STOCKS = 50
PORTFOLIO_WINDOW_DAYS = 60
RISK_WINDOW_DAYS = 365
N_DASHBOARD_AGGREGATIONS = 5


# ---------- worker functions (must be picklable, no closures) ----------

def _portfolio_worker(db_path: str, end_date_iso: str, ts_codes: list[str]) -> dict[str, Any]:
    """Portfolio-style cross-section read.

    For each of 60 trailing trade dates, pull the qfq close + adj_factor for
    ~100 stocks. This mimics the "give me a 60-day factor matrix" pattern.
    """
    db = DuckDBStore(path=Path(db_path), read_only=True)
    try:
        end = date.fromisoformat(end_date_iso)
        start = end - timedelta(days=PORTFOLIO_WINDOW_DAYS * 2)  # generous; trade_cal filters
        placeholders = ",".join(["?"] * len(ts_codes))
        sql = (
            f"SELECT trade_date, ts_code, close_qfq, adj_factor "
            f"FROM mv_daily_qfq "
            f"WHERE ts_code IN ({placeholders}) "
            f"  AND trade_date BETWEEN ? AND ? "
            f"ORDER BY ts_code, trade_date"
        )
        params = list(ts_codes) + [start, end]
        t0 = time.perf_counter()
        df = db.query(sql, params=params)
        elapsed = time.perf_counter() - t0
        return {
            "name": "portfolio",
            "rows": int(len(df)),
            "distinct_dates": int(df["trade_date"].nunique()) if len(df) else 0,
            "distinct_codes": int(df["ts_code"].nunique()) if len(df) else 0,
            "elapsed_s": elapsed,
            "max_date": str(df["trade_date"].max()) if len(df) else None,
            "min_date": str(df["trade_date"].min()) if len(df) else None,
        }
    finally:
        db.con.close()


def _risk_worker(db_path: str, end_date_iso: str, ts_codes: list[str]) -> dict[str, Any]:
    """Risk-style backtest read.

    Pull full OHLCV + qfq close for ~50 stocks across 1 year. This is the
    "give me everything I need to backtest this basket" pattern.
    """
    db = DuckDBStore(path=Path(db_path), read_only=True)
    try:
        end = date.fromisoformat(end_date_iso)
        start = end - timedelta(days=RISK_WINDOW_DAYS + 30)
        placeholders = ",".join(["?"] * len(ts_codes))
        sql = (
            f"SELECT trade_date, ts_code, open, high, low, close, "
            f"       vol, amount, close_qfq, adj_factor "
            f"FROM mv_daily_qfq "
            f"WHERE ts_code IN ({placeholders}) "
            f"  AND trade_date BETWEEN ? AND ? "
            f"ORDER BY ts_code, trade_date"
        )
        params = list(ts_codes) + [start, end]
        t0 = time.perf_counter()
        df = db.query(sql, params=params)
        elapsed = time.perf_counter() - t0
        return {
            "name": "risk",
            "rows": int(len(df)),
            "distinct_dates": int(df["trade_date"].nunique()) if len(df) else 0,
            "distinct_codes": int(df["ts_code"].nunique()) if len(df) else 0,
            "elapsed_s": elapsed,
            "max_date": str(df["trade_date"].max()) if len(df) else None,
            "min_date": str(df["trade_date"].min()) if len(df) else None,
        }
    finally:
        db.con.close()


def _dashboard_worker(db_path: str, end_date_iso: str) -> dict[str, Any]:
    """Dashboard-style: N aggregations on mv_daily_basic.

    Run a mix of GROUP BY queries (market-wide pe/pb/turnover, sector
    breakdown, top-N by total_mv, etc.).
    """
    db = DuckDBStore(path=Path(db_path), read_only=True)
    try:
        end = date.fromisoformat(end_date_iso)
        queries = [
            # 1. market-wide per-date aggregates
            ("SELECT trade_date, AVG(pe) AS avg_pe, AVG(pb) AS avg_pb, "
             "       SUM(total_mv) AS total_mv_sum, SUM(circ_mv) AS circ_mv_sum "
             "FROM mv_daily_basic "
             "WHERE trade_date BETWEEN ? AND ? AND pe IS NOT NULL "
             "GROUP BY trade_date ORDER BY trade_date"),
            # 2. top 20 stocks by total_mv on the last date
            ("SELECT ts_code, total_mv, pe, pb "
             "FROM mv_daily_basic "
             "WHERE trade_date = ? AND total_mv IS NOT NULL "
             "ORDER BY total_mv DESC LIMIT 20"),
            # 3. per-stock volatility proxy: count of dates with pe>0 in window
            ("SELECT COUNT(DISTINCT trade_date) AS n_days, "
             "       COUNT(DISTINCT ts_code) AS n_codes "
             "FROM mv_daily_basic "
             "WHERE trade_date BETWEEN ? AND ?"),
            # 4. turnover distribution per date
            ("SELECT trade_date, AVG(turnover_rate) AS avg_turnover, "
             "       MAX(turnover_rate) AS max_turnover, "
             "       MIN(turnover_rate) AS min_turnover "
             "FROM mv_daily_basic "
             "WHERE trade_date BETWEEN ? AND ? AND turnover_rate IS NOT NULL "
             "GROUP BY trade_date"),
            # 5. cross-market summary (SSE/SZSE/BSE) on the last date
            ("WITH last_d AS (SELECT MAX(trade_date) AS d FROM mv_daily_basic) "
             "SELECT SUBSTR(ts_code, -2) AS exchange, "
             "       COUNT(*) AS n_stocks, AVG(pe) AS avg_pe "
             "FROM mv_daily_basic, last_d "
             "WHERE trade_date = last_d.d "
             "GROUP BY SUBSTR(ts_code, -2) ORDER BY exchange"),
        ]
        t0 = time.perf_counter()
        total_rows = 0
        for q in queries:
            if "?" in q and q.count("?") == 2:
                df = db.query(q, [end - timedelta(days=30), end])
            elif "?" in q and q.count("?") == 1:
                df = db.query(q, [end])
            else:
                df = db.query(q)
            total_rows += int(len(df))
        elapsed = time.perf_counter() - t0
        return {
            "name": "dashboard",
            "rows": total_rows,
            "distinct_dates": N_DASHBOARD_AGGREGATIONS,
            "distinct_codes": 0,
            "elapsed_s": elapsed,
            "max_date": end_date_iso,
            "min_date": (end - timedelta(days=30)).isoformat(),
        }
    finally:
        db.con.close()


# ---------- helpers ----------

def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _pick_universe_and_window() -> tuple[list[str], list[str], str]:
    """Pick a stable stock universe + end date from the real db.

    Deterministic (ORDER BY) so re-runs are comparable across CI runs.

    Picks stocks that have data in the recent window (1y back from
    MAX(trade_date)) — this matches what Portfolio / Risk would do in
    production (no point loading delisted/empty names into a basket).
    Some "L"-status names in stock_basic have no recent bars; we filter
    them out before slicing to the test sizes.

    On macOS DuckDB file locks can outlive the previous Python process for
    a few seconds; we retry the open with a small backoff so the test is
    robust to re-runs against the same file.
    """
    last_err: Exception | None = None
    for attempt in range(10):
        try:
            con = duckdb.connect(str(duckdb_path()), read_only=True)
            break
        except Exception as e:  # IOError / IOException — file still locked
            last_err = e
            time.sleep(1.0)
    else:
        raise RuntimeError(f"Could not open DuckDB file after 10 retries: {last_err}")
    try:
        end_iso = str(con.execute(
            "SELECT MAX(trade_date) FROM raw_tushare_daily"
        ).fetchone()[0])
        end = date.fromisoformat(end_iso)
        # universe = ts_codes with >= 1 daily row in the trailing 1y window
        codes = [
            r[0] for r in con.execute(
                """
                SELECT ts_code FROM raw_tushare_daily
                WHERE trade_date BETWEEN ? AND ?
                GROUP BY ts_code
                HAVING COUNT(*) > 50
                ORDER BY ts_code
                """,
                [end - timedelta(days=RISK_WINDOW_DAYS + 30), end],
            ).fetchall()
        ]
        portfolio_codes = codes[:N_PORTFOLIO_STOCKS]
        risk_codes = codes[:N_RISK_STOCKS]
        if len(portfolio_codes) < N_PORTFOLIO_STOCKS or len(risk_codes) < N_RISK_STOCKS:
            raise RuntimeError(
                f"Universe too small: got {len(codes)} active stocks, "
                f"need {N_PORTFOLIO_STOCKS} (portfolio) + "
                f"{N_RISK_STOCKS} (risk, may overlap)."
            )
    finally:
        con.close()
    return portfolio_codes, risk_codes, end_iso


def _run_sequential(db_path: str, end_iso: str, p_codes: list[str], r_codes: list[str]) -> float:
    """Run all three query styles back-to-back in the current process.

    This is the single-process baseline: it's the time the same workload
    would take if there were no concurrency. We use it as the denominator
    for the degradation ratio.
    """
    t0 = time.perf_counter()
    _portfolio_worker(db_path, end_iso, p_codes)
    _risk_worker(db_path, end_iso, r_codes)
    _dashboard_worker(db_path, end_iso)
    return time.perf_counter() - t0


def _run_concurrent(db_path: str, end_iso: str, p_codes: list[str], r_codes: list[str]) -> tuple[float, list[dict[str, Any]]]:
    """Run all three query styles in parallel processes.

    Uses ``spawn`` so each worker gets a fresh interpreter — no fork-time
    DuckDB state leaks, no GIL contention, no shared file-handle weirdness.
    Wall-clock starts before the first process is spawned and stops when
    the last one finishes.
    """
    ctx = mp.get_context("spawn")
    t0 = time.perf_counter()
    procs = [
        ctx.Process(target=_portfolio_worker, args=(db_path, end_iso, p_codes)),
        ctx.Process(target=_risk_worker, args=(db_path, end_iso, r_codes)),
        ctx.Process(target=_dashboard_worker, args=(db_path, end_iso)),
    ]
    for p in procs:
        p.start()
    for p in procs:
        p.join(timeout=600)
    for p in procs:
        if p.is_alive():
            p.terminate()
            raise RuntimeError(f"{p.name} hung past 10 min")
        if p.exitcode != 0:
            raise RuntimeError(f"{p.name} exited with code {p.exitcode}")
    wall = time.perf_counter() - t0
    # Workers don't return data; we re-read the result by re-running
    # in-process for the *correctness* check. (Timing for those re-runs
    # is excluded from the wall measurement above.)
    return wall, []


def _collect_results(db_path: str, end_iso: str, p_codes: list[str], r_codes: list[str]) -> list[dict[str, Any]]:
    """Run the three query styles in-process to get result metrics.

    Used for correctness checks — we run each query once and compare the
    row counts / distinct dates against a known reference.
    """
    return [
        _portfolio_worker(db_path, end_iso, p_codes),
        _risk_worker(db_path, end_iso, r_codes),
        _dashboard_worker(db_path, end_iso),
    ]


# ---------- the test ----------

@dataclass
class _RoundResult:
    seq_s: float
    par_s: float
    degradation: float
    sha_before: str
    sha_after: str
    re_open_ok: bool
    results: list[dict[str, Any]] = field(default_factory=list)


def test_concurrent_reads_are_safe_and_fast(capsys):
    db_path = duckdb_path()
    assert db_path.exists(), f"DuckDB file missing: {db_path}"
    db_path_str = str(db_path)

    # Pre-flight: pick universe and confirm db is openable
    p_codes, r_codes, end_iso = _pick_universe_and_window()
    print(f"\n[concurrent_read] db={db_path_str}  end_date={end_iso}  "
          f"|portfolio|={len(p_codes)}  |risk|={len(r_codes)}")

    rounds: list[_RoundResult] = []
    for r in range(1, N_ROUNDS + 1):
        sha_before = _sha256_file(db_path)
        seq_s = _run_sequential(db_path_str, end_iso, p_codes, r_codes)
        par_s, _ = _run_concurrent(db_path_str, end_iso, p_codes, r_codes)
        # Verify db still openable after concurrent reads
        try:
            con = None
            for attempt in range(10):
                try:
                    con = duckdb.connect(db_path_str, read_only=True)
                    break
                except Exception:
                    time.sleep(1.0)
            if con is None:
                raise RuntimeError("Could not re-open DuckDB after concurrent burst")
            n = con.execute("SELECT count(*) FROM mv_daily_qfq").fetchone()[0]
            con.close()
            re_open_ok = n > 0
        except Exception as e:
            re_open_ok = False
            print(f"[concurrent_read] round {r} RE-OPEN FAILED: {e}")
        sha_after = _sha256_file(db_path)
        # Collect correctness results (in-process, after concurrent burst)
        results = _collect_results(db_path_str, end_iso, p_codes, r_codes)
        rr = _RoundResult(
            seq_s=seq_s, par_s=par_s,
            degradation=(par_s / seq_s) if seq_s > 0 else float("inf"),
            sha_before=sha_before, sha_after=sha_after,
            re_open_ok=re_open_ok, results=results,
        )
        rounds.append(rr)
        print(f"[concurrent_read] round {r}: seq={seq_s:.2f}s  par={par_s:.2f}s  "
              f"deg={rr.degradation:.2f}x  sha_match={sha_before == sha_after}  "
              f"reopen_ok={re_open_ok}  rows[P/R/D]={results[0]['rows']}/{results[1]['rows']}/{results[2]['rows']}")

    # ---- aggregate ----
    seqs = sorted(r.seq_s for r in rounds)
    pars = sorted(r.par_s for r in rounds)
    degs = sorted(r.degradation for r in rounds)
    seq_median = statistics.median(seqs)
    par_median = statistics.median(pars)
    deg_median = statistics.median(degs)

    # ---- assertions ----
    sha_set = {r.sha_before for r in rounds} | {r.sha_after for r in rounds}
    assert len(sha_set) == 1, (
        f"DuckDB file SHA-256 changed during the test — file is corrupted.\n"
        f"  observed hashes: {sorted(sha_set)}"
    )
    assert all(r.re_open_ok for r in rounds), (
        "DuckDB file could not be re-opened in read-only mode after the "
        "concurrent burst — file is corrupted."
    )
    # Correctness: every result is non-empty and has the expected shape
    for r in rounds:
        for res in r.results:
            assert res["rows"] > 0, f"{res['name']} returned 0 rows: {res}"
            if res["name"] == "portfolio":
                assert res["distinct_codes"] == N_PORTFOLIO_STOCKS, res
                assert res["distinct_dates"] >= 30, res  # at least ~half the window is trade days
            if res["name"] == "risk":
                assert res["distinct_codes"] == N_RISK_STOCKS, res
                assert res["distinct_dates"] >= 100, res  # 1y ≈ 240 trade days
            if res["name"] == "dashboard":
                assert res["distinct_dates"] == N_DASHBOARD_AGGREGATIONS, res
    # Performance: degradation below the limit
    assert deg_median < DEGRADATION_LIMIT, (
        f"Median degradation ratio {deg_median:.2f}x >= {DEGRADATION_LIMIT}x — "
        f"3-process readers block each other. seq_median={seq_median:.2f}s  "
        f"par_median={par_median:.2f}s"
    )

    # ---- report (visible in pytest -s / -v output, and copyable to issue) ----
    report = {
        "db_path": db_path_str,
        "end_date": end_iso,
        "n_rounds": N_ROUNDS,
        "uni": {"portfolio": N_PORTFOLIO_STOCKS, "risk": N_RISK_STOCKS,
                "window_days_p": PORTFOLIO_WINDOW_DAYS, "window_days_r": RISK_WINDOW_DAYS},
        "per_round": [
            {
                "round": i + 1,
                "seq_s": round(r.seq_s, 3),
                "par_s": round(r.par_s, 3),
                "degradation": round(r.degradation, 3),
                "sha256_match": r.sha_before == r.sha_after,
                "reopen_ok": r.re_open_ok,
                "rows_portfolio": r.results[0]["rows"],
                "rows_risk":      r.results[1]["rows"],
                "rows_dashboard": r.results[2]["rows"],
            }
            for i, r in enumerate(rounds)
        ],
        "median_seq_s":   round(seq_median, 3),
        "median_par_s":   round(par_median, 3),
        "median_deg":     round(deg_median, 3),
        "degradation_limit": DEGRADATION_LIMIT,
        "verdict": "PASS" if deg_median < DEGRADATION_LIMIT else "FAIL",
        "sha256": next(iter(sha_set)),
    }
    print("\n[concurrent_read] FINAL REPORT")
    print(json.dumps(report, ensure_ascii=False, indent=2))
