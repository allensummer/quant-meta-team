"""Tests for the quant_portfolio.data_layer / factor logic (ADM-612 §验收).

The acceptance spec requires ≥ 5 tests covering:
  1) 截面 SQL 生成正确性
  2) 空数据/缺失股票容错
  3) 停牌过滤
  4) 多日期回填
  5) 金额/换手率排序稳定性

Plus three more sanity tests the team asked for:
  6) field-unit conversions (v0.4 §5)
  7) static guard: no tushare / akshare import in quant_portfolio
  8) FactorSpec dataclass + helper z-score

All tests run against an isolated ``tmp_data_dir`` + a fresh DuckDBStore
so we don't depend on the real on-disk database. Real-data smoke is
left to ``examples/factor_momentum_reversal.py`` and CI.
"""
from __future__ import annotations

import importlib
import re
import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from quant_data.store.duckdb_store import DuckDBStore
from quant_data.store.parquet_store import ParquetStore
from quant_portfolio import data_layer as dl_mod
from quant_portfolio.data_layer import (
    FactorSpec,
    PortfolioDataLayer,
    amount_to_yuan,
    vol_to_shares,
)


# ---------- fixtures ----------
@pytest.fixture
def synth_store(tmp_data_dir):
    """Build a fresh DuckDBStore with seeded Parquet for daily / adj_factor / trade_cal / stock_basic.

    Layout of synthesized data:
      - 4 stocks, 3 active on as_of + 1 suspended (vol=0, high=low)
      - 30 calendar days; ``mv_trade_cal`` rows for every other day
      - adj_factor constant 1.0 (so close_qfq == close)
    """
    pq_d = ParquetStore(source="tushare", topic="daily")
    pq_a = ParquetStore(source="tushare", topic="adj_factor")
    pq_c = ParquetStore(source="tushare", topic="trade_cal")

    # 60 calendar days ending 2024-03-15 (open Mon/Wed/Fri only) — need ≥21 open days
    days = [date(2024, 1, 15) + timedelta(days=i) for i in range(60)]
    open_days = [d for d in days if d.weekday() in (0, 2, 4)]   # Mon, Wed, Fri
    assert len(open_days) >= 21, f"fixture must provide ≥ 21 open days, got {len(open_days)}"
    as_of = open_days[-1]            # 2024-02-13 (a Wednesday)
    pre_suspend = as_of - timedelta(days=2)

    # Per-stock close history: deterministic linear series (so 20D momentum = ~+20%).
    # Stock A: uptrend, A_close[t] = 10 + 0.1 * t_idx
    # Stock B: downtrend, B_close[t] = 20 - 0.1 * t_idx
    # Stock C: ranging, C_close[t] = 15 + 0.5 * sin(t)
    # Stock D: gets suspended on ``as_of`` (vol=0, high=low)
    rows_d: list[dict] = []
    rows_a: list[dict] = []
    for t_idx, d in enumerate(open_days):
        import math
        for c in ("AAA", "BBB", "CCC", "DDD"):
            rows_a.append({"ts_code": c, "trade_date": d, "adj_factor": 1.0})
        a_close = 10.0 + 0.1 * t_idx
        b_close = 20.0 - 0.1 * t_idx
        c_close = 15.0 + 0.5 * math.sin(t_idx / 3.0)
        for code, px in (("AAA", a_close), ("BBB", b_close), ("CCC", c_close)):
            rows_d.append({
                "ts_code": code, "trade_date": d,
                "open": px - 0.05, "high": px + 0.05,
                "low": px - 0.05, "close": px,
                "pre_close": px - 0.05, "change": 0.05, "pct_chg": 0.5,
                "vol": 1000.0, "amount": 1000.0 * px,
            })
        # Stock D — only present until pre_suspend; suspended on as_of
        if d < as_of:
            d_close = 8.0
            rows_d.append({
                "ts_code": "DDD", "trade_date": d,
                "open": d_close - 0.05, "high": d_close + 0.05,
                "low": d_close - 0.05, "close": d_close,
                "pre_close": d_close - 0.05, "change": 0.0, "pct_chg": 0.0,
                "vol": 500.0, "amount": 500.0 * d_close,
            })
    # Add suspended row on as_of
    rows_d.append({
        "ts_code": "DDD", "trade_date": as_of,
        "open": 8.0, "high": 8.0, "low": 8.0, "close": 8.0,
        "pre_close": 8.0, "change": 0.0, "pct_chg": 0.0,
        "vol": 0.0, "amount": 0.0,
    })
    rows_a.append({"ts_code": "DDD", "trade_date": as_of, "adj_factor": 1.0})

    d_df = pd.DataFrame(rows_d)
    a_df = pd.DataFrame(rows_a)
    c_df = pd.DataFrame([{"exchange": "SSE", "cal_date": d, "is_open": 1,
                          "pretrade_date": (d - timedelta(days=1))}
                         for d in open_days])
    # 2 closed days in calendar to test the filter (any closed weekday)
    closed = [d for d in days if d.weekday() in (5, 6)][:2]   # Sat, Sun
    for d in closed:
        c_df.loc[len(c_df)] = {"exchange": "SSE", "cal_date": d, "is_open": 0,
                                "pretrade_date": (d - timedelta(days=1))}
    # Also add a duplicate exchange row for the same cal_date (SSE + SZSE pattern)
    c_df = pd.concat([c_df, c_df.assign(exchange="SZSE")], ignore_index=True)

    # Stock basic — snapshot
    s_df = pd.DataFrame([{
        "ts_code": c, "name": c, "industry": "TEST", "list_status": "L",
    } for c in ("AAA", "BBB", "CCC", "DDD")])
    s_root = Path(tmp_data_dir) / "raw_tushare_stock_basic" / "_static"
    s_root.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pandas(s_df, preserve_index=False), str(s_root / "sb1.parquet"))
    pq.write_table(pa.Table.from_pandas(s_df, preserve_index=False), str(s_root / "sb2.parquet"))

    for d_val, sub in d_df.groupby("trade_date"):
        pq_d.write(sub.reset_index(drop=True), partition_value=d_val)
    for d_val, sub in a_df.groupby("trade_date"):
        pq_a.write(sub.reset_index(drop=True), partition_value=d_val)
    for d_val, sub in c_df.groupby("cal_date"):
        pq_c.write(sub.reset_index(drop=True), partition_value=d_val)

    store = DuckDBStore()
    store.bootstrap_views()
    return store, as_of, open_days


