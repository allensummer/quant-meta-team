"""Parquet landing with Hive-style partitioning (v0.4 §3.2 + §4.1).

Layout::

    data_dir/raw_tushare_daily/trade_date=2024-01-02/part-<uuid>.parquet
    data_dir/raw_tushare_adj_factor/trade_date=2024-01-02/part-<uuid>.parquet
    data_dir/raw_tushare_stock_basic/_static/part-<uuid>.parquet      # snapshot tables
    data_dir/raw_tushare_trade_cal/cal_date=2024-01-02/part-<uuid>.parquet

We use Snappy compression (fast read, modest size; fine for 1-2 GB total).
"""
from __future__ import annotations

import logging
import uuid
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd

from quant_data.paths import raw_root

log = logging.getLogger("quant_data.store.parquet")


# topic -> hive partition column (None means snapshot)
_PARTITION_COL = {
    "daily": "trade_date",
    "adj_factor": "trade_date",
    "daily_basic": "trade_date",
    "trade_cal": "cal_date",
    "stock_basic": None,  # snapshot
}


class ParquetStore:
    """Writes schema-versioned Parquet files under ``data_dir/raw_<source>_<topic>``."""

    def __init__(self, source: str, topic: str):
        self.source = source
        self.topic = topic
        self.table = f"raw_{source}_{topic}"
        self.root = raw_root() / self.table
        self.root.mkdir(parents=True, exist_ok=True)

    def _partition_dir(self, value: date | str) -> Path:
        col = _PARTITION_COL.get(self.topic)
        if col is None:
            d = self.root / "_static"
        else:
            d = self.root / f"{col}={value.isoformat() if hasattr(value, 'isoformat') else str(value)}"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def write(self, df: pd.DataFrame, partition_value: date | str | None = None) -> Path:
        if df is None or df.empty:
            log.debug("parquet_store: empty df, skipping %s", self.table)
            return None  # type: ignore[return-value]
        col = _PARTITION_COL.get(self.topic)
        pv = partition_value
        if col is None:
            d = self.root / "_static"
            d.mkdir(parents=True, exist_ok=True)
        else:
            if pv is None:
                # use the first row's partition value (df should be single-partition anyway)
                pv = df[col].iloc[0]
            d = self.root / f"{col}={pv.isoformat() if hasattr(pv, 'isoformat') else str(pv)}"
            d.mkdir(parents=True, exist_ok=True)
        path = d / f"part-{uuid.uuid4().hex[:8]}.parquet"
        df.to_parquet(path, index=False, engine="pyarrow", compression="snappy")
        log.info("parquet_store: wrote %d rows -> %s", len(df), path)
        return path

    def list_partitions(self) -> list[str]:
        col = _PARTITION_COL.get(self.topic)
        if col is None:
            d = self.root / "_static"
            return [str(p) for p in d.glob("*.parquet")]
        return sorted(str(p.name) for p in self.root.glob(f"{col}=*"))

    def glob_for_duckdb(self) -> str:
        """Glob pattern that DuckDB's ``read_parquet`` can consume."""
        return str(self.root / "**" / "*.parquet")

    def total_size_bytes(self) -> int:
        return sum(p.stat().st_size for p in self.root.rglob("*.parquet"))
