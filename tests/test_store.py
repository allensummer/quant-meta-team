"""Direct DuckDBStore + ParquetStore + MetaSQLite coverage."""
from __future__ import annotations

import json
import uuid
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import pytest

from quant_data.paths import data_dir, duckdb_path, meta_dir
from quant_data.schemas import DAILY_V1
from quant_data.sources.base import LineageRecord
from quant_data.store.duckdb_store import DuckDBStore
from quant_data.store.meta_sqlite import MetaSQLite
from quant_data.store.parquet_store import ParquetStore


def test_duckdb_bootstrap_views(tmp_data_dir):
    db = DuckDBStore()
    views = db.bootstrap_views()
    assert {"mv_daily_v1", "mv_daily_qfq", "mv_daily_hfq", "mv_trade_cal"} <= set(views)
    # view definitions must reference read_parquet (so they pick up new files)
    row = db.con.execute(
        "SELECT sql FROM duckdb_views() WHERE view_name = 'mv_daily_v1'"
    ).fetchone()
    assert "read_parquet" in row[0]


def test_duckdb_get_set_cursor(tmp_data_dir):
    db = DuckDBStore()
    assert db.get_cursor("nonexistent") is None
    db.set_cursor("t", date(2024, 1, 2), status="ok")
    assert db.get_cursor("t") == date(2024, 1, 2)


def test_duckdb_query_returns_dataframe(tmp_data_dir):
    db = DuckDBStore()
    df = db.query("SELECT 1 AS one, 'x' AS s")
    assert list(df.columns) == ["one", "s"]
    assert int(df["one"].iloc[0]) == 1


def test_duckdb_register_schema_noop(tmp_data_dir):
    db = DuckDBStore()
    db.register_schema(DAILY_V1)  # covered by simple smoke
    from quant_data.schemas import SCHEMAS
    assert SCHEMAS[("daily", "v1")] is DAILY_V1


def test_duckdb_read_only_blocks_writes(tmp_data_dir):
    """read_only=True is the safe path for Portfolio / Risk agents (v0.4 §9.6 #6).

    Writers and readers must be able to attach the same file simultaneously
    without one blocking the other. Native DuckDB read-only mode achieves
    this and must reject any DDL/DML.
    """
    # First, create the file in RW mode and seed a view we can read back,
    # then close so we can re-open the same file RO in this process
    # (DuckDB forbids two in-process handles to the same file; multi-process
    # is the supported pattern — see test_concurrent_read.py).
    rw = DuckDBStore()
    rw.bootstrap_views()
    rw.query("SELECT 1 AS one")
    rw.con.close()

    ro = DuckDBStore(read_only=True)
    assert ro.read_only is True
    # Reads work
    df = ro.query("SELECT 1 AS one")
    assert int(df["one"].iloc[0]) == 1
    # Writes (DDL) are rejected
    with pytest.raises(Exception) as ei:
        ro.con.execute("CREATE TABLE _ro_blocked(i INT)")
    assert "read-only" in str(ei.value).lower()
    # Cursor writes are also rejected
    with pytest.raises(Exception):
        ro.set_cursor("t", date(2024, 1, 2))
    ro.con.close()


def test_parquet_write_hive_partition(tmp_data_dir):
    pq = ParquetStore(source="tushare", topic="daily")
    df = pd.DataFrame({
        "ts_code": ["000001.SZ", "600519.SH"],
        "trade_date": [date(2024, 1, 2), date(2024, 1, 2)],
        "open": [10.0, 100.0], "close": [10.5, 100.5],
        "vol": [100.0, 50.0], "amount": [105.0, 5025.0],
    })
    p = pq.write(df, partition_value=date(2024, 1, 2))
    assert p is not None
    assert p.exists()
    assert p.parent.name == "trade_date=2024-01-02"
    assert pq.total_size_bytes() > 0
    assert len(pq.list_partitions()) == 1


def test_parquet_snapshot_layout_for_stock_basic(tmp_data_dir):
    pq = ParquetStore(source="tushare", topic="stock_basic")
    df = pd.DataFrame({"ts_code": ["000001.SZ"], "name": ["平安银行"]})
    p = pq.write(df, partition_value=None)
    assert p is not None
    assert p.parent.name == "_static"


def test_parquet_write_empty_noop(tmp_data_dir):
    pq = ParquetStore(source="tushare", topic="daily")
    assert pq.write(pd.DataFrame(), partition_value=date(2024, 1, 2)) is None


def test_meta_sqlite_cursors(tmp_data_dir):
    m = MetaSQLite()
    assert m.get_cursor("t") is None
    m.set_cursor("t", date(2024, 1, 2))
    assert m.get_cursor("t") == date(2024, 1, 2)
    m.set_cursor("t", date(2024, 1, 3), status="failed", error="boom")
    cur = m.all_cursors()["t"]
    assert cur["last_trade_date"] == "2024-01-03"
    assert cur["status"] == "failed"
    assert cur["error_msg"] == "boom"


def test_meta_sqlite_lineage_write_and_recent(tmp_data_dir):
    m = MetaSQLite()
    rec = LineageRecord(
        table="raw_tushare_daily", schema_version="v1", source="tushare",
        source_version="tushare-pro-2000", fetched_at=datetime.now().astimezone(),
        params={"trade_date": "20240102"}, rows=5000, rate_limit_hit=0,
        request_id="abc-123",
    )
    path = m.write_lineage(rec)
    assert path.exists()
    body = json.loads(path.read_text(encoding="utf-8"))
    assert body["rows"] == 5000
    recent = m.recent_lineage("raw_tushare_daily", limit=5)
    assert len(recent) >= 1
    assert recent[0]["rows"] == 5000
