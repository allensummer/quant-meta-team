"""Re-running the same sync window must not produce duplicate rows."""
from __future__ import annotations

import json
import shutil
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from quant_data.rate_limit import TokenBucket
from quant_data.sources.base import LineageRecord, RateLimit
from quant_data.store.duckdb_store import DuckDBStore
from quant_data.store.parquet_store import ParquetStore


class FakeDailySource:
    """Module-level fake so other tests / debug scripts can reuse it."""
    name = "fake"
    version = "0"
    capabilities = {"daily"}

    def __init__(self):
        self._rl = TokenBucket(RateLimit(requests_per_min=100))

    def rate_limit(self):
        return RateLimit(requests_per_min=100)

    def healthcheck(self):
        return True

    def fetch(self, topic, **params):
        d = params.get("trade_date")
        return pd.DataFrame({
            "ts_code": ["000001.SZ", "600519.SH"],
            "trade_date": [pd.to_datetime(d, format="%Y%m%d").date()] * 2,
            "open": [10.0, 100.0], "high": [11.0, 101.0], "low": [9.5, 99.0],
            "close": [10.5, 100.5], "pre_close": [10.0, 100.0],
            "change": [0.5, 0.5], "pct_chg": [5.0, 0.5],
            "vol": [100.0, 50.0], "amount": [105.0, 5025.0],
        })

    def lineage(self, **kw):
        from datetime import datetime
        import uuid
        return LineageRecord(
            table=kw.get("table", ""), schema_version=kw.get("schema_version", ""),
            source=self.name, source_version=self.version,
            fetched_at=datetime.now().astimezone(), params=kw.get("params", {}),
            rows=kw.get("rows", 0), rate_limit_hit=0, request_id=str(uuid.uuid4()),
        )


def test_upsert_idempotent_on_trade_date(tmp_path: Path):
    """Pulling the same trade_date twice must not double-count in the backing DuckDB table."""
    # Build a fake parquet tree by hand
    pq_root = tmp_path / "raw_tushare_daily"
    (pq_root / "trade_date=2024-01-02").mkdir(parents=True)
    df = pd.DataFrame({
        "ts_code": ["000001.SZ", "600519.SH"],
        "trade_date": [date(2024, 1, 2), date(2024, 1, 2)],
        "open": [10.0, 100.0], "high": [11.0, 101.0], "low": [9.5, 99.0],
        "close": [10.5, 100.5], "pre_close": [10.0, 100.0],
        "change": [0.5, 0.5], "pct_chg": [5.0, 0.5],
        "vol": [100.0, 50.0], "amount": [105.0, 5025.0],
    })
    df.to_parquet(pq_root / "trade_date=2024-01-02" / "part-a.parquet", index=False)

    db = DuckDBStore(path=tmp_path / "test.duckdb")
    db.con.execute(
        f"CREATE TABLE raw_tushare_daily AS "
        f"SELECT * FROM read_parquet('{pq_root}/**/*.parquet')"
    )
    before = db.con.execute("SELECT count(*) FROM raw_tushare_daily").fetchone()[0]
    assert before == 2

    # Upsert the same df again
    db.upsert("tushare_daily", df, "v1")
    after = db.con.execute("SELECT count(*) FROM raw_tushare_daily").fetchone()[0]
    assert after == 2, "second upsert must replace, not append"


def test_repeated_sync_full_does_not_duplicate(tmp_data_dir, monkeypatch):
    """Run sync_table twice over the same window; row count must stay the same."""
    from quant_data.sync.driver import sync_table
    from quant_data.registry import register_source

    register_source("fake", FakeDailySource())
    # also build a tiny trade_cal so the driver can enumerate days
    _seed_trade_cal(tmp_data_dir, date(2024, 1, 1), date(2024, 1, 31))

    r1 = sync_table("daily", source="fake", start_date=date(2024, 1, 2), end_date=date(2024, 1, 3))
    # r1: cursor is fresh, so it pulls Jan 2 + Jan 3 (2 days × 2 rows = 4 rows)
    assert r1["rows"] == 4

    # Second run with the same start: cursor now sits on Jan 3, so driver
    # automatically picks up from Jan 4 (no duplicates) and the start_date
    # argument is treated as a hint, not a re-pull trigger.
    r2 = sync_table("daily", source="fake", start_date=date(2024, 1, 2), end_date=date(2024, 1, 3))
    assert r2["rows"] == 0, "second run should be a no-op because the cursor already covers the window"

    # Backing table must contain exactly 4 rows (2 dates × 2 stocks), no dupes
    db = DuckDBStore()
    n = db.con.execute("SELECT count(*) FROM raw_fake_daily").fetchone()[0]
    assert n == 4, f"expected 4 rows, got {n}"
    # Per (ts_code, trade_date) must be unique
    dupes = db.con.execute(
        "SELECT count(*) FROM (SELECT ts_code, trade_date, count(*) c "
        "FROM raw_fake_daily GROUP BY 1,2 HAVING c > 1)"
    ).fetchone()[0]
    assert dupes == 0


def _seed_trade_cal(data_dir: Path, start: date, end: date) -> None:
    import pandas as pd
    from quant_data.paths import raw_root
    root = raw_root() / "raw_tushare_trade_cal"
    (root / "_seed").mkdir(parents=True, exist_ok=True)
    rows = []
    d = start
    while d <= end:
        is_open = 1 if d.weekday() < 5 else 0
        rows.append({"exchange": "SSE", "cal_date": d, "is_open": is_open, "pretrade_date": d})
        from datetime import timedelta
        d = d + timedelta(days=1)
    pd.DataFrame(rows).to_parquet(root / "_seed" / "cal.parquet", index=False)
