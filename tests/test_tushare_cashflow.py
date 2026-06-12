"""Tests for tushare ``cashflow`` adapter + sync (v0.9 — ADM-653 Batch 2)."""
from __future__ import annotations

from datetime import date
from unittest.mock import patch, MagicMock

import pandas as pd

from quant_data.registry import register_source
from quant_data.store.duckdb_store import DuckDBStore
from quant_data.sync.driver import sync_cashflow


def test_cashflow_in_capabilities():
    from quant_data.sources.tushare import TushareAdapter
    assert "cashflow" in TushareAdapter.capabilities


def test_cashflow_dispatch_to_pro_method():
    with patch("tushare.pro_api") as pro_api:
        pro = MagicMock()
        pro_api.return_value = pro
        pro.cashflow.return_value = pd.DataFrame({
            "ts_code": ["600519.SH"],
            "ann_date": ["20240315"], "f_ann_date": [None],
            "end_date": ["20231231"], "report_type": ["1"],
            "net_profit": [5.3e10],
            "c_fr_sale_sg": [1.5e11], "c_paid_goods_s": [3.0e10],
            "net_cash_flows_oper": [6.0e10],
            "net_cash_flows_inv_act": [-1.0e10],
            "net_cash_flows_fin_act": [-2.0e10],
            "free_cashflow": [5.0e10],
        })
        from quant_data.sources.tushare import TushareAdapter
        a = TushareAdapter(pro_token="x")
        out = a.fetch("cashflow", period="20231231")
        assert len(out) == 1
        assert out["net_cash_flows_oper"].iloc[0] == 6.0e10


def test_cashflow_schema_registered():
    from quant_data.schemas import SCHEMAS, CASHFLOW_V1
    assert ("cashflow", "v1") in SCHEMAS
    assert CASHFLOW_V1.primary_key == ["ts_code", "end_date", "ann_date", "f_ann_date"]


def test_cashflow_view_exists():
    db = DuckDBStore()
    db.bootstrap_views()
    rows = db.con.execute(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_type='VIEW' AND table_name='mv_cashflow_v1'"
    ).fetchall()
    assert rows
    assert db.con.execute("SELECT count(*) FROM mv_cashflow_v1").fetchone()[0] >= 0  # post-live-sync view


def test_sync_cashflow_writes_duckdb(tmp_data_dir):
    from quant_data.rate_limit import TokenBucket
    from quant_data.sources.base import LineageRecord, RateLimit

    class CfSrc:
        name = "cf"; version = "0"; capabilities = {"cashflow"}
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
                "net_profit": [5.3e10],
                "net_cash_flows_oper": [6.0e10],
                "free_cashflow": [5.0e10],
            })
        def lineage(self, **kw):
            from datetime import datetime; import uuid
            return LineageRecord(
                table=kw.get("table", ""), schema_version=kw.get("schema_version", ""),
                source=self.name, source_version=self.version,
                fetched_at=datetime.now().astimezone(), params=kw.get("params", {}),
                rows=kw.get("rows", 0), rate_limit_hit=0, request_id=str(uuid.uuid4()),
            )

    register_source("cf", CfSrc())
    r = sync_cashflow(source="cf", start_date=date(2024, 1, 1), end_date=date(2024, 3, 31))
    assert r["rows"] == 1
    db = DuckDBStore()
    assert db.con.execute("SELECT count(*) FROM raw_cf_cashflow").fetchone()[0] == 1
