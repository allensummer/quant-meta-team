"""Tests for tushare ``dividend`` adapter + sync (v0.9 — ADM-653 Batch 1)."""
from __future__ import annotations

from datetime import date
from unittest.mock import patch, MagicMock

import pandas as pd

from quant_data.registry import register_source
from quant_data.store.duckdb_store import DuckDBStore
from quant_data.sync.driver import sync_dividend
from tests.test_sync_idempotent import _seed_trade_cal


def test_dividend_in_capabilities():
    from quant_data.sources.tushare import TushareAdapter
    assert "dividend" in TushareAdapter.capabilities


def test_dividend_dispatch_to_pro_method():
    with patch("tushare.pro_api") as pro_api:
        pro = MagicMock()
        pro_api.return_value = pro
        pro.dividend.return_value = pd.DataFrame({
            "ts_code": ["600519.SH"],
            "end_date": ["20231231"],
            "ann_date": ["20240315"],
            "div_proc": ["实施"],
            "stk_div": [0.0], "stk_bo_rate": [0.0],
            "cash_div": [30.88], "cash_div_tax": [26.25],
            "record_date": ["20240620"], "ex_date": ["20240621"],
            "pay_date": ["20240621"],
        })
        from quant_data.sources.tushare import TushareAdapter
        a = TushareAdapter(pro_token="x")
        out = a.fetch("dividend", ann_date="20240315")
        assert len(out) == 1
        assert out["cash_div"].iloc[0] == 30.88


def test_dividend_schema_registered():
    from quant_data.schemas import SCHEMAS, DIVIDEND_V1
    assert ("dividend", "v1") in SCHEMAS
    assert DIVIDEND_V1.primary_key == ["ts_code", "end_date"]


def test_dividend_view_exists():
    db = DuckDBStore()
    db.bootstrap_views()
    rows = db.con.execute(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_type='VIEW' AND table_name='mv_dividend_v1'"
    ).fetchall()
    assert rows
    assert db.con.execute("SELECT count(*) FROM mv_dividend_v1").fetchone()[0] >= 0  # post-live-sync view


def test_sync_dividend_writes_duckdb(tmp_data_dir):
    from quant_data.rate_limit import TokenBucket
    from quant_data.sources.base import LineageRecord, RateLimit

    class DvSrc:
        name = "dv"; version = "0"; capabilities = {"dividend"}
        def __init__(self):
            self._rl = TokenBucket(RateLimit(requests_per_min=100))
        def rate_limit(self): return RateLimit(requests_per_min=100)
        def healthcheck(self): return True
        def fetch(self, topic, **p):
            d = p["ann_date"]
            return pd.DataFrame({
                "ts_code": ["600519.SH", "000858.SZ"],
                "end_date": [pd.to_datetime("20231231", format="%Y%m%d").date()] * 2,
                "ann_date": [pd.to_datetime(d, format="%Y%m%d").date()] * 2,
                "div_proc": ["实施", "实施"],
                "stk_div": [0.0, 0.0], "stk_bo_rate": [0.0, 0.0],
                "cash_div": [30.88, 5.0], "cash_div_tax": [26.25, 4.25],
                "record_date": [None, None], "ex_date": [None, None],
                "pay_date": [None, None],
            })
        def lineage(self, **kw):
            from datetime import datetime; import uuid
            return LineageRecord(
                table=kw.get("table", ""), schema_version=kw.get("schema_version", ""),
                source=self.name, source_version=self.version,
                fetched_at=datetime.now().astimezone(), params=kw.get("params", {}),
                rows=kw.get("rows", 0), rate_limit_hit=0, request_id=str(uuid.uuid4()),
            )

    register_source("dv", DvSrc())
    _seed_trade_cal(tmp_data_dir, date(2024, 3, 1), date(2024, 3, 20))
    r = sync_dividend(source="dv", start_date=date(2024, 3, 15), end_date=date(2024, 3, 15))
    assert r["rows"] == 2
    db = DuckDBStore()
    assert db.con.execute("SELECT count(*) FROM raw_dv_dividend").fetchone()[0] == 2
