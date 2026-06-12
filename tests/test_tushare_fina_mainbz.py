"""Tests for tushare ``fina_mainbz`` adapter + sync (v0.9 — ADM-653 Batch 2)."""
from __future__ import annotations

from datetime import date
from unittest.mock import patch, MagicMock

import pandas as pd

from quant_data.registry import register_source
from quant_data.store.duckdb_store import DuckDBStore
from quant_data.sync.driver import sync_fina_mainbz


def test_fina_mainbz_in_capabilities():
    from quant_data.sources.tushare import TushareAdapter
    assert "fina_mainbz" in TushareAdapter.capabilities


def test_fina_mainbz_dispatch_to_pro_method():
    with patch("tushare.pro_api") as pro_api:
        pro = MagicMock()
        pro_api.return_value = pro
        pro.fina_mainbz.return_value = pd.DataFrame({
            "ts_code": ["600519.SH"],
            "end_date": ["20231231"],
            "bz_item": ["白酒"],
            "bz_code": ["C15"],
            "bz_sales": [1.5e11], "bz_profit": [6.0e10], "bz_cost": [3.0e10],
            "curr_type": ["CNY"],
        })
        from quant_data.sources.tushare import TushareAdapter
        a = TushareAdapter(pro_token="x")
        out = a.fetch("fina_mainbz", period="20231231")
        assert len(out) == 1
        assert out["bz_item"].iloc[0] == "白酒"


def test_fina_mainbz_schema_registered():
    from quant_data.schemas import SCHEMAS, FINA_MAINBZ_V1
    assert ("fina_mainbz", "v1") in SCHEMAS
    assert FINA_MAINBZ_V1.primary_key == ["ts_code", "end_date", "bz_item"]


def test_fina_mainbz_view_exists():
    db = DuckDBStore()
    db.bootstrap_views()
    rows = db.con.execute(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_type='VIEW' AND table_name='mv_fina_mainbz_v1'"
    ).fetchall()
    assert rows
    assert db.con.execute("SELECT count(*) FROM mv_fina_mainbz_v1").fetchone()[0] >= 0  # post-live-sync view


def test_sync_fina_mainbz_writes_duckdb(tmp_data_dir):
    from quant_data.rate_limit import TokenBucket
    from quant_data.sources.base import LineageRecord, RateLimit

    class MbSrc:
        name = "mb"; version = "0"; capabilities = {"fina_mainbz"}
        def __init__(self):
            self._rl = TokenBucket(RateLimit(requests_per_min=100))
        def rate_limit(self): return RateLimit(requests_per_min=100)
        def healthcheck(self): return True
        def fetch(self, topic, **p):
            return pd.DataFrame({
                "ts_code": ["600519.SH", "600519.SH"],
                "end_date": [pd.to_datetime("20231231", format="%Y%m%d").date()] * 2,
                "bz_item": ["白酒", "包装"],
                "bz_code": ["C15", "C17"],
                "bz_sales": [1.5e11, 1.0e10],
                "bz_profit": [6.0e10, 4.0e9],
                "bz_cost": [3.0e10, 5.0e9],
                "curr_type": ["CNY", "CNY"],
            })
        def lineage(self, **kw):
            from datetime import datetime; import uuid
            return LineageRecord(
                table=kw.get("table", ""), schema_version=kw.get("schema_version", ""),
                source=self.name, source_version=self.version,
                fetched_at=datetime.now().astimezone(), params=kw.get("params", {}),
                rows=kw.get("rows", 0), rate_limit_hit=0, request_id=str(uuid.uuid4()),
            )

    register_source("mb", MbSrc())
    from quant_data.sync.driver import _sync_financial_quarterly
    r = _sync_financial_quarterly(
        "fina_mainbz", source="mb",
        start_date=date(2024, 1, 1), end_date=date(2024, 3, 31),
        ts_codes=("600519.SH",),
    )
    assert r["rows"] == 2
    db = DuckDBStore()
    assert db.con.execute("SELECT count(*) FROM raw_mb_fina_mainbz").fetchone()[0] == 2
