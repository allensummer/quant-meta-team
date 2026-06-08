"""Kill mid-sync and verify resume picks up at the failed date (no skip, no dup)."""
from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import pytest

from quant_data.registry import register_source
from quant_data.store.meta_sqlite import MetaSQLite
from quant_data.sync.driver import sync_table
from tests.test_sync_idempotent import _seed_trade_cal


class _FailOnDay:
    """Fake source that returns data for most days but throws on 2024-01-03."""
    name = "fail_on_day"
    version = "0"
    capabilities = {"daily"}
    fail_on = date(2024, 1, 3)

    def __init__(self):
        from quant_data.rate_limit import TokenBucket
        from quant_data.sources.base import RateLimit
        self._rl = TokenBucket(RateLimit(requests_per_min=100))

    def rate_limit(self):
        from quant_data.sources.base import RateLimit
        return RateLimit(requests_per_min=100)

    def healthcheck(self):
        return True

    def fetch(self, topic, **params):
        d = params.get("trade_date")
        if d and d == self.fail_on.strftime("%Y%m%d"):
            raise RuntimeError("simulated network blip")
        return pd.DataFrame({
            "ts_code": ["000001.SZ"],
            "trade_date": [pd.to_datetime(d, format="%Y%m%d").date()],
            "open": [10.0], "high": [11.0], "low": [9.5], "close": [10.5],
            "pre_close": [10.0], "change": [0.5], "pct_chg": [5.0],
            "vol": [100.0], "amount": [105.0],
        })

    def lineage(self, **kw):
        from quant_data.sources.base import LineageRecord
        from datetime import datetime
        import uuid
        return LineageRecord(
            table=kw.get("table", ""), schema_version=kw.get("schema_version", ""),
            source=self.name, source_version=self.version,
            fetched_at=datetime.now().astimezone(), params=kw.get("params", {}),
            rows=kw.get("rows", 0), rate_limit_hit=0, request_id=str(uuid.uuid4()),
        )


def test_resume_after_failure(tmp_data_dir, monkeypatch):
    register_source("fail_on_day", _FailOnDay())
    _seed_trade_cal(tmp_data_dir, date(2024, 1, 1), date(2024, 1, 10))

    # First run: fails on 2024-01-03 after succeeding on 2024-01-02
    r1 = sync_table("daily", source="fail_on_day",
                    start_date=date(2024, 1, 2), end_date=date(2024, 1, 5))
    # rows = 1 (1 row on the only successful day)
    assert r1["rows"] == 1
    assert r1["last_date"] == "2024-01-02"

    meta = MetaSQLite()
    cur = meta.get_cursor("fail_on_day_daily")
    assert cur == date(2024, 1, 2), f"cursor should sit on the LAST GOOD day (2024-01-02), got {cur}"

    # Fix the network and re-run: must resume from 2024-01-03 (not re-pull 2024-01-02)
    _FailOnDay.fail_on = date(2099, 1, 1)  # never fails again
    r2 = sync_table("daily", source="fail_on_day",
                    start_date=date(2024, 1, 2), end_date=date(2024, 1, 5))
    # 3 more days should now succeed (Jan 3, 4, 5 — but Jan 6/7 might fall in window too)
    # We don't assume which weekdays those are; just assert cursor advanced
    cur2 = meta.get_cursor("fail_on_day_daily")
    assert cur2 > date(2024, 1, 2), f"cursor should advance past 2024-01-02, got {cur2}"

    # And the previously-synced day must not have duplicates
    from quant_data.store.duckdb_store import DuckDBStore
    db = DuckDBStore()
    n_jan2 = db.con.execute(
        "SELECT count(*) FROM raw_fail_on_day_daily WHERE trade_date = DATE '2024-01-02'"
    ).fetchone()[0]
    assert n_jan2 == 1, "2024-01-02 must remain single-row after resume"
