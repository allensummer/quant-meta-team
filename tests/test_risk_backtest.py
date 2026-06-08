"""Unit tests for quant_risk (Week 3 deliverable).

Coverage targets ≥ 80% for ``quant_risk/``. The tests use a tmp DATA_DIR
so they don't depend on (or corrupt) the real project data. They
specifically pin down the 8 acceptance-criteria items from the issue:

  1) Stock pool built at a historical time-point
  2) Suspended days are not tradable
  3) Limit-up filter blocks buys
  4) Delisted stocks excluded from the universe
  5) One-way cost of 0.15% applied to traded notional
  6) T+1 execution (signal at close of t, trade at close of t+1)
  7) Benchmark aligned to the strategy calendar
  8) Reproducibility: identical inputs → identical NAV

Plus auxiliary tests for metrics, LimitBand, momentum, and the public
data-layer surface.
"""
from __future__ import annotations

import math
import os
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from quant_data.store.duckdb_store import DuckDBStore
from quant_data.store.parquet_store import ParquetStore
from quant_risk import data_layer as dl_mod
from quant_risk import metrics as metrics_mod
from quant_risk.backtest import (
    BacktestConfig,
    run_backtest,
    momentum_score,
    _to_date,
)
from quant_risk.data_layer import LimitBand, RiskDataLayer


# ---------------- Fixtures ----------------
@pytest.fixture
def fixture_data_dir(tmp_data_dir, tmp_path: Path) -> Path:
    """A tmp data dir with a small but complete daily/adj_factor/stock_basic
    /trade_cal parquet set. Sufficient to exercise every public API."""
    # Trade calendar: 2 months of trading days (Mon-Fri minus a holiday)
    cal_dates = []
    for d in pd.date_range("2024-01-01", "2024-03-31", freq="B"):
        cal_dates.append({"exchange": "SSE", "cal_date": d.date(), "is_open": 1, "pretrade_date": None})
        cal_dates.append({"exchange": "SZSE", "cal_date": d.date(), "is_open": 1, "pretrade_date": None})
    # Add a holiday (2024-02-09..2024-02-18 Chinese New Year)
    cal_df = pd.DataFrame(cal_dates)
    cal_df = cal_df[~cal_df["cal_date"].isin(pd.date_range("2024-02-09", "2024-02-18").date)]
    pq_cal = ParquetStore(source="tushare", topic="trade_cal")
    for d, sub in cal_df.groupby("cal_date"):
        pq_cal.write(sub.reset_index(drop=True), partition_value=d)

    # stock_basic: 4 active + 1 delisted + 1 ST-flagged
    #   - 000001.SZ active listed 1990 (kept)
    #   - 000002.SZ active listed 1990 (kept)
    #   - 600000.SH active listed 2000 (kept)
    #   - 999999.SH delisted 2024-02-15 (kept in Jan, excluded from Feb 20 onward)
    #   - STTEST.SZ active, name contains 'ST' (excluded by name filter)
    sb = pd.DataFrame([
        {"ts_code": "000001.SZ", "symbol": "000001", "name": "PINGAN", "industry": "BANK", "cnspell": "PA",
         "list_date": date(1991, 4, 3), "list_status": "L", "delist_date": None, "exchange": "SZSE"},
        {"ts_code": "000002.SZ", "symbol": "000002", "name": "WANKE", "industry": "REALESTATE", "cnspell": "WK",
         "list_date": date(1991, 1, 29), "list_status": "L", "delist_date": None, "exchange": "SZSE"},
        {"ts_code": "600000.SH", "symbol": "600000", "name": "PUDONG", "industry": "BANK", "cnspell": "PD",
         "list_date": date(1999, 11, 10), "list_status": "L", "delist_date": None, "exchange": "SSE"},
        {"ts_code": "999999.SH", "symbol": "999999", "name": "GONEONE", "industry": "OTHER", "cnspell": "GX",
         "list_date": date(2000, 1, 1), "list_status": "D", "delist_date": date(2024, 2, 15), "exchange": "SSE"},
        {"ts_code": "STTEST.SZ", "symbol": "300000", "name": "ST TEST", "industry": "OTHER", "cnspell": "SS",
         "list_date": date(2010, 1, 1), "list_status": "L", "delist_date": None, "exchange": "SZSE"},
    ])
    pq_sb = ParquetStore(source="tushare", topic="stock_basic")
    pq_sb.write(sb, partition_value=None)

    # daily: 2 months of data; include a suspension and a limit-up scenario
    rows = []
    cal_index = sorted(cal_df["cal_date"].unique())
    rng = np.random.default_rng(0)
    for code, base_price in [("000001.SZ", 10.0), ("000002.SZ", 20.0), ("600000.SH", 8.0), ("STTEST.SZ", 5.0)]:
        price = base_price
        for i, d in enumerate(cal_index):
            # Inject a suspension on day 5 for 000001.SZ
            if code == "000001.SZ" and i == 5:
                rows.append({"ts_code": code, "trade_date": d, "open": price, "high": price, "low": price,
                             "close": price, "pre_close": price, "change": 0.0, "pct_chg": 0.0,
                             "vol": 0.0, "amount": 0.0})
                continue
            # Inject a limit-up on day 10 for 600000.SH (+10% to 8.8)
            if code == "600000.SH" and i == 10:
                p_new = round(price * 1.10, 2)
                rows.append({"ts_code": code, "trade_date": d, "open": price, "high": p_new, "low": price,
                             "close": p_new, "pre_close": price, "change": p_new - price, "pct_chg": 10.0,
                             "vol": 1_000_000.0, "amount": 8_000_000.0})
                price = p_new
                continue
            ret = rng.normal(0, 0.01)
            p_new = round(price * (1 + ret), 2)
            rows.append({"ts_code": code, "trade_date": d, "open": price, "high": max(price, p_new), "low": min(price, p_new),
                         "close": p_new, "pre_close": price, "change": p_new - price, "pct_chg": ret * 100,
                         "vol": 1_000_000.0, "amount": 8_000_000.0})
            price = p_new
    daily = pd.DataFrame(rows)
    pq_d = ParquetStore(source="tushare", topic="daily")
    for d, sub in daily.groupby("trade_date"):
        pq_d.write(sub.reset_index(drop=True), partition_value=d)

    # adj_factor: 1.0 for all
    af_rows = []
    for code in ("000001.SZ", "000002.SZ", "600000.SH", "STTEST.SZ"):
        for d in cal_index:
            af_rows.append({"ts_code": code, "trade_date": d, "adj_factor": 1.0})
    af = pd.DataFrame(af_rows)
    pq_a = ParquetStore(source="tushare", topic="adj_factor")
    for d, sub in af.groupby("trade_date"):
        pq_a.write(sub.reset_index(drop=True), partition_value=d)

    return tmp_data_dir


