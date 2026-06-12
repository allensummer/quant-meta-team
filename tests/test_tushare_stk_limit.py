"""Tests for tushare ``stk_limit`` adapter + sync (v0.9 — ADM-653 Batch 1)."""
from __future__ import annotations

from datetime import date
from unittest.mock import patch, MagicMock

import pandas as pd

from quant_data.registry import register_source
from quant_data.store.duckdb_store import DuckDBStore
from quant_data.sync.driver import sync_stk_limit
from tests.test_sync_idempotent import _seed_trade_cal


def test_stk_limit_in_capabilities():
    from quant_data.sources.tushare import TushareAdapter
    assert "stk_limit" in TushareAdapter.capabilities


def test_stk_limit_dispatch_to_pro_method():
    with patch("tushare.pro_api") as pro_api:
        pro = MagicMock()
        pro_api.return_value = pro
        pro.stk_limit.return_value = pd.DataFrame({
            "ts_code": ["000001.SZ"],
            "trade_date": ["20240102"],
            "up_limit": [11.55], "down_limit": [9.45],
        })
        from quant_data.sources.tushare import TushareAdapter
        a = TushareAdapter(pro_token="x")
        out = a.fetch("stk_limit", trade_date="20240102")
        assert len(out) == 1
        assert out["up_limit"].iloc[0] == 11.55


def test_stk_limit_schema_registered():
    from quant_data.schemas import SCHEMAS, STK_LIMIT_V1
    assert ("stk_limit", "v1") in SCHEMAS
    assert STK_LIMIT_V1.primary_key == ["ts_code", "trade_date"]


def test_stk_limit_view_exists():
    db = DuckDBStore()
    db.bootstrap_views()
    rows = db.con.execute(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_type='VIEW' AND table_name='mv_stk_limit_v1'"
    ).fetchall()
    assert rows
    assert db.con.execute("SELECT count(*) FROM mv_stk_limit_v1").fetchone()[0] >= 0  # post-live-sync view


def test_sync_stk_limit_writes_duckdb(tmp_data_dir):
    from quant_data.rate_limit import TokenBucket
    from quant_data.sources.base import LineageRecord, RateLimit

    class SlSrc:
        name = "sl"; version = "0"; capabilities = {"stk_limit"}
        def __init__(self):
            self._rl = TokenBucket(RateLimit(requests_per_min=100))
        def rate_limit(self): return RateLimit(requests_per_min=100)
        def healthcheck(self): return True
        def fetch(self, topic, **p):
            d = p["trade_date"]
            return pd.DataFrame({
                "ts_code": ["000001.SZ", "600519.SH"],
                "trade_date": [pd.to_datetime(d, format="%Y%m%d").date()] * 2,
                "up_limit": [11.55, 1900.0], "down_limit": [9.45, 1550.0],
            })
        def lineage(self, **kw):
            from datetime import datetime; import uuid
            return LineageRecord(
                table=kw.get("table", ""), schema_version=kw.get("schema_version", ""),
                source=self.name, source_version=self.version,
                fetched_at=datetime.now().astimezone(), params=kw.get("params", {}),
                rows=kw.get("rows", 0), rate_limit_hit=0, request_id=str(uuid.uuid4()),
            )

    register_source("sl", SlSrc())
    _seed_trade_cal(tmp_data_dir, date(2024, 1, 1), date(2024, 1, 3))
    r = sync_stk_limit(source="sl", start_date=date(2024, 1, 2), end_date=date(2024, 1, 2))
    assert r["rows"] == 2
    db = DuckDBStore()
    assert db.con.execute("SELECT count(*) FROM raw_sl_stk_limit").fetchone()[0] == 2
