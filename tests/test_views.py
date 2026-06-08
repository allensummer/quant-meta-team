"""Pin the qfq/hfq formulas against a hand-computed reference (v0.4 §3.3)."""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from quant_data.store.duckdb_store import DuckDBStore
from quant_data.store.parquet_store import ParquetStore


def test_qfq_formula_matches_tushare_doc(tmp_data_dir):
    """qfq = close * adj_factor / latest_adj_factor per ts_code.

    Per https://tushare.pro/document/2?doc_id=28 — the canonical algorithm.
    """
    pq_d = ParquetStore(source="tushare", topic="daily")
    pq_a = ParquetStore(source="tushare", topic="adj_factor")
    d_df = pd.DataFrame({
        "ts_code": ["000001.SZ"] * 3,
        "trade_date": [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4)],
        "open": [10.0, 11.0, 12.0], "high": [10.5, 11.5, 12.5],
        "low": [9.5, 10.5, 11.5], "close": [10.0, 11.0, 12.0],
        "pre_close": [9.0, 10.0, 11.0], "change": [1.0, 1.0, 1.0],
        "pct_chg": [10.0, 10.0, 10.0], "vol": [100.0, 110.0, 120.0],
        "amount": [1000.0, 1210.0, 1440.0],
    })
    a_df = pd.DataFrame({
        "ts_code": ["000001.SZ"] * 3,
        "trade_date": [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4)],
        "adj_factor": [1.0, 1.1, 1.21],
    })
    for d_val, sub in d_df.groupby("trade_date"):
        pq_d.write(sub.reset_index(drop=True), partition_value=d_val)
    for d_val, sub in a_df.groupby("trade_date"):
        pq_a.write(sub.reset_index(drop=True), partition_value=d_val)

    db = DuckDBStore()
    db.bootstrap_views()

    qfq = db.query(
        "SELECT trade_date, close, adj_factor, close_qfq "
        "FROM mv_daily_qfq "
        "WHERE ts_code = '000001.SZ' AND close IS NOT NULL "
        "ORDER BY trade_date"
    )
    latest = a_df["adj_factor"].max()  # 1.21
    for _, row in qfq.iterrows():
        adj = a_df.loc[a_df["trade_date"] == row["trade_date"].date(), "adj_factor"].iloc[0]
        expected = row["close"] * adj / latest
        assert abs(row["close_qfq"] - expected) < 1e-9, \
            f"qfq mismatch on {row['trade_date']}: got {row['close_qfq']}, expected {expected}"


def test_hfq_formula_uses_first_adj_factor(tmp_data_dir):
    pq_d = ParquetStore(source="tushare", topic="daily")
    pq_a = ParquetStore(source="tushare", topic="adj_factor")
    d_df = pd.DataFrame({
        "ts_code": ["000001.SZ"] * 3,
        "trade_date": [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4)],
        "open": [10.0, 11.0, 12.0], "high": [10.5, 11.5, 12.5],
        "low": [9.5, 10.5, 11.5], "close": [10.0, 11.0, 12.0],
        "pre_close": [9.0, 10.0, 11.0], "change": [1.0, 1.0, 1.0],
        "pct_chg": [10.0, 10.0, 10.0], "vol": [100.0, 110.0, 120.0],
        "amount": [1000.0, 1210.0, 1440.0],
    })
    a_df = pd.DataFrame({
        "ts_code": ["000001.SZ"] * 3,
        "trade_date": [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4)],
        "adj_factor": [1.0, 1.1, 1.21],
    })
    for d_val, sub in d_df.groupby("trade_date"):
        pq_d.write(sub.reset_index(drop=True), partition_value=d_val)
    for d_val, sub in a_df.groupby("trade_date"):
        pq_a.write(sub.reset_index(drop=True), partition_value=d_val)

    db = DuckDBStore()
    db.bootstrap_views()
    hfq = db.query(
        "SELECT trade_date, close, adj_factor, close_hfq "
        "FROM mv_daily_hfq "
        "WHERE ts_code = '000001.SZ' AND close IS NOT NULL "
        "ORDER BY trade_date"
    )
    first = a_df["adj_factor"].min()  # 1.0
    for _, row in hfq.iterrows():
        adj = a_df.loc[a_df["trade_date"] == row["trade_date"].date(), "adj_factor"].iloc[0]
        expected = row["close"] * adj / first
        assert abs(row["close_hfq"] - expected) < 1e-9


def test_mv_trade_cal_filters_open_days(tmp_data_dir):
    pq = ParquetStore(source="tushare", topic="trade_cal")
    df = pd.DataFrame({
        "exchange": ["SSE", "SSE", "SSE", "SSE"],
        "cal_date": [date(2024, 1, 1), date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4)],
        "is_open": [0, 1, 1, 0],
        "pretrade_date": [date(2023, 12, 29), date(2023, 12, 29), date(2024, 1, 2), date(2024, 1, 3)],
    })
    for d_val, sub in df.groupby("cal_date"):
        pq.write(sub.reset_index(drop=True), partition_value=d_val)

    db = DuckDBStore()
    db.bootstrap_views()
    rows = db.query("SELECT cal_date FROM mv_trade_cal ORDER BY cal_date")
    # pyarrow returns DATE as datetime.date; pandas may surface as Timestamp —
    # compare by .date() in either case.
    got = [d.date() if hasattr(d, "date") else d for d in rows["cal_date"]]
    assert got == [date(2024, 1, 2), date(2024, 1, 3)]