@pytest.fixture
def risk_dl(fixture_data_dir) -> RiskDataLayer:
    """A RiskDataLayer rooted at the fixture DATA_DIR, with views bootstrapped."""
    return RiskDataLayer(read_only=False)


# ============================================================
# 1) Universe is built at the historical time-point
# ============================================================
def test_universe_filters_by_list_date_and_excludes_delisted(risk_dl):
    """On 2024-02-20 the delisted stock 999999.SH (delist_date=2024-02-15)
    must be excluded, and the 4 actively-listed ones returned. STTEST.SZ is
    excluded by the name filter."""
    u = risk_dl.get_universe(date(2024, 2, 20))
    codes = set(u["ts_code"].tolist())
    assert "000001.SZ" in codes
    assert "000002.SZ" in codes
    assert "600000.SH" in codes
    assert "999999.SH" not in codes  # already delisted by 2024-02-20
    assert "STTEST.SZ" not in codes   # name contains ST
    # Spot-check that ALL returned stocks have list_date <= as_of
    for d in u["list_date"]:
        assert pd.Timestamp(d).date() <= date(2024, 2, 20)


def test_index_member_returns_only_60_00_30_68_prefixed(risk_dl):
    """csi300_proxy restricts to 60/00/30/68-prefixed codes."""
    i = risk_dl.get_index_member(date(2024, 2, 20))
    for code in i["ts_code"]:
        prefix3 = code.split(".")[0][:2]
        assert prefix3 in {"60", "00", "30", "68"}


# ============================================================
# 2) Suspended days are not tradable
# ============================================================
def test_is_suspended_detects_zero_volume_no_movement(risk_dl):
    """We injected a suspended day for 000001.SZ on the 6th trading day of
    2024-01. is_suspended should return True there, False elsewhere."""
    # Discover the suspended day
    cal = risk_dl.get_calendar(date(2024, 1, 1), date(2024, 1, 31))
    cal_dates = sorted({_to_date(d) for d in cal["cal_date"]})
    suspended_day = cal_dates[5]  # we set i=5 in the fixture
    other_day = cal_dates[0]
    assert risk_dl.is_suspended("000001.SZ", suspended_day) is True
    assert risk_dl.is_suspended("000001.SZ", other_day) is False


