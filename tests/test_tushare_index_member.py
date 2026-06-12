"""Tests for tushare ``index_member`` adapter + sync (v0.9 — ADM-653 Batch 1)."""
from __future__ import annotations

from datetime import date
from unittest.mock import patch, MagicMock

import pandas as pd

from quant_data.registry import register_source
from quant_data.store.duckdb_store import DuckDBStore
from quant_data.sync.driver import sync_index_member
from tests.test_sync_idempotent import _seed_trade_cal


def test_index_member_in_capabilities():
    from quant_data.sources.tushare import TushareAdapter
    assert "index_member" in TushareAdapter.capabilities


def test_index_member_dispatch_to_pro_method():
    with patch("tushare.pro_api") as pro_api:
        pro = MagicMock()
        pro_api.return_value = pro
        pro.index_member.return_value = pd.DataFrame({
            "index_code": ["000300.SH"],
            "con_code": ["600519.SH"],
            "in_date": ["20100101"],
            "out_date": [None],
            "is_new": ["Y"],
        })
        from quant_data.sources.tushare import TushareAdapter
        a = TushareAdapter(pro_token="x")
        out = a.fetch("index_member", index_code="000300.SH", trade_date="20240102")
        assert len(out) == 1
        assert out["con_code"].iloc[0] == "600519.SH"


def test_index_member_schema_registered():
    from quant_data.schemas import SCHEMAS, INDEX_MEMBER_V1
    assert ("index_member", "v1") in SCHEMAS
    assert INDEX_MEMBER_V1.primary_key == ["index_code", "con_code", "in_date"]


def test_index_member_view_exists():
    db = DuckDBStore()
    db.bootstrap_views()
    rows = db.con.execute(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_type='VIEW' AND table_name='mv_index_member_v1'"
    ).fetchall()
    assert rows
    assert db.con.execute("SELECT count(*) FROM mv_index_member_v1").fetchone()[0] >= 0  # post-live-sync view


def test_sync_index_member_writes_duckdb(tmp_data_dir):
    from quant_data.rate_limit import TokenBucket
    from quant_data.sources.base import LineageRecord, RateLimit

    class ImSrc:
        name = "im"; version = "0"; capabilities = {"index_member"}
        def __init__(self):
            self._rl = TokenBucket(RateLimit(requests_per_min=100))
        def rate_limit(self): return RateLimit(requests_per_min=100)
        def healthcheck(self): return True
        def fetch(self, topic, **p):
            return pd.DataFrame({
                "index_code": [p.get("index_code", "000300.SH")] * 2,
                "con_code": ["600519.SH", "000858.SZ"],
                "in_date": [pd.to_datetime("20100101", format="%Y%m%d").date()] * 2,
                "out_date": [None, None],
                "is_new": ["Y", "Y"],
            })
        def lineage(self, **kw):
            from datetime import datetime; import uuid
            return LineageRecord(
                table=kw.get("table", ""), schema_version=kw.get("schema_version", ""),
                source=self.name, source_version=self.version,
                fetched_at=datetime.now().astimezone(), params=kw.get("params", {}),
                rows=kw.get("rows", 0), rate_limit_hit=0, request_id=str(uuid.uuid4()),
            )

    register_source("im", ImSrc())
    _seed_trade_cal(tmp_data_dir, date(2024, 1, 1), date(2024, 1, 3))
    r = sync_index_member(source="im", start_date=date(2024, 1, 2), end_date=date(2024, 1, 2),
                          index_pool=("000300.SH",))
    assert r["rows"] == 2
    db = DuckDBStore()
    assert db.con.execute("SELECT count(*) FROM raw_im_index_member").fetchone()[0] == 2
