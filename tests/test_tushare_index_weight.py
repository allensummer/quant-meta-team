"""Test tushare `index_weight` — per-(index, day) constituent weights."""
from __future__ import annotations

from datetime import date
from unittest.mock import patch, MagicMock

import pandas as pd

from quant_data.registry import register_source
from quant_data.schemas import SCHEMAS, INDEX_WEIGHT_V1
from quant_data.store.duckdb_store import DuckDBStore
from quant_data.sync.driver import sync_index_weight
from tests.test_sync_idempotent import _seed_trade_cal


def test_index_weight_schema_pk():
    assert ("index_weight", "v1") in SCHEMAS
    assert INDEX_WEIGHT_V1.primary_key == ["index_code", "con_code", "trade_date"]
    assert len(INDEX_WEIGHT_V1.fields) == 4


def test_index_weight_dispatch():
    with patch("tushare.pro_api") as pro_api:
        pro = MagicMock()
        pro_api.return_value = pro
        pro.index_weight.return_value = pd.DataFrame({
            "index_code": ["000300.SH"] * 3,
            "con_code": ["000001.SZ", "000002.SZ", "600519.SH"],
            "trade_date": ["20240102"] * 3,
            "weight": [0.5, 0.3, 0.2],
        })
        from quant_data.sources.tushare import TushareAdapter
        a = TushareAdapter(pro_token="x")
        out = a.fetch("index_weight", index_code="000300.SH", trade_date="20240102")
        assert len(out) == 3
        assert abs(out["weight"].sum() - 1.0) < 1e-9


def test_index_weight_view_exists():
    db = DuckDBStore()
    rows = db.con.execute(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_type='VIEW' AND table_name='mv_index_weight_v1'"
    ).fetchall()
    assert rows


def test_sync_index_weight_iterates_index_pool(tmp_data_dir):
    """sync_index_weight must call fetch once per (index, day)."""
    from quant_data.rate_limit import TokenBucket
    from quant_data.sources.base import LineageRecord, RateLimit

    class IW:
        name = "iw"; version = "0"; capabilities = {"index_weight"}
        def __init__(self):
            self._rl = TokenBucket(RateLimit(requests_per_min=100))
            self.calls: list[dict] = []
        def rate_limit(self): return RateLimit(requests_per_min=100)
        def healthcheck(self): return True
        def fetch(self, topic, **p):
            self.calls.append(p)
            return pd.DataFrame({
                "index_code": [p["index_code"]] * 2,
                "con_code": ["000001.SZ", "000002.SZ"],
                "trade_date": [pd.to_datetime(p["trade_date"], format="%Y%m%d").date()] * 2,
                "weight": [0.5, 0.5],
            })
        def lineage(self, **kw):
            from datetime import datetime; import uuid
            return LineageRecord(
                table=kw.get("table", ""), schema_version=kw.get("schema_version", ""),
                source=self.name, source_version=self.version,
                fetched_at=datetime.now().astimezone(), params=kw.get("params", {}),
                rows=kw.get("rows", 0), rate_limit_hit=0, request_id=str(uuid.uuid4()),
            )

    src = IW()
    register_source("iw", src)
    _seed_trade_cal(tmp_data_dir, date(2024, 1, 1), date(2024, 1, 5))
    # Override the default index pool to a tiny one for this test
    r = sync_index_weight(source="iw",
                          start_date=date(2024, 1, 2), end_date=date(2024, 1, 3),
                          index_pool=("000300.SH", "000905.SH"))
    # 2 indices × 2 days = 4 batches, 2 rows per batch = 8 rows
    assert r["rows"] == 8
    assert r["batches"] == 4
    # The adapter must have been called with both index_code values
    codes = {c["index_code"] for c in src.calls}
    assert codes == {"000300.SH", "000905.SH"}

    db = DuckDBStore()
    n = db.con.execute("SELECT count(*) FROM raw_iw_index_weight").fetchone()[0]
    assert n == 8
