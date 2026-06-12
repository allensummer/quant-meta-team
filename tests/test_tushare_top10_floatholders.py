"""Tests for tushare ``top10_floatholders`` adapter + sync (v0.9 — ADM-653 Batch 3)."""
from __future__ import annotations

from datetime import date
from unittest.mock import patch, MagicMock

import pandas as pd

from quant_data.registry import register_source
from quant_data.store.duckdb_store import DuckDBStore
from quant_data.sync.driver import sync_top10_floatholders


def test_top10_floatholders_in_capabilities():
    from quant_data.sources.tushare import TushareAdapter
    assert "top10_floatholders" in TushareAdapter.capabilities


def test_top10_floatholders_dispatch_to_pro_method():
    with patch("tushare.pro_api") as pro_api:
        pro = MagicMock()
        pro_api.return_value = pro
        pro.top10_floatholders.return_value = pd.DataFrame({
            "ts_code": ["600519.SH"],
            "ann_date": ["20240315"],
            "end_date": ["20231231"],
            "holder_name": ["香港中央结算"],
            "hold_amount": [1.0e8], "hold_ratio": [8.0],
            "hold_float_ratio": [9.0], "hold_change": [5.0e6],
            "holder_type": ["外资"],
        })
        from quant_data.sources.tushare import TushareAdapter
        a = TushareAdapter(pro_token="x")
        out = a.fetch("top10_floatholders", period="20231231")
        assert len(out) == 1
        assert out["holder_name"].iloc[0] == "香港中央结算"


def test_top10_floatholders_schema_registered():
    from quant_data.schemas import SCHEMAS, TOP10_FLOATHOLDERS_V1
    assert ("top10_floatholders", "v1") in SCHEMAS
    assert TOP10_FLOATHOLDERS_V1.primary_key == ["ts_code", "ann_date", "end_date", "holder_name"]


def test_top10_floatholders_view_exists():
    db = DuckDBStore()
    db.bootstrap_views()
    rows = db.con.execute(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_type='VIEW' AND table_name='mv_top10_floatholders_v1'"
    ).fetchall()
    assert rows
    assert db.con.execute("SELECT count(*) FROM mv_top10_floatholders_v1").fetchone()[0] >= 0  # post-live-sync view


def test_sync_top10_floatholders_writes_duckdb(tmp_data_dir):
    from quant_data.rate_limit import TokenBucket
    from quant_data.sources.base import LineageRecord, RateLimit

    class TfhSrc:
        name = "tfh"; version = "0"; capabilities = {"top10_floatholders"}
        def __init__(self):
            self._rl = TokenBucket(RateLimit(requests_per_min=100))
        def rate_limit(self): return RateLimit(requests_per_min=100)
        def healthcheck(self): return True
        def fetch(self, topic, **p):
            return pd.DataFrame({
                "ts_code": ["600519.SH", "600519.SH"],
                "ann_date": [pd.to_datetime("20240315", format="%Y%m%d").date()] * 2,
                "end_date": [pd.to_datetime("20231231", format="%Y%m%d").date()] * 2,
                "holder_name": ["香港中央结算", "易方达"],
                "hold_amount": [1.0e8, 5.0e7], "hold_ratio": [8.0, 4.0],
                "hold_float_ratio": [9.0, 4.5], "hold_change": [5.0e6, 1.0e6],
                "holder_type": ["外资", "公募"],
            })
        def lineage(self, **kw):
            from datetime import datetime; import uuid
            return LineageRecord(
                table=kw.get("table", ""), schema_version=kw.get("schema_version", ""),
                source=self.name, source_version=self.version,
                fetched_at=datetime.now().astimezone(), params=kw.get("params", {}),
                rows=kw.get("rows", 0), rate_limit_hit=0, request_id=str(uuid.uuid4()),
            )

    register_source("tfh", TfhSrc())
    # top10_floatholders uses period-only (no ts_code). Just call the sync.
    r = sync_top10_floatholders(source="tfh", start_date=date(2024, 1, 1), end_date=date(2024, 3, 31))
    assert r["rows"] == 2
    db = DuckDBStore()
    assert db.con.execute("SELECT count(*) FROM raw_tfh_top10_floatholders").fetchone()[0] == 2
