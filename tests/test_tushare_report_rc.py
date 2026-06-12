"""Tests for tushare ``report_rc`` adapter + sync (v0.9 — ADM-653 Batch 3)."""
from __future__ import annotations

from datetime import date
from unittest.mock import patch, MagicMock

import pandas as pd

from quant_data.registry import register_source
from quant_data.store.duckdb_store import DuckDBStore
from quant_data.sync.driver import sync_report_rc
from tests.test_sync_idempotent import _seed_trade_cal


def test_report_rc_in_capabilities():
    from quant_data.sources.tushare import TushareAdapter
    assert "report_rc" in TushareAdapter.capabilities


def test_report_rc_dispatch_to_pro_method():
    with patch("tushare.pro_api") as pro_api:
        pro = MagicMock()
        pro_api.return_value = pro
        pro.report_rc.return_value = pd.DataFrame({
            "ts_code": ["600519.SH"],
            "name": ["贵州茅台"],
            "report_date": ["20240115"],
            "report_title": ["业绩超预期"],
            "report_type": ["点评"],
            "org_name": ["中信证券"],
            "author_name": ["分析师A"],
            "rating": ["买入"],
            "rating_change": ["维持"],
            "target_price": [2200.0],
            "industry_name": ["食品饮料"],
            "title_keyword": ["业绩超预期"],
        })
        from quant_data.sources.tushare import TushareAdapter
        a = TushareAdapter(pro_token="x")
        out = a.fetch("report_rc", report_date="20240115")
        assert len(out) == 1
        assert out["rating"].iloc[0] == "买入"


def test_report_rc_schema_registered():
    from quant_data.schemas import SCHEMAS, REPORT_RC_V1
    assert ("report_rc", "v1") in SCHEMAS
    assert REPORT_RC_V1.primary_key == ["ts_code", "report_date", "org_name", "author_name"]


def test_report_rc_view_exists():
    db = DuckDBStore()
    db.bootstrap_views()
    rows = db.con.execute(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_type='VIEW' AND table_name='mv_report_rc_v1'"
    ).fetchall()
    assert rows
    assert db.con.execute("SELECT count(*) FROM mv_report_rc_v1").fetchone()[0] >= 0  # post-live-sync view


def test_sync_report_rc_writes_duckdb(tmp_data_dir):
    from quant_data.rate_limit import TokenBucket
    from quant_data.sources.base import LineageRecord, RateLimit

    class RcSrc:
        name = "rc"; version = "0"; capabilities = {"report_rc"}
        def __init__(self):
            self._rl = TokenBucket(RateLimit(requests_per_min=100))
        def rate_limit(self): return RateLimit(requests_per_min=100)
        def healthcheck(self): return True
        def fetch(self, topic, **p):
            d = p["report_date"]
            return pd.DataFrame({
                "ts_code": ["600519.SH", "000858.SZ"],
                "name": ["贵州茅台", "五粮液"],
                "report_date": [pd.to_datetime(d, format="%Y%m%d").date()] * 2,
                "report_title": ["业绩超预期", "稳健增长"],
                "report_type": ["点评", "深度"],
                "org_name": ["中信证券", "中金公司"],
                "author_name": ["分析师A", "分析师B"],
                "rating": ["买入", "增持"],
                "rating_change": ["维持", "维持"],
                "target_price": [2200.0, 200.0],
                "industry_name": ["食品饮料", "食品饮料"],
                "title_keyword": ["业绩超预期", "稳健增长"],
            })
        def lineage(self, **kw):
            from datetime import datetime; import uuid
            return LineageRecord(
                table=kw.get("table", ""), schema_version=kw.get("schema_version", ""),
                source=self.name, source_version=self.version,
                fetched_at=datetime.now().astimezone(), params=kw.get("params", {}),
                rows=kw.get("rows", 0), rate_limit_hit=0, request_id=str(uuid.uuid4()),
            )

    register_source("rc", RcSrc())
    _seed_trade_cal(tmp_data_dir, date(2024, 1, 1), date(2024, 1, 20))
    r = sync_report_rc(source="rc", start_date=date(2024, 1, 15), end_date=date(2024, 1, 15))
    assert r["rows"] == 2
    db = DuckDBStore()
    assert db.con.execute("SELECT count(*) FROM raw_rc_report_rc").fetchone()[0] == 2
