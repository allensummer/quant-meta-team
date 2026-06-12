"""Tests for tushare ``fina_indicator`` adapter + sync (v0.9 — ADM-653 Batch 2)."""
from __future__ import annotations

from datetime import date
from unittest.mock import patch, MagicMock

import pandas as pd

from quant_data.registry import register_source
from quant_data.store.duckdb_store import DuckDBStore
from quant_data.sync.driver import sync_fina_indicator


def test_fina_indicator_in_capabilities():
    from quant_data.sources.tushare import TushareAdapter
    assert "fina_indicator" in TushareAdapter.capabilities


def test_fina_indicator_dispatch_to_pro_method():
    with patch("tushare.pro_api") as pro_api:
        pro = MagicMock()
        pro_api.return_value = pro
        pro.fina_indicator.return_value = pd.DataFrame({
            "ts_code": ["600519.SH"],
            "end_date": ["20231231"],
            "ann_date": ["20240315"],
            "f_ann_date": ["20240315"],
            "eps": [50.0], "dt_eps": [49.0],
            "gross_margin": [85.0], "netprofit_margin": [40.0],
            "roe": [25.0], "roa": [15.0], "roic": [20.0],
            "debt_to_assets": [30.0], "current_ratio": [3.0],
        })
        from quant_data.sources.tushare import TushareAdapter
        a = TushareAdapter(pro_token="x")
        out = a.fetch("fina_indicator", period="20231231")
        assert len(out) == 1
        assert out["roe"].iloc[0] == 25.0


def test_fina_indicator_schema_registered():
    from quant_data.schemas import SCHEMAS, FINA_INDICATOR_V1
    assert ("fina_indicator", "v1") in SCHEMAS
    assert FINA_INDICATOR_V1.primary_key == ["ts_code", "end_date", "ann_date"]


def test_fina_indicator_view_exists():
    db = DuckDBStore()
    db.bootstrap_views()
    rows = db.con.execute(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_type='VIEW' AND table_name='mv_fina_indicator_v1'"
    ).fetchall()
    assert rows
    assert db.con.execute("SELECT count(*) FROM mv_fina_indicator_v1").fetchone()[0] >= 0  # post-live-sync view


def test_sync_fina_indicator_writes_duckdb(tmp_data_dir):
    from quant_data.rate_limit import TokenBucket
    from quant_data.sources.base import LineageRecord, RateLimit

    class FiSrc:
        name = "fi"; version = "0"; capabilities = {"fina_indicator"}
        def __init__(self):
            self._rl = TokenBucket(RateLimit(requests_per_min=100))
        def rate_limit(self): return RateLimit(requests_per_min=100)
        def healthcheck(self): return True
        def fetch(self, topic, **p):
            return pd.DataFrame({
                "ts_code": ["600519.SH", "000858.SZ"],
                "end_date": [pd.to_datetime("20231231", format="%Y%m%d").date()] * 2,
                "ann_date": [pd.to_datetime("20240315", format="%Y%m%d").date()] * 2,
                "f_ann_date": [None, None],
                "eps": [50.0, 5.0], "dt_eps": [49.0, 4.8],
                "gross_margin": [85.0, 60.0], "netprofit_margin": [40.0, 25.0],
                "roe": [25.0, 20.0], "roa": [15.0, 12.0], "roic": [20.0, 18.0],
                "debt_to_assets": [30.0, 40.0], "current_ratio": [3.0, 2.0],
            })
        def lineage(self, **kw):
            from datetime import datetime; import uuid
            return LineageRecord(
                table=kw.get("table", ""), schema_version=kw.get("schema_version", ""),
                source=self.name, source_version=self.version,
                fetched_at=datetime.now().astimezone(), params=kw.get("params", {}),
                rows=kw.get("rows", 0), rate_limit_hit=0, request_id=str(uuid.uuid4()),
            )

    register_source("fi", FiSrc())
    # _sync_financial_quarterly iterates over (ts_code × quarter_end).
    # The mock returns 2 rows on any topic; with 1 ts_code × 1 quarter
    # (2024-03-31) we get 2 rows. We pass ts_codes explicitly to avoid
    # the stock_basic universe-loading branch.
    from quant_data.sync.driver import _sync_financial_quarterly
    r = _sync_financial_quarterly(
        "fina_indicator", source="fi",
        start_date=date(2024, 1, 1), end_date=date(2024, 3, 31),
        ts_codes=("600519.SH",),
    )
    assert r["rows"] == 2
    db = DuckDBStore()
    assert db.con.execute("SELECT count(*) FROM raw_fi_fina_indicator").fetchone()[0] == 2