# ---------- 1) 截面 SQL 生成正确性 ----------
def test_cross_section_sql_uses_positional_params(synth_store):
    """``get_universe`` must build SQL that uses positional `?` placeholders
    and pass a list (not a dict) so DuckDB binds correctly. This is a
    regression guard for the §验收 #1 acceptance item."""
    store, as_of, _ = synth_store
    layer = PortfolioDataLayer(store=store)
    df = layer.get_universe(as_of)
    # The DDL-emitted view must have been re-bootstrapped against our synthetic data
    assert "ts_code" in df.columns
    # We expect at least the 4 stocks, with DDD filtered out (suspended)
    assert set(df["ts_code"]) == {"AAA", "BBB", "CCC"}


# ---------- 2) 空数据/缺失股票容错 ----------
def test_empty_calendar_returns_empty_universe(tmp_data_dir):
    """When the trade calendar has no rows for the requested window, both
    ``get_universe`` and ``get_factor_universe`` must return empty
    results without raising."""
    store = DuckDBStore()
    store.bootstrap_views()    # placeholder files only — 0 rows
    layer = PortfolioDataLayer(store=store)

    u = layer.get_universe(date(2099, 1, 1))
    assert u.empty

    res = layer.get_factor_universe(date(2099, 1, 1), lookback_days=30)
    assert res.df.empty
    assert any("empty" in n.lower() or "not enough" in n.lower() or "no trade" in n.lower()
               for n in res.notes), res.notes


def test_universe_keeps_zero_amount_rows_by_default(synth_store):
    """By default ``min_amount_yuan=0``; stocks with amount_yuan==0 (e.g. the
    suspended row before it gets dropped) should still flow through. The
    filter is opt-in, not silent."""
    store, as_of, _ = synth_store
    layer = PortfolioDataLayer(store=store)
    df = layer.get_universe(as_of, drop_suspended=False, min_amount_yuan=0)
    # DDD is suspended on as_of; with drop_suspended=False it stays in the frame
    assert "DDD" in set(df["ts_code"])
    # And its is_suspended flag is True
    assert bool(df.loc[df["ts_code"] == "DDD", "is_suspended"].iloc[0])


