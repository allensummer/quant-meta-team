"""Additional coverage for quant_data.sync.driver."""
from __future__ import annotations

from datetime import date
from unittest.mock import patch

import pandas as pd
import pytest

from quant_data.registry import register_source
from quant_data.store.meta_sqlite import MetaSQLite
from quant_data.sync import driver
from quant_data.sync.driver import (
    sync_adj_factor, sync_daily_basic, sync_stock_basic, sync_table, sync_trade_cal,
)
from tests.test_sync_idempotent import FakeDailySource, _seed_trade_cal


def test_sync_table_no_days_returns_empty(tmp_data_dir):
    """When start_date > end_date the driver returns immediately with 0 rows."""
    register_source("fake", FakeDailySource())
    _seed_trade_cal(tmp_data_dir, date(2024, 1, 1), date(2024, 1, 5))
    r = sync_table("daily", source="fake",
                   start_date=date(2024, 1, 10), end_date=date(2024, 1, 5))
    assert r["rows"] == 0
    assert r["batches"] == 0


def test_sync_table_handles_empty_source_response(tmp_data_dir):
    """If the source returns an empty df, sync must still advance the cursor and not crash."""
    from quant_data.rate_limit import TokenBucket
    from quant_data.sources.base import LineageRecord, RateLimit

    class Empty:
        name = "empty"; version = "0"; capabilities = {"daily"}
        def __init__(self): self._rl = TokenBucket(RateLimit(requests_per_min=100))
        def rate_limit(self): return RateLimit(requests_per_min=100)
        def healthcheck(self): return True
        def fetch(self, topic, **p): return pd.DataFrame()
        def lineage(self, **kw):
            from datetime import datetime; import uuid
            return LineageRecord(table="", schema_version="", source=self.name,
                                 source_version="0", fetched_at=datetime.now().astimezone(),
                                 params={}, rows=0, rate_limit_hit=0, request_id=str(uuid.uuid4()))

    register_source("empty", Empty())
    _seed_trade_cal(tmp_data_dir, date(2024, 1, 1), date(2024, 1, 5))
    r = sync_table("daily", source="empty",
                   start_date=date(2024, 1, 2), end_date=date(2024, 1, 3))
    assert r["rows"] == 0
    # cursor should still advance to the last day even with 0-row responses
    cur = MetaSQLite().get_cursor("empty_daily")
    assert cur == date(2024, 1, 3)


def test_sync_table_passes_exchange_for_trade_cal(tmp_data_dir):
    """The trade_cal sync must pass exchange to the adapter."""
    from quant_data.rate_limit import TokenBucket
    from quant_data.sources.base import LineageRecord, RateLimit

    captured: list[dict] = []

    class CaptureSrc:
        name = "capture"; version = "0"; capabilities = {"trade_cal"}
        def __init__(self): self._rl = TokenBucket(RateLimit(requests_per_min=100))
        def rate_limit(self): return RateLimit(requests_per_min=100)
        def healthcheck(self): return True
        def fetch(self, topic, **p):
            captured.append(p)
            return pd.DataFrame({
                "exchange": [p.get("exchange", "SSE")],
                "cal_date": [pd.to_datetime(p["start_date"], format="%Y%m%d").date()],
                "is_open": [1], "pretrade_date": [None],
            })
        def lineage(self, **kw):
            from datetime import datetime; import uuid
            return LineageRecord(table="", schema_version="", source=self.name,
                                 source_version="0", fetched_at=datetime.now().astimezone(),
                                 params={}, rows=0, rate_limit_hit=0, request_id=str(uuid.uuid4()))

    register_source("capture", CaptureSrc())
    sync_trade_cal(start=date(2024, 1, 2), end=date(2024, 1, 2), source="capture")
    assert any("exchange" in c for c in captured)


def test_sync_stock_basic_is_snapshot(tmp_data_dir):
    """stock_basic sync must fully replace the universe, not be incremental."""
    from quant_data.rate_limit import TokenBucket
    from quant_data.sources.base import LineageRecord, RateLimit

    call_count = 0

    class Snap:
        name = "snap"; version = "0"; capabilities = {"stock_basic"}
        def __init__(self): self._rl = TokenBucket(RateLimit(requests_per_min=100))
        def rate_limit(self): return RateLimit(requests_per_min=100)
        def healthcheck(self): return True
        def fetch(self, topic, **p):
            nonlocal call_count
            call_count += 1
            n = call_count * 2  # 2, 4, 6...
            return pd.DataFrame({
                "ts_code": [f"00000{i}.SZ" for i in range(n)],
                "symbol": [f"00000{i}" for i in range(n)],
                "name": [f"stock{i}" for i in range(n)],
                "industry": ["bank"] * n,
                "exchange": ["SZSE"] * n,
                "curr_type": ["CNY"] * n,
                "list_status": ["L"] * n,
                "list_date": [date(2000, 1, 1)] * n,
            })
        def lineage(self, **kw):
            from datetime import datetime; import uuid
            return LineageRecord(table="", schema_version="", source=self.name,
                                 source_version="0", fetched_at=datetime.now().astimezone(),
                                 params={}, rows=0, rate_limit_hit=0, request_id=str(uuid.uuid4()))

    register_source("snap", Snap())
    r1 = sync_stock_basic(source="snap")
    r2 = sync_stock_basic(source="snap")
    # r2 returned more rows because the fake grows; the table must reflect r2 only
    from quant_data.store.duckdb_store import DuckDBStore
    db = DuckDBStore()
    n = db.con.execute("SELECT count(*) FROM raw_snap_stock_basic").fetchone()[0]
    assert n == r2["rows"]


def test_sync_adj_factor_and_basic_dispatch(tmp_data_dir):
    """sync_adj_factor and sync_daily_basic must reuse the generic driver."""
    from quant_data.rate_limit import TokenBucket
    from quant_data.sources.base import LineageRecord, RateLimit

    class CapAdj:
        name = "cap_adj"; version = "0"; capabilities = {"adj_factor"}
        def __init__(self): self._rl = TokenBucket(RateLimit(requests_per_min=100))
        def rate_limit(self): return RateLimit(requests_per_min=100)
        def healthcheck(self): return True
        def fetch(self, topic, **p):
            return pd.DataFrame({
                "ts_code": ["000001.SZ"],
                "trade_date": [pd.to_datetime(p["trade_date"], format="%Y%m%d").date()],
                "adj_factor": [1.0],
            })
        def lineage(self, **kw):
            from datetime import datetime; import uuid
            return LineageRecord(table="", schema_version="", source=self.name,
                                 source_version="0", fetched_at=datetime.now().astimezone(),
                                 params={}, rows=0, rate_limit_hit=0, request_id=str(uuid.uuid4()))

    register_source("cap_adj", CapAdj())
    _seed_trade_cal(tmp_data_dir, date(2024, 1, 1), date(2024, 1, 5))
    r = sync_adj_factor(source="cap_adj",
                        start_date=date(2024, 1, 2), end_date=date(2024, 1, 3))
    assert r["rows"] == 2
    assert r["topic"] == "cap_adj_adj_factor"
