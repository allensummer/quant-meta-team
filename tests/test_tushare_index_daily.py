"""Tests for tushare ``index_daily`` adapter + sync (v0.9 — ADM-653 Batch 1)."""
from __future__ import annotations

from datetime import date
from unittest.mock import patch, MagicMock

import pandas as pd

from quant_data.registry import register_source
from quant_data.store.duckdb_store import DuckDBStore
from quant_data.sync.driver import sync_index_daily
from tests.test_sync_idempotent import _seed_trade_cal


def test_index_daily_in_capabilities():
    from quant_data.sources.tushare import TushareAdapter
    assert "index_daily" in TushareAdapter.capabilities


def test_index_daily_dispatch_to_pro_method():
    with patch("tushare.pro_api") as pro_api:
        pro = MagicMock()
        pro_api.return_value = pro
        pro.index_daily.return_value = pd.DataFrame({
            "ts_code": ["000300.SH"],
            "trade_date": ["20240102"],
            "open": [3500.0], "high": [3520.0], "low": [3490.0], "close": [3510.0],
            "pre_close": [3495.0], "change": [15.0], "pct_chg": [0.43],
            "vol": [1e8], "amount": [3.5e11],
        })
        from quant_data.sources.tushare import TushareAdapter
        a = TushareAdapter(pro_token="x")
        out = a.fetch("index_daily", trade_date="20240102")
        assert len(out) == 1
        assert out["ts_code"].iloc[0] == "000300.SH"
        pro.index_daily.assert_called_with(trade_date="20240102")


def test_index_daily_schema_registered():
    from quant_data.schemas import SCHEMAS, INDEX_DAILY_V1
    assert ("index_daily", "v1") in SCHEMAS
    assert INDEX_DAILY_V1.primary_key == ["ts_code", "trade_date"]


def test_index_daily_view_exists():
    db = DuckDBStore()
    db.bootstrap_views()
    rows = db.con.execute(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_type='VIEW' AND table_name='mv_index_daily_v1'"
    ).fetchall()
    assert rows
    assert db.con.execute("SELECT count(*) FROM mv_index_daily_v1").fetchone()[0] >= 0  # post-live-sync view


def test_sync_index_daily_writes_duckdb(tmp_data_dir):
    from quant_data.rate_limit import TokenBucket
    from quant_data.sources.base import LineageRecord, RateLimit

    class IdSrc:
        name = "id"; version = "0"; capabilities = {"index_daily"}
        def __init__(self):
            self._rl = TokenBucket(RateLimit(requests_per_min=100))
        def rate_limit(self): return RateLimit(requests_per_min=100)
        def healthcheck(self): return True
        def fetch(self, topic, **p):
            d = p["trade_date"]
            return pd.DataFrame({
                "ts_code": ["000300.SH", "000905.SH"],
                "trade_date": [pd.to_datetime(d, format="%Y%m%d").date()] * 2,
                "open": [3500.0, 5500.0], "high": [3520.0, 5520.0],
                "low": [3490.0, 5490.0], "close": [3510.0, 5510.0],
                "pre_close": [3495.0, 5495.0], "change": [15.0, 15.0],
                "pct_chg": [0.43, 0.27], "vol": [1e8, 5e7], "amount": [3.5e11, 2.5e11],
            })
        def lineage(self, **kw):
            from datetime import datetime; import uuid
            return LineageRecord(
                table=kw.get("table", ""), schema_version=kw.get("schema_version", ""),
                source=self.name, source_version=self.version,
                fetched_at=datetime.now().astimezone(), params=kw.get("params", {}),
                rows=kw.get("rows", 0), rate_limit_hit=0, request_id=str(uuid.uuid4()),
            )

    register_source("id", IdSrc())
    _seed_trade_cal(tmp_data_dir, date(2024, 1, 1), date(2024, 1, 5))
    # sync_index_daily iterates over (index_pool × trade_days). The mock
    # returns 2 rows per call. With 1 ts_code × 2 days × 2 rows = 4 rows total.
    r = sync_index_daily(
        source="id", start_date=date(2024, 1, 2), end_date=date(2024, 1, 3),
        index_pool=("000300.SH",),
    )
    assert r["rows"] == 4
    db = DuckDBStore()
    assert db.con.execute("SELECT count(*) FROM raw_id_index_daily").fetchone()[0] == 4