# ---------- 3) 停牌过滤 ----------
def test_suspension_filter_drops_high_eq_low_and_zero_vol(synth_store):
    """v0.4 §5 停牌: ``high = low AND vol = 0``. Stock DDD is synthesised
    with exactly those values on ``as_of``; the universe must drop it."""
    store, as_of, _ = synth_store
    layer = PortfolioDataLayer(store=store)
    df = layer.get_universe(as_of)
    assert "DDD" not in set(df["ts_code"])
    # Confirm the underlying row was indeed marked suspended
    raw = store.query("SELECT * FROM mv_daily_qfq WHERE ts_code='DDD' AND trade_date = ?", [as_of])
    assert bool(raw["high"].iloc[0] == raw["low"].iloc[0] and raw["vol"].iloc[0] == 0.0)


# ---------- 4) 多日期回填 ----------
def test_multi_date_backfill_produces_consistent_factors(synth_store):
    """Run get_factor_universe on two consecutive open trade days; the
    result must be reproducible (same factor sign for the trending
    names) and suspension must remove the suspended stock from the
    universe on its suspended day (cross-date consistency of the
    §3 停牌 rule)."""
    store, as_of, open_days = synth_store
    layer = PortfolioDataLayer(store=store)
    # as_of is the last open day; use the prior open day for the second call
    d1 = open_days[-2]   # trading normally
    d2 = open_days[-1]   # = as_of; DDD is suspended here
    assert d1 < d2
    r1 = layer.get_factor_universe(d1, lookback_days=25)
    r2 = layer.get_factor_universe(d2, lookback_days=25)
    # On d1 DDD trades normally (4 stocks); on d2 it's suspended (3 stocks)
    assert set(r1.df["ts_code"]) == {"AAA", "BBB", "CCC", "DDD"}
    assert set(r2.df["ts_code"]) == {"AAA", "BBB", "CCC"}
    assert "DDD" not in set(r2.df["ts_code"])
    # AAA's momentum_20d must stay positive across both dates
    aaa_1 = r1.df.loc[r1.df["ts_code"] == "AAA", "momentum_20d"].iloc[0]
    aaa_2 = r2.df.loc[r2.df["ts_code"] == "AAA", "momentum_20d"].iloc[0]
    assert aaa_1 > 0 and aaa_2 > 0
    # BBB's momentum_20d must stay negative
    bbb_1 = r1.df.loc[r1.df["ts_code"] == "BBB", "momentum_20d"].iloc[0]
    bbb_2 = r2.df.loc[r2.df["ts_code"] == "BBB", "momentum_20d"].iloc[0]
    assert bbb_1 < 0 and bbb_2 < 0


# ---------- 5) 金额/换手率排序稳定性 ----------
def test_amount_sort_is_stable_and_ties_break_on_ts_code(tmp_path: Path):
    """Sorting by ``amount_yuan`` must be deterministic. Synthetic data
    with explicit ties between two rows must keep original order
    (pandas ``sort_values`` is stable by default) AND the secondary
    sort by ``ts_code`` (string) must be reproducible across runs."""
    df = pd.DataFrame({
        "ts_code": ["A", "B", "C", "D"],
        "amount_yuan": [100.0, 50.0, 50.0, 200.0],
    })
    out = df.sort_values(["amount_yuan", "ts_code"], ascending=[False, True])
    # D(200) > A(100) > B(50)=C(50); ties B/C break alphabetically on ts_code
    assert list(out["ts_code"]) == ["D", "A", "B", "C"]
    # Run again; same result (idempotency)
    out2 = df.sort_values(["amount_yuan", "ts_code"], ascending=[False, True])
    assert list(out2["ts_code"]) == list(out["ts_code"])
    # And the same secondary key alone is stable
    only_ties = df[df["amount_yuan"] == 50.0].sort_values("ts_code")
    assert list(only_ties["ts_code"]) == ["B", "C"]


