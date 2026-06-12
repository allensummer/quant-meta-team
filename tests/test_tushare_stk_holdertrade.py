"""Tests for tushare ``stk_holdertrade`` adapter + sync (v0.9 — ADM-653 Batch 3)."""
from __future__ import annotations

from datetime import date
from unittest.mock import patch, MagicMock

import pandas as pd

from quant_data.registry import register_source
from quant_data.store.duckdb_store import DuckDBStore
from quant_data.sync.driver import sync_stk_holdertrade
from tests.test_sync_idempotent import _seed_trade_cal


def test_stk_holdertrade_in_capabilities():
    from quant_data.sources.tushare import TushareAdapter
    assert "stk_holdertrade" in TushareAdapter.capabilities


def test_stk_holdertrade_dispatch_to_pro_method():
    with patch("tushare.pro_api") as pro_api:
        pro = MagicMock()
        pro_api.return_value = pro
        pro.stk_holdertrade.return_value = pd.DataFrame({
            "ts_code": ["600519.SH"],
            "ann_date": ["20240115"],
            "holder_name": ["某董事"],
            "holder_type": ["高管"],
            "in_de": ["DE"],
            "change_vol": [-1e5], "change_ratio": [-0.5],
            "after_share": [1e6], "after_ratio": [5.0],
            "avg_price": [1700.0], "total_fee": [1.7e8],
            "trade_date": ["20240110"],
        })
        from quant_data.sources.tushare import TushareAdapter
        a = TushareAdapter(pro_token="x")
        out = a.fetch("stk_holdertrade", ann_date="20240115")
        assert len(out) == 1
        assert out["in_de"].iloc[0] == "DE"


def test_stk_holdertrade_schema_registered():
    from quant_data.schemas import SCHEMAS, STK_HOLDERTRADE_V1
    assert ("stk_holdertrade", "v1") in SCHEMAS
    assert STK_HOLDERTRADE_V1.primary_key == ["ts_code", "ann_date", "holder_name", "trade_date"]


def test_stk_holdertrade_view_exists():
    db = DuckDBStore()
    db.bootstrap_views()
    rows = db.con.execute(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_type='VIEW' AND table_name='mv_stk_holdertrade_v1'"
    ).fetchall()
    assert rows
    assert db.con.execute("SELECT count(*) FROM mv_stk_holdertrade_v1").fetchone()[0] >= 0  # post-live-sync view


def test_sync_stk_holdertrade_writes_duckdb(tmp_data_dir):
    from quant_data.rate_limit import TokenBucket
    from quant_data.sources.base import LineageRecord, RateLimit

    class ShtSrc:
        name = "sht"; version = "0"; capabilities = {"stk_holdertrade"}
        def __init__(self):
            self._rl = TokenBucket(RateLimit(requests_per_min=100))
        def rate_limit(self): return RateLimit(requests_per_min=100)
        def healthcheck(self): return True
        def fetch(self, topic, **p):
            d = p["ann_date"]
            return pd.DataFrame({
                "ts_code": ["600519.SH", "600519.SH"],
                "ann_date": [pd.to_datetime(d, format="%Y%m%d").date()] * 2,
                "holder_name": ["某董事", "某监事"],
                "holder_type": ["高管", "高管"],
                "in_de": ["DE", "IN"],
                "change_vol": [-1e5, 5e4], "change_ratio": [-0.5, 0.2],
                "after_share": [1e6, 5e5], "after_ratio": [5.0, 2.5],
                "avg_price": [1700.0, 1700.0], "total_fee": [1.7e8, 8.5e7],
                "trade_date": [pd.to_datetime("20240110", format="%Y%m%d").date()] * 2,
            })
        def lineage(self, **kw):
            from datetime import datetime; import uuid
            return LineageRecord(
                table=kw.get("table", ""), schema_version=kw.get("schema_version", ""),
                source=self.name, source_version=self.version,
                fetched_at=datetime.now().astimezone(), params=kw.get("params", {}),
                rows=kw.get("rows", 0), rate_limit_hit=0, request_id=str(uuid.uuid4()),
            )

    register_source("sht", ShtSrc())
    _seed_trade_cal(tmp_data_dir, date(2024, 1, 1), date(2024, 1, 20))
    r = sync_stk_holdertrade(source="sht", start_date=date(2024, 1, 15), end_date=date(2024, 1, 15))
    assert r["rows"] == 2
    db = DuckDBStore()
    assert db.con.execute("SELECT count(*) FROM raw_sht_stk_holdertrade").fetchone()[0] == 2
