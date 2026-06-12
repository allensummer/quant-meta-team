"""Test tushare `moneyflow_hsgt` — 1 row/day cross-border capital flow."""
from __future__ import annotations

from datetime import date
from unittest.mock import patch, MagicMock

import pandas as pd

from quant_data.registry import register_source
from quant_data.schemas import SCHEMAS, MONEYFLOW_HSGT_V1
from quant_data.store.duckdb_store import DuckDBStore
from quant_data.sync.driver import sync_moneyflow_hsgt
from tests.test_sync_idempotent import _seed_trade_cal


def test_moneyflow_hsgt_schema_pk():
    """PK is (trade_date) only — 1 row per trading day."""
    assert ("moneyflow_hsgt", "v1") in SCHEMAS
    assert MONEYFLOW_HSGT_V1.primary_key == ["trade_date"]
    # 7 fields per tushare dry-run
    assert len(MONEYFLOW_HSGT_V1.fields) == 7


def test_moneyflow_hsgt_dispatch():
    with patch("tushare.pro_api") as pro_api:
        pro = MagicMock()
        pro_api.return_value = pro
        pro.moneyflow_hsgt.return_value = pd.DataFrame({
            "trade_date": ["20240102"],
            "ggt_ss": [1.0], "ggt_sz": [0.5],
            "hgt": [10.0], "sgt": [8.0],
            "north_money": [18.0], "south_money": [1.5],
        })
        from quant_data.sources.tushare import TushareAdapter
        a = TushareAdapter(pro_token="x")
        out = a.fetch("moneyflow_hsgt", trade_date="20240102")
        assert len(out) == 1
        assert out["trade_date"].iloc[0] == date(2024, 1, 2)
        assert out["north_money"].iloc[0] == 18.0


def test_moneyflow_hsgt_view_exists():
    db = DuckDBStore()
    rows = db.con.execute(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_type='VIEW' AND table_name='mv_moneyflow_hsgt_v1'"
    ).fetchall()
    assert rows


def test_sync_moneyflow_hsgt_writes_duckdb(tmp_data_dir):
    """Single-day snapshot — 1 row per trading day, cursor on (trade_date)."""
    from quant_data.rate_limit import TokenBucket
    from quant_data.sources.base import LineageRecord, RateLimit

    class HsgtSrc:
        name = "hsgt"; version = "0"; capabilities = {"moneyflow_hsgt"}
        def __init__(self):
            self._rl = TokenBucket(RateLimit(requests_per_min=100))
        def rate_limit(self): return RateLimit(requests_per_min=100)
        def healthcheck(self): return True
        def fetch(self, topic, **p):
            d = p["trade_date"]
            return pd.DataFrame({
                "trade_date": [pd.to_datetime(d, format="%Y%m%d").date()],
                "ggt_ss": [1.0], "ggt_sz": [0.5],
                "hgt": [10.0], "sgt": [8.0],
                "north_money": [18.0], "south_money": [1.5],
            })
        def lineage(self, **kw):
            from datetime import datetime; import uuid
            return LineageRecord(
                table=kw.get("table", ""), schema_version=kw.get("schema_version", ""),
                source=self.name, source_version=self.version,
                fetched_at=datetime.now().astimezone(), params=kw.get("params", {}),
                rows=kw.get("rows", 0), rate_limit_hit=0, request_id=str(uuid.uuid4()),
            )

    register_source("hsgt", HsgtSrc())
    _seed_trade_cal(tmp_data_dir, date(2024, 1, 1), date(2024, 1, 5))
    r = sync_moneyflow_hsgt(source="hsgt",
                            start_date=date(2024, 1, 2), end_date=date(2024, 1, 3))
    # 2 trading days, 1 row each
    assert r["rows"] == 2
    assert r["topic"] == "hsgt_moneyflow_hsgt"

    db = DuckDBStore()
    n = db.con.execute("SELECT count(*) FROM raw_hsgt_moneyflow_hsgt").fetchone()[0]
    assert n == 2