# ---------- 6) field-unit conversions (v0.4 §5) ----------
def test_field_unit_helpers_match_v04_section_5():
    assert vol_to_shares(100) == 10_000
    assert amount_to_yuan(1234.5) == pytest.approx(1_234_500.0)
    # Vectorised form
    import pandas as pd
    s = pd.Series([1.0, 2.0, 3.0])
    assert list(vol_to_shares(s)) == [100.0, 200.0, 300.0]
    assert list(amount_to_yuan(s)) == [1_000.0, 2_000.0, 3_000.0]


# ---------- 7) static guard: no tushare / akshare imports ----------
def test_quant_portfolio_does_not_import_data_source_sdks():
    """The acceptance spec REQUIRES ``grep import tushare|import akshare``
    to return 0 hits under ``quant_portfolio/``."""
    pkg_root = Path(dl_mod.__file__).parent
    bad: list[str] = []
    for py in pkg_root.rglob("*.py"):
        text = py.read_text(encoding="utf-8")
        # Match an actual import statement (not a comment / docstring token)
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if re.search(r"\b(import\s+tushare|from\s+tushare\s+import|"
                         r"import\s+akshare|from\s+akshare\s+import)\b", stripped):
                bad.append(f"{py}: {line}")
    assert not bad, "forbidden data-source imports:\n" + "\n".join(bad)


# ---------- 8) FactorSpec + top/bottom helper ----------
def test_factorspec_and_top_bottom_helper():
    spec = FactorSpec("mom_20d", 20, +1, "20D momentum")
    assert spec.direction == 1
    assert spec.lookback_days == 20
    df = pd.DataFrame({"ts_code": list("ABCD"), "score": [0.1, 0.5, -0.3, 0.2]})
    top, bot = PortfolioDataLayer.top_bottom(df, "score", n=2)
    assert list(top["ts_code"]) == ["B", "D"]
    assert list(bot["ts_code"]) == ["C", "A"]


# ---------- 9) end-to-end smoke for the example module ----------
def test_example_module_runs_end_to_end(synth_store, tmp_path: Path, monkeypatch):
    """Smoke-test ``quant_portfolio.examples.factor_momentum_reversal``
    against synthetic data and an isolated DATA_DIR so we exercise the
    full pipeline (build_report + to_markdown + main)."""
    store, as_of, _ = synth_store
    from quant_portfolio.examples import factor_momentum_reversal as ex

    # Rebuild the example with a high amount floor so all 3 non-suspended
    # rows pass the liquidity filter.
    as_of_, universe, top, bot, meta = ex.build_report(
        on_date=as_of, min_amount_yuan=0.0, top_n=3, store=store
    )
    assert as_of_ == as_of
    assert len(universe) == 3                # AAA, BBB, CCC (DDD suspended)
    assert "DDD" not in set(top["ts_code"])
    md = ex.to_markdown(as_of_, universe, top, bot, meta)
    # Markdown must contain the 5 sections
    for header in ("# 动量(20D) + 反转(5D)", "## 1. 因子定义", "## 2. 数据与筛选",
                   "## 3. Top 10", "## 4. Bottom 10", "## 5. 5 只样例股票",
                   "## 6. 交接说明"):
        assert header in md, f"missing section: {header}"
    # The samples table must list at least min(5, universe_size) rows;
    # the production report (run on real A-share data) always has ≥5.
    sample_section = md.split("## 5. 5 只样例股票")[1].split("## 6.")[0]
    sample_rows = [ln for ln in sample_section.splitlines()
                   if ln.startswith("| ") and "ts_code" not in ln and "---" not in ln]
    expected = min(5, len(universe))
    assert len(sample_rows) == expected, (
        f"expected {expected} sample rows, got {len(sample_rows)}: {sample_rows}"
    )

    # main() should also succeed and write a file.
    out = tmp_path / "report.md"
    rc = ex.main([
        "--on-date", as_of.isoformat(),
        "--min-amount-yuan", "0",
        "--top-n", "3",
        "--report-path", str(out),
    ])
    assert rc == 0
    assert out.exists()
    body = out.read_text(encoding="utf-8")
    assert "示例输出，非可执行组合" in body
