"""Tests for tushare ``suspend`` adapter + sync (v0.9 — ADM-653 Batch 1)."""
from __future__ import annotations

from datetime import date
from unittest.mock import patch, MagicMock

import pandas as pd

from quant_data.registry import register_source
from quant_data.store.duckdb_store import DuckDBStore
from quant_data.sync.driver import sync_suspend
from tests.test_sync_idempotent import _seed_trade_cal


def test_suspend_in_capabilities():
    from quant_data.sources.tushare import TushareAdapter
    assert "suspend" in TushareAdapter.capabilities


def test_suspend_dispatch_to_pro_method():
    with patch("tushare.pro_api") as pro_api:
        pro = MagicMock()
        pro_api.return_value = pro
        pro.suspend.return_value = pd.DataFrame({
            "ts_code": ["000001.SZ"],
            "suspend_date": ["20240102"],
            "resume_date": [None],
            "suspend_type": ["S"],
            "reason": ["重大事项"],
        })
        from quant_data.sources.tushare import TushareAdapter
        a = TushareAdapter(pro_token="x")
        out = a.fetch("suspend", suspend_date="20240102")
        assert len(out) == 1
        assert out["ts_code"].iloc[0] == "000001.SZ"


def test_suspend_schema_registered():
    from quant_data.schemas import SCHEMAS, SUSPEND_V1
    assert ("suspend", "v1") in SCHEMAS
    assert SUSPEND_V1.primary_key == ["ts_code", "suspend_date"]


def test_suspend_view_exists():
    db = DuckDBStore()
    db.bootstrap_views()
    rows = db.con.execute(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_type='VIEW' AND table_name='mv_suspend_v1'"
    ).fetchall()
    assert rows
    assert db.con.execute("SELECT count(*) FROM mv_suspend_v1").fetchone()[0] >= 0  # post-live-sync view


def test_sync_suspend_writes_duckdb(tmp_data_dir):
    from quant_data.rate_limit import TokenBucket
    from quant_data.sources.base import LineageRecord, RateLimit

    class SuSrc:
        name = "su"; version = "0"; capabilities = {"suspend"}
        def __init__(self):
            self._rl = TokenBucket(RateLimit(requests_per_min=100))
        def rate_limit(self): return RateLimit(requests_per_min=100)
        def healthcheck(self): return True
        def fetch(self, topic, **p):
            d = p["suspend_date"]
            return pd.DataFrame({
                "ts_code": ["000001.SZ"],
                "suspend_date": [pd.to_datetime(d, format="%Y%m%d").date()],
                "resume_date": [None],
                "suspend_type": ["S"],
                "reason": ["重大事项"],
            })
        def lineage(self, **kw):
            from datetime import datetime; import uuid
            return LineageRecord(
                table=kw.get("table", ""), schema_version=kw.get("schema_version", ""),
                source=self.name, source_version=self.version,
                fetched_at=datetime.now().astimezone(), params=kw.get("params", {}),
                rows=kw.get("rows", 0), rate_limit_hit=0, request_id=str(uuid.uuid4()),
            )

    register_source("su", SuSrc())
    _seed_trade_cal(tmp_data_dir, date(2024, 1, 1), date(2024, 1, 3))
    r = sync_suspend(source="su", start_date=date(2024, 1, 2), end_date=date(2024, 1, 2))
    assert r["rows"] == 1
    db = DuckDBStore()
    assert db.con.execute("SELECT count(*) FROM raw_su_suspend").fetchone()[0] == 1
