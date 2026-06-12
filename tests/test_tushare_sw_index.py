"""Tests for tushare ``sw_index`` adapter + sync (v0.9 — ADM-653 Batch 1)."""
from __future__ import annotations

from datetime import date
from unittest.mock import patch, MagicMock

import pandas as pd
import pytest

from quant_data.registry import register_source
from quant_data.store.duckdb_store import DuckDBStore
from quant_data.sync.driver import sync_sw_index
from tests.test_sync_idempotent import _seed_trade_cal


def test_sw_index_in_capabilities():
    from quant_data.sources.tushare import TushareAdapter
    assert "sw_index" in TushareAdapter.capabilities


def test_sw_index_dispatch_to_pro_method():
    """``sw_index`` is tier-blocked on 2000 积分档 (need ≥5000). Verify the
    adapter raises a clear RuntimeError rather than wasting a network call.
    """
    from quant_data.sources.tushare import TushareAdapter
    a = TushareAdapter(pro_token="x", tier=2000)
    with pytest.raises(RuntimeError, match="tier-blocked"):
        a.fetch("sw_index", trade_date="20240102", level=1)


def test_sw_index_dispatch_works_on_higher_tier():
    """If we ever upgrade to ≥5000 积分档, the adapter should call pro.sw_index."""
    with patch("tushare.pro_api") as pro_api:
        pro = MagicMock()
        pro_api.return_value = pro
        pro.sw_index.return_value = pd.DataFrame({
            "index_code": ["801010.SI"],
            "index_name": ["农林牧渔"],
            "level": [1],
            "trade_date": ["20240102"],
            "open": [3000.0], "high": [3020.0], "low": [2990.0], "close": [3010.0],
            "change": [10.0], "pct_chg": [0.33], "vol": [1e7], "amount": [3e9],
        })
        from quant_data.sources.tushare import TushareAdapter
        a = TushareAdapter(pro_token="x", tier=5000)
        out = a.fetch("sw_index", trade_date="20240102", level=1)
        assert len(out) == 1
        assert out["index_code"].iloc[0] == "801010.SI"


def test_sw_index_schema_registered():
    from quant_data.schemas import SCHEMAS, SW_INDEX_V1
    assert ("sw_index", "v1") in SCHEMAS
    assert SW_INDEX_V1.primary_key == ["index_code", "trade_date"]


def test_sw_index_view_exists():
    db = DuckDBStore()
    db.bootstrap_views()
    rows = db.con.execute(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_type='VIEW' AND table_name='mv_sw_index_v1'"
    ).fetchall()
    assert rows
    assert db.con.execute("SELECT count(*) FROM mv_sw_index_v1").fetchone()[0] >= 0  # post-live-sync view


def test_sync_sw_index_writes_duckdb(tmp_data_dir):
    from quant_data.rate_limit import TokenBucket
    from quant_data.sources.base import LineageRecord, RateLimit

    class SwSrc:
        name = "sw"; version = "0"; capabilities = {"sw_index"}
        def __init__(self):
            self._rl = TokenBucket(RateLimit(requests_per_min=100))
        def rate_limit(self): return RateLimit(requests_per_min=100)
        def healthcheck(self): return True
        def fetch(self, topic, **p):
            d = p["trade_date"]
            return pd.DataFrame({
                "index_code": ["801010.SI", "801020.SI"],
                "index_name": ["农林牧渔", "基础化工"],
                "level": [1, 1],
                "trade_date": [pd.to_datetime(d, format="%Y%m%d").date()] * 2,
                "open": [3000.0, 4000.0], "high": [3020.0, 4020.0],
                "low": [2990.0, 3990.0], "close": [3010.0, 4010.0],
                "change": [10.0, 10.0], "pct_chg": [0.33, 0.25],
                "vol": [1e7, 2e7], "amount": [3e9, 8e9],
            })
        def lineage(self, **kw):
            from datetime import datetime; import uuid
            return LineageRecord(
                table=kw.get("table", ""), schema_version=kw.get("schema_version", ""),
                source=self.name, source_version=self.version,
                fetched_at=datetime.now().astimezone(), params=kw.get("params", {}),
                rows=kw.get("rows", 0), rate_limit_hit=0, request_id=str(uuid.uuid4()),
            )

    register_source("sw", SwSrc())
    _seed_trade_cal(tmp_data_dir, date(2024, 1, 1), date(2024, 1, 3))
    r = sync_sw_index(source="sw", start_date=date(2024, 1, 2), end_date=date(2024, 1, 2), level=1)
    assert r["rows"] == 2
    db = DuckDBStore()
    assert db.con.execute("SELECT count(*) FROM raw_sw_sw_index").fetchone()[0] == 2
