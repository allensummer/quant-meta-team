"""Tests for tushare ``income`` adapter + sync (v0.9 — ADM-653 Batch 2)."""
from __future__ import annotations

from datetime import date
from unittest.mock import patch, MagicMock

import pandas as pd

from quant_data.registry import register_source
from quant_data.store.duckdb_store import DuckDBStore
from quant_data.sync.driver import sync_income


def test_income_in_capabilities():
    from quant_data.sources.tushare import TushareAdapter
    assert "income" in TushareAdapter.capabilities


def test_income_dispatch_to_pro_method():
    with patch("tushare.pro_api") as pro_api:
        pro = MagicMock()
        pro_api.return_value = pro
        pro.income.return_value = pd.DataFrame({
            "ts_code": ["600519.SH"],
            "ann_date": ["20240315"],
            "f_ann_date": ["20240315"],
            "end_date": ["20231231"],
            "report_type": ["1"],
            "basic_eps": [50.0], "diluted_eps": [50.0],
            "total_revenue": [1.5e11], "revenue": [1.5e11],
            "total_cogs": [3.0e10], "oper_cost": [2.0e10],
            "operate_profit": [7.0e10], "total_profit": [7.0e10],
            "income_tax": [1.7e10], "n_income": [5.3e10], "n_income_attr_p": [5.3e10],
        })
        from quant_data.sources.tushare import TushareAdapter
        a = TushareAdapter(pro_token="x")
        out = a.fetch("income", period="20231231")
        assert len(out) == 1
        assert out["n_income"].iloc[0] == 5.3e10


def test_income_schema_registered():
    from quant_data.schemas import SCHEMAS, INCOME_V1
    assert ("income", "v1") in SCHEMAS
    assert INCOME_V1.primary_key == ["ts_code", "end_date", "ann_date", "f_ann_date"]


def test_income_view_exists():
    db = DuckDBStore()
    db.bootstrap_views()
    rows = db.con.execute(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_type='VIEW' AND table_name='mv_income_v1'"
    ).fetchall()
    assert rows
    assert db.con.execute("SELECT count(*) FROM mv_income_v1").fetchone()[0] >= 0  # post-live-sync view


def test_sync_income_writes_duckdb(tmp_data_dir):
    from quant_data.rate_limit import TokenBucket
    from quant_data.sources.base import LineageRecord, RateLimit

    class IncSrc:
        name = "inc"; version = "0"; capabilities = {"income"}
        def __init__(self):
            self._rl = TokenBucket(RateLimit(requests_per_min=100))
        def rate_limit(self): return RateLimit(requests_per_min=100)
        def healthcheck(self): return True
        def fetch(self, topic, **p):
            return pd.DataFrame({
                "ts_code": ["600519.SH"],
                "ann_date": [pd.to_datetime("20240315", format="%Y%m%d").date()],
                "f_ann_date": [None],
                "end_date": [pd.to_datetime("20231231", format="%Y%m%d").date()],
                "report_type": ["1"],
                "basic_eps": [50.0], "diluted_eps": [50.0],
                "total_revenue": [1.5e11], "revenue": [1.5e11],
                "operate_profit": [7.0e10], "total_profit": [7.0e10],
                "income_tax": [1.7e10], "n_income": [5.3e10], "n_income_attr_p": [5.3e10],
            })
        def lineage(self, **kw):
            from datetime import datetime; import uuid
            return LineageRecord(
                table=kw.get("table", ""), schema_version=kw.get("schema_version", ""),
                source=self.name, source_version=self.version,
                fetched_at=datetime.now().astimezone(), params=kw.get("params", {}),
                rows=kw.get("rows", 0), rate_limit_hit=0, request_id=str(uuid.uuid4()),
            )

    register_source("inc", IncSrc())
    r = sync_income(source="inc", start_date=date(2024, 1, 1), end_date=date(2024, 3, 31))
    assert r["rows"] == 1
    db = DuckDBStore()
    assert db.con.execute("SELECT count(*) FROM raw_inc_income").fetchone()[0] == 1
