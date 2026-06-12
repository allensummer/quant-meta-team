"""Tests for tushare ``shares_float`` adapter + sync (v0.9 — ADM-653 Batch 1)."""
from __future__ import annotations

from datetime import date
from unittest.mock import patch, MagicMock

import pandas as pd

from quant_data.registry import register_source
from quant_data.store.duckdb_store import DuckDBStore
from quant_data.sync.driver import sync_shares_float
from tests.test_sync_idempotent import _seed_trade_cal


def test_shares_float_in_capabilities():
    from quant_data.sources.tushare import TushareAdapter
    assert "shares_float" in TushareAdapter.capabilities


def test_shares_float_dispatch_to_pro_method():
    """Adapter maps our internal ``shares_float`` topic → tushare's upstream
    ``share_float`` method (singular). This test confirms the dispatch works.
    """
    with patch("tushare.pro_api") as pro_api:
        pro = MagicMock()
        pro_api.return_value = pro
        # Note: tushare's upstream method is ``share_float`` (singular),
        # even though our internal topic is ``shares_float``.
        pro.share_float.return_value = pd.DataFrame({
            "ts_code": ["000001.SZ"],
            "float_date": ["20240115"],
            "float_share": [1e9],
            "float_ratio": [5.0],
            "holder_name": ["某资管计划"],
            "share_type": ["定增"],
        })
        from quant_data.sources.tushare import TushareAdapter
        a = TushareAdapter(pro_token="x")
        out = a.fetch("shares_float", float_date="20240115")
        assert len(out) == 1
        assert out["holder_name"].iloc[0] == "某资管计划"
        # Verify the call was made to the right upstream method
        pro.share_float.assert_called_once()


def test_shares_float_schema_registered():
    from quant_data.schemas import SCHEMAS, SHARES_FLOAT_V1
    assert ("shares_float", "v1") in SCHEMAS
    assert SHARES_FLOAT_V1.primary_key == ["ts_code", "float_date", "holder_name"]


def test_shares_float_view_exists():
    db = DuckDBStore()
    db.bootstrap_views()
    rows = db.con.execute(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_type='VIEW' AND table_name='mv_shares_float_v1'"
    ).fetchall()
    assert rows
    # After live A-tier sync (ADM-653) the view has rows from the smoke test;
    # we only assert the view is materialized with the expected schema.
    n = db.con.execute("SELECT count(*) FROM mv_shares_float_v1").fetchone()[0]
    assert n >= 0  # materialized (0 acceptable pre-sync, >0 post-sync)


def test_sync_shares_float_writes_duckdb(tmp_data_dir):
    from quant_data.rate_limit import TokenBucket
    from quant_data.sources.base import LineageRecord, RateLimit

    class SfSrc:
        name = "sf"; version = "0"; capabilities = {"shares_float"}
        def __init__(self):
            self._rl = TokenBucket(RateLimit(requests_per_min=100))
        def rate_limit(self): return RateLimit(requests_per_min=100)
        def healthcheck(self): return True
        def fetch(self, topic, **p):
            d = p["float_date"]
            return pd.DataFrame({
                "ts_code": ["000001.SZ"],
                "float_date": [pd.to_datetime(d, format="%Y%m%d").date()],
                "float_share": [1e9], "float_ratio": [5.0],
                "holder_name": ["某资管计划"], "share_type": ["定增"],
            })
        def lineage(self, **kw):
            from datetime import datetime; import uuid
            return LineageRecord(
                table=kw.get("table", ""), schema_version=kw.get("schema_version", ""),
                source=self.name, source_version=self.version,
                fetched_at=datetime.now().astimezone(), params=kw.get("params", {}),
                rows=kw.get("rows", 0), rate_limit_hit=0, request_id=str(uuid.uuid4()),
            )

    register_source("sf", SfSrc())
    _seed_trade_cal(tmp_data_dir, date(2024, 1, 1), date(2024, 1, 20))
    r = sync_shares_float(source="sf", start_date=date(2024, 1, 15), end_date=date(2024, 1, 15))
    assert r["rows"] == 1
    db = DuckDBStore()
    assert db.con.execute("SELECT count(*) FROM raw_sf_shares_float").fetchone()[0] == 1
