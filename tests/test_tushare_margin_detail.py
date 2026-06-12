"""Tests for tushare ``margin_detail`` adapter + sync (v0.9 — ADM-653 Batch 3)."""
from __future__ import annotations

from datetime import date
from unittest.mock import patch, MagicMock

import pandas as pd

from quant_data.registry import register_source
from quant_data.store.duckdb_store import DuckDBStore
from quant_data.sync.driver import sync_margin_detail
from tests.test_sync_idempotent import _seed_trade_cal


def test_margin_detail_in_capabilities():
    from quant_data.sources.tushare import TushareAdapter
    assert "margin_detail" in TushareAdapter.capabilities


def test_margin_detail_dispatch_to_pro_method():
    with patch("tushare.pro_api") as pro_api:
        pro = MagicMock()
        pro_api.return_value = pro
        pro.margin_detail.return_value = pd.DataFrame({
            "trade_date": ["20240102"],
            "ts_code": ["000001.SZ"],
            "rzye": [1e9], "rqye": [1e8], "rzmre": [5e7],
            "rqyl": [1e6], "rzche": [4e7], "rqchl": [1e5],
            "rqmcl": [5e4], "rzrqye": [1.1e9],
        })
        from quant_data.sources.tushare import TushareAdapter
        a = TushareAdapter(pro_token="x")
        out = a.fetch("margin_detail", trade_date="20240102")
        assert len(out) == 1
        assert out["rzye"].iloc[0] == 1e9


def test_margin_detail_schema_registered():
    from quant_data.schemas import SCHEMAS, MARGIN_DETAIL_V1
    assert ("margin_detail", "v1") in SCHEMAS
    assert MARGIN_DETAIL_V1.primary_key == ["trade_date", "ts_code"]


def test_margin_detail_view_exists():
    db = DuckDBStore()
    db.bootstrap_views()
    rows = db.con.execute(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_type='VIEW' AND table_name='mv_margin_detail_v1'"
    ).fetchall()
    assert rows
    assert db.con.execute("SELECT count(*) FROM mv_margin_detail_v1").fetchone()[0] >= 0  # post-live-sync view


def test_sync_margin_detail_writes_duckdb(tmp_data_dir):
    from quant_data.rate_limit import TokenBucket
    from quant_data.sources.base import LineageRecord, RateLimit

    class MdSrc:
        name = "md"; version = "0"; capabilities = {"margin_detail"}
        def __init__(self):
            self._rl = TokenBucket(RateLimit(requests_per_min=100))
        def rate_limit(self): return RateLimit(requests_per_min=100)
        def healthcheck(self): return True
        def fetch(self, topic, **p):
            d = p["trade_date"]
            return pd.DataFrame({
                "trade_date": [pd.to_datetime(d, format="%Y%m%d").date()] * 2,
                "ts_code": ["000001.SZ", "600519.SH"],
                "rzye": [1e9, 5e8], "rqye": [1e8, 5e7],
                "rzmre": [5e7, 2e7], "rqyl": [1e6, 5e5],
                "rzche": [4e7, 2e7], "rqchl": [1e5, 5e4],
                "rqmcl": [5e4, 2e4], "rzrqye": [1.1e9, 5.5e8],
            })
        def lineage(self, **kw):
            from datetime import datetime; import uuid
            return LineageRecord(
                table=kw.get("table", ""), schema_version=kw.get("schema_version", ""),
                source=self.name, source_version=self.version,
                fetched_at=datetime.now().astimezone(), params=kw.get("params", {}),
                rows=kw.get("rows", 0), rate_limit_hit=0, request_id=str(uuid.uuid4()),
            )

    register_source("md", MdSrc())
    _seed_trade_cal(tmp_data_dir, date(2024, 1, 1), date(2024, 1, 3))
    r = sync_margin_detail(source="md", start_date=date(2024, 1, 2), end_date=date(2024, 1, 2))
    assert r["rows"] == 2
    db = DuckDBStore()
    assert db.con.execute("SELECT count(*) FROM raw_md_margin_detail").fetchone()[0] == 2
