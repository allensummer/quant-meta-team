"""Test tushare `hsgt_top10` — per-day top-10 northbound net-buy stocks.

Field set reflects the actual tushare dry-run 2026-06-12 (11 columns):
  trade_date, ts_code, name, close, change, rank, market_type,
  amount, net_amount, buy, sell
  - amount / net_amount / buy / sell in yuan (NOT 万元)
  - market_type is int (1=沪, 3=深)
"""
from __future__ import annotations

from datetime import date
from unittest.mock import patch, MagicMock

import pandas as pd

from quant_data.registry import register_source
from quant_data.schemas import SCHEMAS, HSGT_TOP10_V1
from quant_data.store.duckdb_store import DuckDBStore
from quant_data.sync.driver import sync_hsgt_top10
from tests.test_sync_idempotent import _seed_trade_cal


def test_hsgt_top10_schema_pk_and_fields():
    assert ("hsgt_top10", "v1") in SCHEMAS
    assert HSGT_TOP10_V1.primary_key == ["trade_date", "ts_code"]
    # 11 fields per actual tushare API
    assert len(HSGT_TOP10_V1.fields) == 11
    # market_type is int, not string (per dry-run 2026-06-12)
    assert HSGT_TOP10_V1.fields["market_type"].dtype == "int32"
    # amount in yuan, not wan_yuan
    assert HSGT_TOP10_V1.fields["amount"].unit == "yuan"


def test_hsgt_top10_dispatch():
    with patch("tushare.pro_api") as pro_api:
        pro = MagicMock()
        pro_api.return_value = pro
        pro.hsgt_top10.return_value = pd.DataFrame({
            "trade_date": ["20240102"] * 2,
            "ts_code": ["000001.SZ", "000002.SZ"],
            "name": ["平安银行", "万科A"],
            "close": [10.0, 8.0],
            "change": [1.5, 2.0],
            "rank": [1, 2],
            "market_type": [3, 1],
            "amount": [1.0e9, 5.0e8],
            "net_amount": [1.0e8, 5.0e7],
            "buy": [5.5e8, 2.75e8],
            "sell": [4.5e8, 2.25e8],
        })
        from quant_data.sources.tushare import TushareAdapter
        a = TushareAdapter(pro_token="x")
        out = a.fetch("hsgt_top10", trade_date="20240102")
        assert len(out) == 2
        assert out["market_type"].iloc[0] == 3
        assert out["rank"].iloc[0] == 1
        pro.hsgt_top10.assert_called_with(trade_date="20240102")


def test_hsgt_top10_view_exists():
    db = DuckDBStore()
    rows = db.con.execute(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_type='VIEW' AND table_name='mv_hsgt_top10_v1'"
    ).fetchall()
    assert rows


def test_sync_hsgt_top10_writes_duckdb(tmp_data_dir):
    from quant_data.rate_limit import TokenBucket
    from quant_data.sources.base import LineageRecord, RateLimit

    class Top10Src:
        name = "top10"; version = "0"; capabilities = {"hsgt_top10"}
        def __init__(self):
            self._rl = TokenBucket(RateLimit(requests_per_min=100))
        def rate_limit(self): return RateLimit(requests_per_min=100)
        def healthcheck(self): return True
        def fetch(self, topic, **p):
            d = p["trade_date"]
            return pd.DataFrame({
                "trade_date": [pd.to_datetime(d, format="%Y%m%d").date()] * 3,
                "ts_code": ["000001.SZ", "000002.SZ", "600519.SH"],
                "name": ["平安", "万科", "茅台"],
                "close": [10.0, 8.0, 1500.0],
                "change": [1.0, 0.5, -0.2],
                "rank": [1, 2, 3],
                "market_type": [3, 1, 1],
                "amount": [1.0e9, 5.0e8, 2.0e9],
                "net_amount": [1.0e8, 5.0e7, 2.0e8],
                "buy": [5.5e8, 2.75e8, 1.1e9],
                "sell": [4.5e8, 2.25e8, 9.0e8],
            })
        def lineage(self, **kw):
            from datetime import datetime; import uuid
            return LineageRecord(
                table=kw.get("table", ""), schema_version=kw.get("schema_version", ""),
                source=self.name, source_version=self.version,
                fetched_at=datetime.now().astimezone(), params=kw.get("params", {}),
                rows=kw.get("rows", 0), rate_limit_hit=0, request_id=str(uuid.uuid4()),
            )

    register_source("top10", Top10Src())
    _seed_trade_cal(tmp_data_dir, date(2024, 1, 1), date(2024, 1, 5))
    r = sync_hsgt_top10(source="top10",
                        start_date=date(2024, 1, 2), end_date=date(2024, 1, 3))
    # 2 days × 3 rows = 6 rows
    assert r["rows"] == 6
    assert r["topic"] == "top10_hsgt_top10"

    db = DuckDBStore()
    n = db.con.execute("SELECT count(*) FROM raw_top10_hsgt_top10").fetchone()[0]
    assert n == 6
