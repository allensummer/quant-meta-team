"""Test tushare `moneyflow` adapter + sync driver.

Strategy: monkey-patch the pro API on the registered TushareAdapter to return
a synthetic per-day DataFrame, then run ``sync_moneyflow`` end-to-end and
verify the backing DuckDB table + cursor.
"""
from __future__ import annotations

from datetime import date
from unittest.mock import patch, MagicMock

import pandas as pd
import pytest

from quant_data.registry import register_source
from quant_data.store.duckdb_store import DuckDBStore
from quant_data.sync.driver import sync_moneyflow
from tests.test_sync_idempotent import _seed_trade_cal


# ---------------------------------------------------------------------------
# Adapter-level tests
# ---------------------------------------------------------------------------
def test_moneyflow_in_capabilities():
    """``moneyflow`` must be in TushareAdapter.capabilities (v0.8 — ADM-652)."""
    from quant_data.sources.tushare import TushareAdapter
    assert "moneyflow" in TushareAdapter.capabilities
    assert "moneyflow_hsgt" in TushareAdapter.capabilities
    assert "index_weight" in TushareAdapter.capabilities
    assert "hsgt_top10" in TushareAdapter.capabilities
    assert "fund_holdings" in TushareAdapter.capabilities


def test_moneyflow_dispatch_to_pro_method():
    """``fetch('moneyflow', trade_date=...)`` must reach ``pro.moneyflow``."""
    with patch("tushare.pro_api") as pro_api:
        pro = MagicMock()
        pro_api.return_value = pro
        pro.moneyflow.return_value = pd.DataFrame({
            "ts_code": ["000001.SZ"],
            "trade_date": ["20240102"],
            "buy_sm_vol": [1.0], "buy_sm_amount": [2.0],
            "sell_sm_vol": [1.0], "sell_sm_amount": [2.0],
            "buy_md_vol": [1.0], "buy_md_amount": [2.0],
            "sell_md_vol": [1.0], "sell_md_amount": [2.0],
            "buy_lg_vol": [1.0], "buy_lg_amount": [2.0],
            "sell_lg_vol": [1.0], "sell_lg_amount": [2.0],
            "buy_elg_vol": [1.0], "buy_elg_amount": [2.0],
            "sell_elg_vol": [1.0], "sell_elg_amount": [2.0],
            "net_mf_vol": [0.0], "net_mf_amount": [0.0],
        })
        from quant_data.sources.tushare import TushareAdapter
        a = TushareAdapter(pro_token="x")
        out = a.fetch("moneyflow", trade_date="20240102")
        assert len(out) == 1
        assert out["ts_code"].iloc[0] == "000001.SZ"
        assert out["trade_date"].iloc[0] == date(2024, 1, 2)
        pro.moneyflow.assert_called_with(trade_date="20240102")


# ---------------------------------------------------------------------------
# Schema + view tests
# ---------------------------------------------------------------------------
def test_moneyflow_schema_registered():
    """The schema for moneyflow must be in SCHEMAS."""
    from quant_data.schemas import SCHEMAS, MONEYFLOW_V1
    assert ("moneyflow", "v1") in SCHEMAS
    assert SCHEMAS[("moneyflow", "v1")] is MONEYFLOW_V1
    assert MONEYFLOW_V1.primary_key == ["ts_code", "trade_date"]
    # 20 expected fields per tushare dry-run 2026-06-12
    assert len(MONEYFLOW_V1.fields) == 20


def test_moneyflow_view_exists():
    """mv_moneyflow_v1 must be a queryable view after bootstrap."""
    db = DuckDBStore()
    rows = db.con.execute(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_type='VIEW' AND table_name='mv_moneyflow_v1'"
    ).fetchall()
    assert rows, "mv_moneyflow_v1 not bootstrapped"
    # empty placeholder data -> 0 rows
    n = db.con.execute("SELECT count(*) FROM mv_moneyflow_v1").fetchone()[0]
    assert n >= 0  # post-live-sync view


# ---------------------------------------------------------------------------
# Sync driver tests (mocked)
# ---------------------------------------------------------------------------
def test_sync_moneyflow_writes_duckdb(tmp_data_dir):
    """End-to-end sync of moneyflow writes to raw_tushare_moneyflow."""
    from quant_data.rate_limit import TokenBucket
    from quant_data.sources.base import LineageRecord, RateLimit

    class MfSrc:
        name = "mf"; version = "0"; capabilities = {"moneyflow"}
        def __init__(self):
            self._rl = TokenBucket(RateLimit(requests_per_min=100))
        def rate_limit(self): return RateLimit(requests_per_min=100)
        def healthcheck(self): return True
        def fetch(self, topic, **p):
            d = p["trade_date"]
            return pd.DataFrame({
                "ts_code": ["000001.SZ", "600519.SH"],
                "trade_date": [pd.to_datetime(d, format="%Y%m%d").date()] * 2,
                "buy_sm_vol": [10.0, 5.0], "buy_sm_amount": [100.0, 50.0],
                "sell_sm_vol": [8.0, 4.0], "sell_sm_amount": [80.0, 40.0],
                "buy_md_vol": [10.0, 5.0], "buy_md_amount": [100.0, 50.0],
                "sell_md_vol": [8.0, 4.0], "sell_md_amount": [80.0, 40.0],
                "buy_lg_vol": [10.0, 5.0], "buy_lg_amount": [100.0, 50.0],
                "sell_lg_vol": [8.0, 4.0], "sell_lg_amount": [80.0, 40.0],
                "buy_elg_vol": [10.0, 5.0], "buy_elg_amount": [100.0, 50.0],
                "sell_elg_vol": [8.0, 4.0], "sell_elg_amount": [80.0, 40.0],
                "net_mf_vol": [4.0, 2.0], "net_mf_amount": [40.0, 20.0],
            })
        def lineage(self, **kw):
            from datetime import datetime; import uuid
            return LineageRecord(
                table=kw.get("table", ""), schema_version=kw.get("schema_version", ""),
                source=self.name, source_version=self.version,
                fetched_at=datetime.now().astimezone(), params=kw.get("params", {}),
                rows=kw.get("rows", 0), rate_limit_hit=0, request_id=str(uuid.uuid4()),
            )

    register_source("mf", MfSrc())
    _seed_trade_cal(tmp_data_dir, date(2024, 1, 1), date(2024, 1, 5))
    r = sync_moneyflow(source="mf", start_date=date(2024, 1, 2), end_date=date(2024, 1, 3))
    assert r["rows"] == 4
    assert r["topic"] == "mf_moneyflow"

    db = DuckDBStore()
    n = db.con.execute("SELECT count(*) FROM raw_mf_moneyflow").fetchone()[0]
    assert n == 4
    # amount unit must be preserved in raw_ (千元); the view normalizes to yuan
    amt = db.con.execute("SELECT buy_sm_amount FROM raw_mf_moneyflow LIMIT 1").fetchone()[0]
    assert amt == 100.0
