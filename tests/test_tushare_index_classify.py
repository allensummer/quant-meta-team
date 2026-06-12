"""Tests for tushare ``index_classify`` adapter + sync (v0.9 — ADM-653 Batch 1)."""
from __future__ import annotations

from datetime import date
from unittest.mock import patch, MagicMock

import pandas as pd
import pytest

from quant_data.registry import register_source
from quant_data.store.duckdb_store import DuckDBStore
from quant_data.sync.driver import sync_index_classify
from tests.test_sync_idempotent import _seed_trade_cal


def test_index_classify_in_capabilities():
    from quant_data.sources.tushare import TushareAdapter
    assert "index_classify" in TushareAdapter.capabilities


def test_index_classify_dispatch_to_pro_method():
    with patch("tushare.pro_api") as pro_api:
        pro = MagicMock()
        pro_api.return_value = pro
        pro.index_classify.return_value = pd.DataFrame({
            "index_code": ["801010.SI"],
            "index_name": ["农林牧渔"],
            "industry_name": ["农林牧渔"],
            "level": ["L1"],
            "is_published": [1],
            "src": ["SW"],
            "weight_rule": ["等权"],
            "exchange": ["SSE"],
            "list_date": ["20140101"],
            "exp_date": [None],
        })
        from quant_data.sources.tushare import TushareAdapter
        a = TushareAdapter(pro_token="x")
        out = a.fetch("index_classify")
        assert len(out) == 1
        assert out["index_code"].iloc[0] == "801010.SI"
        pro.index_classify.assert_called_once()


def test_index_classify_schema_registered():
    from quant_data.schemas import SCHEMAS, INDEX_CLASSIFY_V1
    assert ("index_classify", "v1") in SCHEMAS
    assert SCHEMAS[("index_classify", "v1")] is INDEX_CLASSIFY_V1
    assert INDEX_CLASSIFY_V1.primary_key == ["index_code"]


def test_index_classify_view_exists():
    db = DuckDBStore()
    db.bootstrap_views()
    rows = db.con.execute(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_type='VIEW' AND table_name='mv_index_classify_v1'"
    ).fetchall()
    assert rows
    n = db.con.execute("SELECT count(*) FROM mv_index_classify_v1").fetchone()[0]
    # Materialized with the expected schema; pre-sync = 0, post-sync >= 1.
    assert n >= 0


def test_sync_index_classify_writes_duckdb(tmp_data_dir):
    from quant_data.rate_limit import TokenBucket
    from quant_data.sources.base import LineageRecord, RateLimit

    class IcSrc:
        name = "ic"; version = "0"; capabilities = {"index_classify"}
        def __init__(self):
            self._rl = TokenBucket(RateLimit(requests_per_min=100))
        def rate_limit(self): return RateLimit(requests_per_min=100)
        def healthcheck(self): return True
        def fetch(self, topic, **p):
            return pd.DataFrame({
                "index_code": ["801010.SI", "801020.SI"],
                "index_name": ["农林牧渔", "基础化工"],
                "industry_name": ["农林牧渔", "基础化工"],
                "level": ["L1", "L1"],
                "is_published": [1, 1],
                "src": ["SW", "SW"],
                "weight_rule": ["等权", "等权"],
                "exchange": ["SSE", "SSE"],
                "list_date": [pd.to_datetime("20140101", format="%Y%m%d").date()] * 2,
                "exp_date": [None, None],
            })
        def lineage(self, **kw):
            from datetime import datetime; import uuid
            return LineageRecord(
                table=kw.get("table", ""), schema_version=kw.get("schema_version", ""),
                source=self.name, source_version=self.version,
                fetched_at=datetime.now().astimezone(), params=kw.get("params", {}),
                rows=kw.get("rows", 0), rate_limit_hit=0, request_id=str(uuid.uuid4()),
            )

    register_source("ic", IcSrc())
    r = sync_index_classify(source="ic")
    assert r["rows"] == 2
    assert r["topic"] == "ic_index_classify"
    db = DuckDBStore()
    n = db.con.execute("SELECT count(*) FROM raw_ic_index_classify").fetchone()[0]
    assert n == 2
