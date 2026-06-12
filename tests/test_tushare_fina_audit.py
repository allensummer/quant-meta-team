"""Tests for tushare ``fina_audit`` adapter + sync (v0.9 — ADM-653 Batch 2)."""
from __future__ import annotations

from datetime import date
from unittest.mock import patch, MagicMock

import pandas as pd

from quant_data.registry import register_source
from quant_data.store.duckdb_store import DuckDBStore
from quant_data.sync.driver import sync_fina_audit


def test_fina_audit_in_capabilities():
    from quant_data.sources.tushare import TushareAdapter
    assert "fina_audit" in TushareAdapter.capabilities


def test_fina_audit_dispatch_to_pro_method():
    with patch("tushare.pro_api") as pro_api:
        pro = MagicMock()
        pro_api.return_value = pro
        pro.fina_audit.return_value = pd.DataFrame({
            "ts_code": ["600519.SH"],
            "end_date": ["20231231"],
            "ann_date": ["20240315"],
            "audit_result": ["标准无保留意见"],
            "audit_firm": ["天健"],
            "audit_sign": ["张三"],
            "audit_date": ["20240315"],
        })
        from quant_data.sources.tushare import TushareAdapter
        a = TushareAdapter(pro_token="x")
        out = a.fetch("fina_audit", period="20231231")
        assert len(out) == 1
        assert out["audit_firm"].iloc[0] == "天健"


def test_fina_audit_schema_registered():
    from quant_data.schemas import SCHEMAS, FINA_AUDIT_V1
    assert ("fina_audit", "v1") in SCHEMAS
    assert FINA_AUDIT_V1.primary_key == ["ts_code", "end_date"]


def test_fina_audit_view_exists():
    db = DuckDBStore()
    db.bootstrap_views()
    rows = db.con.execute(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_type='VIEW' AND table_name='mv_fina_audit_v1'"
    ).fetchall()
    assert rows
    assert db.con.execute("SELECT count(*) FROM mv_fina_audit_v1").fetchone()[0] >= 0  # post-live-sync view


def test_sync_fina_audit_writes_duckdb(tmp_data_dir):
    from quant_data.rate_limit import TokenBucket
    from quant_data.sources.base import LineageRecord, RateLimit

    class FaSrc:
        name = "fa"; version = "0"; capabilities = {"fina_audit"}
        def __init__(self):
            self._rl = TokenBucket(RateLimit(requests_per_min=100))
        def rate_limit(self): return RateLimit(requests_per_min=100)
        def healthcheck(self): return True
        def fetch(self, topic, **p):
            return pd.DataFrame({
                "ts_code": ["600519.SH"],
                "end_date": [pd.to_datetime("20231231", format="%Y%m%d").date()],
                "ann_date": [pd.to_datetime("20240315", format="%Y%m%d").date()],
                "audit_result": ["标准无保留意见"],
                "audit_firm": ["天健"], "audit_sign": ["张三"],
                "audit_date": [pd.to_datetime("20240315", format="%Y%m%d").date()],
            })
        def lineage(self, **kw):
            from datetime import datetime; import uuid
            return LineageRecord(
                table=kw.get("table", ""), schema_version=kw.get("schema_version", ""),
                source=self.name, source_version=self.version,
                fetched_at=datetime.now().astimezone(), params=kw.get("params", {}),
                rows=kw.get("rows", 0), rate_limit_hit=0, request_id=str(uuid.uuid4()),
            )

    register_source("fa", FaSrc())
    r = sync_fina_audit(source="fa", start_date=date(2024, 1, 1), end_date=date(2024, 3, 31))
    assert r["rows"] == 1
    db = DuckDBStore()
    assert db.con.execute("SELECT count(*) FROM raw_fa_fina_audit").fetchone()[0] == 1
