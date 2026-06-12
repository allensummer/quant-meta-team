"""Tests for tushare ``top_list`` adapter + sync (v0.9 — ADM-653 Batch 3)."""
from __future__ import annotations

from datetime import date
from unittest.mock import patch, MagicMock

import pandas as pd

from quant_data.registry import register_source
from quant_data.store.duckdb_store import DuckDBStore
from quant_data.sync.driver import sync_top_list
from tests.test_sync_idempotent import _seed_trade_cal


def test_top_list_in_capabilities():
    from quant_data.sources.tushare import TushareAdapter
    assert "top_list" in TushareAdapter.capabilities


def test_top_list_dispatch_to_pro_method():
    with patch("tushare.pro_api") as pro_api:
        pro = MagicMock()
        pro_api.return_value = pro
        pro.top_list.return_value = pd.DataFrame({
            "trade_date": ["20240102"],
            "ts_code": ["000001.SZ"],
            "name": ["平安银行"],
            "close": [10.0], "pct_change": [10.0],
            "turnover_rate": [5.0], "amount": [1e9],
            "l_sell": [5e8], "l_buy": [6e8], "l_amount": [1.1e9],
            "net_amount": [1e8], "net_rate": [10.0], "amount_rate": [50.0],
            "float_values": [1e11], "reason": ["日涨幅偏离值达7%"],
            "net_buy_amount": [1e8], "sell_amount": [5e8], "buy_amount": [6e8],
        })
        from quant_data.sources.tushare import TushareAdapter
        a = TushareAdapter(pro_token="x")
        out = a.fetch("top_list", trade_date="20240102")
        assert len(out) == 1
        assert out["net_amount"].iloc[0] == 1e8


def test_top_list_schema_registered():
    from quant_data.schemas import SCHEMAS, TOP_LIST_V1
    assert ("top_list", "v1") in SCHEMAS
    assert TOP_LIST_V1.primary_key == ["trade_date", "ts_code"]


def test_top_list_view_exists():
    db = DuckDBStore()
    db.bootstrap_views()
    rows = db.con.execute(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_type='VIEW' AND table_name='mv_top_list_v1'"
    ).fetchall()
    assert rows
    assert db.con.execute("SELECT count(*) FROM mv_top_list_v1").fetchone()[0] >= 0  # post-live-sync view has rows; pre-sync = 0


def test_sync_top_list_writes_duckdb(tmp_data_dir):
    from quant_data.rate_limit import TokenBucket
    from quant_data.sources.base import LineageRecord, RateLimit

    class TlSrc:
        name = "tl"; version = "0"; capabilities = {"top_list"}
        def __init__(self):
            self._rl = TokenBucket(RateLimit(requests_per_min=100))
        def rate_limit(self): return RateLimit(requests_per_min=100)
        def healthcheck(self): return True
        def fetch(self, topic, **p):
            d = p["trade_date"]
            return pd.DataFrame({
                "trade_date": [pd.to_datetime(d, format="%Y%m%d").date()] * 2,
                "ts_code": ["000001.SZ", "600519.SH"],
                "name": ["平安银行", "贵州茅台"],
                "close": [10.0, 1700.0],
                "pct_change": [10.0, -5.0],
                "turnover_rate": [5.0, 0.5],
                "amount": [1e9, 5e8],
                "net_amount": [1e8, -5e7], "net_rate": [10.0, -10.0],
            })
        def lineage(self, **kw):
            from datetime import datetime; import uuid
            return LineageRecord(
                table=kw.get("table", ""), schema_version=kw.get("schema_version", ""),
                source=self.name, source_version=self.version,
                fetched_at=datetime.now().astimezone(), params=kw.get("params", {}),
                rows=kw.get("rows", 0), rate_limit_hit=0, request_id=str(uuid.uuid4()),
            )

    register_source("tl", TlSrc())
    _seed_trade_cal(tmp_data_dir, date(2024, 1, 1), date(2024, 1, 3))
    r = sync_top_list(source="tl", start_date=date(2024, 1, 2), end_date=date(2024, 1, 2))
    assert r["rows"] == 2
    db = DuckDBStore()
    assert db.con.execute("SELECT count(*) FROM raw_tl_top_list").fetchone()[0] == 2
