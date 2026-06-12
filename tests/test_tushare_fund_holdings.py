"""Test tushare `fund_holdings` — quarterly fund top-10 holdings.

Note (v0.8 — ADM-652): ``fund_holdings`` is reported to be a higher-tier
endpoint at the 2000-point tier (2026-06-12 dry-run returned
"请指定正确的接口名"). The schema, sync driver, and view remain
implemented and the unit tests below exercise the full sync path against
a mocked source. Real-network access is gated on either a tier upgrade
or a corrected method name.
"""
from __future__ import annotations

from datetime import date
from unittest.mock import patch, MagicMock

import pandas as pd

from quant_data.registry import register_source
from quant_data.schemas import SCHEMAS, FUND_HOLDINGS_V1
from quant_data.store.duckdb_store import DuckDBStore
from quant_data.sync.driver import sync_fund_holdings


def test_fund_holdings_schema_pk_and_fields():
    assert ("fund_holdings", "v1") in SCHEMAS
    assert FUND_HOLDINGS_V1.primary_key == ["ts_code", "ann_date", "end_date", "symbol"]
    # 8 expected fields per the value-ranking spec
    assert len(FUND_HOLDINGS_V1.fields) == 8
    # mkv is 持仓市值 (万元) per tushare
    assert FUND_HOLDINGS_V1.fields["mkv"].unit == "wan_yuan"


def test_fund_holdings_dispatch():
    """The driver must call pro.fund_holdings with end_date=YYYYMMDD."""
    with patch("tushare.pro_api") as pro_api:
        pro = MagicMock()
        pro_api.return_value = pro
        pro.fund_holdings.return_value = pd.DataFrame({
            "ts_code": ["519983.OF"] * 2,
            "ann_date": ["20240422", "20240422"],
            "end_date": ["20240331", "20240331"],
            "symbol": ["600519.SH", "000858.SZ"],
            "stk_name": ["贵州茅台", "五粮液"],
            "mkv": [10000.0, 5000.0],
            "amount": [1000.0, 5000.0],
            "stk_mkv_ratio": [5.0, 2.5],
        })
        from quant_data.sources.tushare import TushareAdapter
        a = TushareAdapter(pro_token="x")
        out = a.fetch("fund_holdings", end_date="20240331")
        assert len(out) == 2
        pro.fund_holdings.assert_called_with(end_date="20240331")


def test_fund_holdings_view_exists():
    db = DuckDBStore()
    rows = db.con.execute(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_type='VIEW' AND table_name='mv_fund_holdings_v1'"
    ).fetchall()
    assert rows


def test_sync_fund_holdings_iterates_quarter_ends(tmp_data_dir):
    """sync_fund_holdings must enumerate quarter-end dates in the window,
    not calendar trading days (per ADM-652 risk note)."""
    from quant_data.rate_limit import TokenBucket
    from quant_data.sources.base import LineageRecord, RateLimit

    called_ends: list[str] = []

    class FHSrc:
        name = "fh"; version = "0"; capabilities = {"fund_holdings"}
        def __init__(self):
            self._rl = TokenBucket(RateLimit(requests_per_min=100))
        def rate_limit(self): return RateLimit(requests_per_min=100)
        def healthcheck(self): return True
        def fetch(self, topic, **p):
            called_ends.append(p["end_date"])
            return pd.DataFrame({
                "ts_code": ["519983.OF"] * 2,
                "ann_date": ["20240422", "20240422"],
                "end_date": [pd.to_datetime(p["end_date"], format="%Y%m%d").date()] * 2,
                "symbol": ["600519.SH", "000858.SZ"],
                "stk_name": ["贵州茅台", "五粮液"],
                "mkv": [10000.0, 5000.0],
                "amount": [1000.0, 5000.0],
                "stk_mkv_ratio": [5.0, 2.5],
            })
        def lineage(self, **kw):
            from datetime import datetime; import uuid
            return LineageRecord(
                table=kw.get("table", ""), schema_version=kw.get("schema_version", ""),
                source=self.name, source_version=self.version,
                fetched_at=datetime.now().astimezone(), params=kw.get("params", {}),
                rows=kw.get("rows", 0), rate_limit_hit=0, request_id=str(uuid.uuid4()),
            )

    register_source("fh", FHSrc())
    # 2024 has quarter-ends 03-31, 06-30, 09-30, 12-31 — 4 batches
    r = sync_fund_holdings(source="fh",
                           start_date=date(2024, 1, 1), end_date=date(2024, 12, 31))
    assert sorted(called_ends) == ["20240331", "20240630", "20240930", "20241231"]
    # 4 quarter-ends × 2 holdings = 8 rows
    assert r["rows"] == 8
    assert r["batches"] == 4

    db = DuckDBStore()
    n = db.con.execute("SELECT count(*) FROM raw_fh_fund_holdings").fetchone()[0]
    assert n == 8
    # cursor lives in SQLite (MetaSQLite) — verify the last quarter-end
    from quant_data.store.meta_sqlite import MetaSQLite
    cur = MetaSQLite().get_cursor("fh_fund_holdings")
    assert cur == date(2024, 12, 31)