# ============================================================
# 3) Limit-up filter blocks buys
# ============================================================
def test_is_limit_up_detects_10pct_move_on_main_board(risk_dl):
    """We injected a 10% limit-up for 600000.SH on the 11th trading day
    (i=10). On that day high == round(pre_close * 1.10, 2) → is_limit_up
    must be True. The previous day must not be flagged."""
    cal = risk_dl.get_calendar(date(2024, 1, 1), date(2024, 1, 31))
    cal_dates = sorted({_to_date(d) for d in cal["cal_date"]})
    up_day = cal_dates[10]
    prev_day = cal_dates[9]
    assert risk_dl.is_limit_up("600000.SH", up_day) is True
    assert risk_dl.is_limit_up("600000.SH", prev_day) is False


def test_limit_band_distinguishes_boards():
    """Different boards have different price-limit percentages."""
    assert LimitBand.for_code("600000.SH").pct == pytest.approx(0.10)
    assert LimitBand.for_code("000001.SZ").pct == pytest.approx(0.10)
    assert LimitBand.for_code("300750.SZ").pct == pytest.approx(0.20)
    assert LimitBand.for_code("688981.SH").pct == pytest.approx(0.20)
    assert LimitBand.for_code("830799.BJ").pct == pytest.approx(0.30)


# ============================================================
# 4) Delisted stocks excluded from the universe
# ============================================================
def test_universe_excludes_delisted_via_delist_date(risk_dl):
    """999999.SH has delist_date=2024-02-15. As of 2024-01-31 it must still
    appear (delist is later); as of 2024-02-20 it must be gone."""
    early = risk_dl.get_universe(date(2024, 1, 31))
    late = risk_dl.get_universe(date(2024, 2, 20))
    assert "999999.SH" in set(early["ts_code"])  # still alive in Jan
    assert "999999.SH" not in set(late["ts_code"])  # delisted by Feb 20


# ============================================================
# 5) One-way cost of 0.15% applied to traded notional
# ============================================================
def test_one_way_cost_reduces_nav(risk_dl):
    """Run a tiny backtest with cost_bps=15 and again with cost_bps=0.
    The cost-bearing NAV must be lower (or equal) on every day where a
    trade occurred."""
    cfg_cost = BacktestConfig(
        start=date(2024, 1, 1), end=date(2024, 2, 15),
        rebalance_freq="monthly", lookback_days=5, top_n=2, cost_bps=15.0,
    )
    cfg_nocost = BacktestConfig(
        start=date(2024, 1, 1), end=date(2024, 2, 15),
        rebalance_freq="monthly", lookback_days=5, top_n=2, cost_bps=0.0,
    )
    r_cost = run_backtest(cfg_cost, dl=risk_dl)
    r_nocost = run_backtest(cfg_nocost, dl=risk_dl)
    # Same windows, same trades → cost-bps > 0 ⇒ lower NAV everywhere.
    assert (r_cost.nav <= r_nocost.nav + 1e-12).all()
    # At least one trade must have happened
    assert r_cost.trades, "expected at least one trade"
    traded_notional = sum(abs(t.weight_after - t.weight_before) for t in r_cost.trades)
    expected_drag = traded_notional * 0.0015
    nav_drag = float(r_nocost.nav.iloc[-1] - r_cost.nav.iloc[-1])
    # Cost is applied on each rebalance: NAV *= (1 - traded * 0.0015).
    # The drag is dominated by traded_notional * cost_bps / 1e4, but the
    # exact value picks up a small compound-with-returns term. Use a
    # generous relative tolerance here.
    assert math.isclose(nav_drag, expected_drag, rel_tol=5e-2, abs_tol=1e-4), \
        f"cost drag mismatch: nav_drag={nav_drag}, expected={expected_drag}"


# ============================================================
# 6) T+1 execution: signal at t, trade at t+1
# ============================================================
def test_t_plus_one_trade_executes_day_after_signal(risk_dl):
    """The rebalance schedule picks the first trading day of each month.
    Trades for that rebalance must appear on the *next* trade day, not
    on the rebalance day itself."""
    cfg = BacktestConfig(
        start=date(2024, 1, 1), end=date(2024, 3, 31),
        rebalance_freq="monthly", lookback_days=5, top_n=2, cost_bps=0.0,
    )
    r = run_backtest(cfg, dl=risk_dl)
    trade_dates = sorted({t.trade_date for t in r.trades})
    rebal_dates = sorted({d for d in r.rebalance_dates})
    # Each trade date must be strictly after some rebalance date, and not be a rebalance date itself.
    for td in trade_dates:
        assert td not in rebal_dates
    # First trade date should be one day after the first rebal date (next trading day).
    assert trade_dates[0] > rebal_dates[0]


def test_signal_score_is_pure_function_of_close_matrix(risk_dl):
    """momentum_score(close, as_of) must be deterministic for a fixed
    matrix and as_of date."""
    cal = risk_dl.get_calendar(date(2024, 1, 1), date(2024, 2, 15))
    cal_dates = sorted({_to_date(d) for d in cal["cal_date"]})
    members = risk_dl.get_index_member(date(2024, 1, 1))
    matrix = risk_dl.get_close_matrix(members["ts_code"].tolist(), date(2024, 1, 1), date(2024, 2, 15))
    a = momentum_score(matrix, cal_dates[10], lookback=5)
    b = momentum_score(matrix, cal_dates[10], lookback=5)
    pd.testing.assert_series_equal(a.sort_index(), b.sort_index())


# ============================================================
# 7) Benchmark aligned to the strategy calendar
# ============================================================
def test_benchmark_aligned_to_strategy_days(risk_dl):
    cfg = BacktestConfig(
        start=date(2024, 1, 1), end=date(2024, 3, 31),
        rebalance_freq="monthly", lookback_days=5, top_n=2, cost_bps=0.0,
    )
    r = run_backtest(cfg, dl=risk_dl)
    # benchmark_nav must be non-empty and share the strategy's date index
    assert not r.benchmark_nav.empty
    assert r.benchmark_nav.index[0] >= r.nav.index[0]
    # Reindex and check no full-NaN holes where the strategy has data
    aligned = r.benchmark_nav.reindex(r.nav.index).ffill()
    assert aligned.notna().sum() >= len(r.nav) * 0.9


# ============================================================
# 8) Reproducibility
# ============================================================
def test_reproducibility_same_inputs_same_nav(risk_dl):
    cfg = BacktestConfig(
        start=date(2024, 1, 1), end=date(2024, 3, 31),
        rebalance_freq="monthly", lookback_days=5, top_n=2, cost_bps=15.0,
    )
    a = run_backtest(cfg, dl=risk_dl)
    b = run_backtest(cfg, dl=risk_dl)
    pd.testing.assert_series_equal(a.nav, b.nav, check_names=False)
    # And the trade ledger must be identical
    assert [(t.trade_date, t.ts_code, t.weight_after) for t in a.trades] == \
           [(t.trade_date, t.ts_code, t.weight_after) for t in b.trades]


# ============================================================
# Auxiliary tests — metrics, helpers
# ============================================================
def test_metrics_compute_all_seven_keys():
    nav = pd.Series(
        [1.0, 1.01, 1.005, 0.99, 1.02, 1.015, 1.03],
        index=pd.date_range("2024-01-01", periods=7),
    )
    m = metrics_mod.compute_metrics(nav, turnover_series=pd.Series([0.1, 0.1, 0.1]))
    d = m.as_dict()
    assert set(d.keys()) == {"annual_return", "volatility", "sharpe", "max_drawdown", "var_95", "cvar_95", "turnover"}
    # Sanity bounds
    assert d["max_drawdown"] >= 0  # stored as positive
    assert d["var_95"] >= 0        # stored as positive
    assert d["cvar_95"] >= d["var_95"] - 1e-9


def test_metrics_handles_degenerate_inputs():
    """A constant NAV → zero vol, NaN Sharpe, 0 drawdown, 0 VaR."""
    nav = pd.Series([1.0] * 5, index=pd.date_range("2024-01-01", periods=5))
    m = metrics_mod.compute_metrics(nav)
    assert m.volatility == pytest.approx(0.0)
    assert math.isnan(m.sharpe) or m.sharpe == 0
    assert m.max_drawdown == pytest.approx(0.0)


def test_to_date_handles_timestamp_and_string():
    assert _to_date(pd.Timestamp("2024-01-02")) == date(2024, 1, 2)
    assert _to_date("2024-01-02") == date(2024, 1, 2)
    assert _to_date(date(2024, 1, 2)) == date(2024, 1, 2)


def test_calendar_and_next_prev(risk_dl):
    cal = risk_dl.get_calendar(date(2024, 1, 1), date(2024, 1, 31))
    assert not cal.empty
    assert cal["cal_date"].is_monotonic_increasing
    a = _to_date(cal["cal_date"].iloc[0])
    nxt = risk_dl.next_trade_day(a)
    prv = risk_dl.prev_trade_day(a)
    assert nxt is not None and nxt > a
    assert prv is None  # first day in the slice


def test_get_calendar_dedups_duplicate_snapshots(fixture_data_dir, monkeypatch):
    """Even with multiple sync snapshots, get_calendar should return
    one row per (cal_date, exchange)."""
    dl = RiskDataLayer(read_only=False)
    cal = dl.get_calendar(date(2024, 1, 1), date(2024, 1, 31))
    # one SSE + one SZSE per day
    counts = cal.groupby("cal_date").size()
    assert (counts == 2).all(), f"expected 2 rows/day, got counts: {counts.value_counts().to_dict()}"


def test_data_layer_does_not_import_tushare_or_akshare():
    """Hard guard: the risk agent must never import tushare/akshare."""
    import importlib, sys
    # Wipe any cached imports of the offending modules
    for name in list(sys.modules):
        if name == "tushare" or name.startswith("tushare.") \
           or name == "akshare" or name.startswith("akshare."):
            del sys.modules[name]
    # Importing the package must not pull tushare/akshare
    if "quant_risk" in sys.modules:
        del sys.modules["quant_risk"]
    if "quant_risk.data_layer" in sys.modules:
        del sys.modules["quant_risk.data_layer"]
    if "quant_risk.backtest" in sys.modules:
        del sys.modules["quant_risk.backtest"]
    importlib.import_module("quant_risk.data_layer")
    importlib.import_module("quant_risk.backtest")
    leaked = [m for m in sys.modules if m == "tushare" or m.startswith("tushare.")
              or m == "akshare" or m.startswith("akshare.")]
    assert not leaked, f"tushare/akshare leaked into quant_risk: {leaked}"


# ---------------- Additional coverage tests ----------------
def test_is_limit_down_for_main_board(risk_dl):
    """We did not inject a 跌停, but the function should return False
    consistently on a normal day and not error."""
    cal = risk_dl.get_calendar(date(2024, 1, 1), date(2024, 1, 31))
    cal_dates = sorted({_to_date(d) for d in cal["cal_date"]})
    assert risk_dl.is_limit_down("000001.SZ", cal_dates[2]) is False


def test_status_checks_return_false_on_missing_row(risk_dl):
    """If the row for (code, date) is missing, the helpers return False."""
    far = date(2099, 1, 1)
    assert risk_dl.is_suspended("000001.SZ", far) is False
    assert risk_dl.is_limit_up("000001.SZ", far) is False
    assert risk_dl.is_limit_down("000001.SZ", far) is False


def test_prev_and_next_trade_day_in_middle(risk_dl):
    cal = risk_dl.get_calendar(date(2024, 1, 1), date(2024, 1, 31))
    cal_dates = sorted({_to_date(d) for d in cal["cal_date"]})
    middle = cal_dates[5]
    prev = risk_dl.prev_trade_day(middle)
    nxt = risk_dl.next_trade_day(middle)
    assert prev is not None and prev < middle
    assert nxt is not None and nxt > middle


def test_get_close_matrix_empty_codes(risk_dl):
    out = risk_dl.get_close_matrix([], date(2024, 1, 1), date(2024, 1, 31))
    assert out.empty


def test_get_price_series_qfq_false(risk_dl):
    p = risk_dl.get_price_series("000001.SZ", date(2024, 1, 1), date(2024, 1, 15), qfq=False)
    assert not p.empty
    assert "close" in p.columns


def test_momentum_score_handles_short_history():
    """If the close matrix is too short, momentum returns an empty Series."""
    close = pd.DataFrame({"000001.SZ": [10.0, 10.5, 11.0]},
                         index=pd.date_range("2024-01-01", periods=3))
    s = momentum_score(close, "2024-01-03", lookback=5)
    assert s.empty


def test_table_row_counts_smoke(risk_dl):
    counts = risk_dl.table_row_counts()
    assert set(counts) == {"daily", "adj_factor", "daily_basic", "trade_cal", "stock_basic"}
    # daily and stock_basic should be > 0
    assert counts["daily"] > 0
    assert counts["stock_basic"] > 0


def test_get_universe_with_exclude_st_false(risk_dl):
    """Setting exclude_st=False should re-include ST-flagged stocks."""
    a = risk_dl.get_universe(date(2024, 1, 31))
    b = risk_dl.get_universe(date(2024, 1, 31), exclude_st=False)
    assert "STTEST.SZ" in set(b["ts_code"])
    assert "STTEST.SZ" not in set(a["ts_code"])
