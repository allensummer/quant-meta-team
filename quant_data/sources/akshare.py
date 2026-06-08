"""Akshare adapter — backup / cross-validation source (v0.4 §1).

Heavy imports: ``akshare`` is imported lazily on first call so unit tests
that never touch akshare stay fast.
"""
from __future__ import annotations

import logging
import os
import time
from datetime import datetime
from typing import Any

import pandas as pd

from quant_data.rate_limit import TokenBucket
from quant_data.sources.base import DataSource, LineageRecord, RateLimit

log = logging.getLogger("quant_data.sources.akshare")


# unit conversion: akshare -> tushare space (v0.4 §5)
def _akshare_to_canonical_daily(df: pd.DataFrame) -> pd.DataFrame:
    """akshare stock_zh_a_hist returns: 日期, 股票代码, 开盘, 收盘, 最高, 最低, 成交量, 成交额, 振幅, 涨跌幅, 涨跌额, 换手率."""
    if df.empty:
        return df
    rename = {
        "日期": "trade_date",
        "股票代码": "ts_code",
        "开盘": "open",
        "最高": "high",
        "最低": "low",
        "收盘": "close",
        "成交量": "vol_share",
        "成交额": "amount_yuan",
    }
    df = df.rename(columns=rename)
    # tushare uses YYYYMMDD integer-ish strings; we keep as date() objects via canonicalize step
    df["trade_date"] = pd.to_datetime(df["trade_date"], errors="coerce").dt.date
    # convert units to tushare's: vol in 100-share lots, amount in 千元
    if "vol_share" in df.columns:
        df["vol"] = df["vol_share"] / 100.0
    if "amount_yuan" in df.columns:
        df["amount"] = df["amount_yuan"] / 1000.0
    return df


class AkshareAdapter:
    name = "akshare"
    version = "1.0.0"
    capabilities = {"daily", "stock_basic"}

    def __init__(self):
        # akshare 上游（东财）极不耐操 — 留余量到 30 req/min
        self._rl = TokenBucket(RateLimit(requests_per_min=40, notes="akshare-conservative"))

    def rate_limit(self) -> RateLimit:
        return RateLimit(requests_per_min=40, notes="akshare-conservative")

    def healthcheck(self) -> bool:
        try:
            import akshare as ak
            df = ak.stock_zh_a_spot_em()  # 全市场 snapshot
            return not df.empty
        except Exception as e:  # pragma: no cover
            log.warning("akshare healthcheck failed: %s", e)
            return False

    def fetch(self, topic: str, **params: Any) -> pd.DataFrame:
        if topic not in self.capabilities:
            raise ValueError(f"akshare adapter: topic {topic!r} not supported")
        import akshare as ak  # lazy

        self._rl.acquire()
        t0 = time.monotonic()
        if topic == "daily":
            code = params["ts_code"][:6]  # strip .SH/.SZ
            start = params.get("start_date", "20240101")
            end = params.get("end_date", datetime.now().strftime("%Y%m%d"))
            df = ak.stock_zh_a_hist(symbol=code, period="daily",
                                    start_date=start.replace("-", ""),
                                    end_date=end.replace("-", ""))
            df = _akshare_to_canonical_daily(df)
        elif topic == "stock_basic":
            df = ak.stock_info_a_code_name()
        else:
            df = pd.DataFrame()
        log.info("akshare %s -> %d rows in %.2fs", topic, len(df), time.monotonic() - t0)
        return df

    def lineage(self, table: str, schema_version: str, params: dict, rows: int) -> LineageRecord:
        import uuid
        return LineageRecord(
            table=table,
            schema_version=schema_version,
            source=self.name,
            source_version="akshare-latest",
            fetched_at=datetime.now().astimezone(),
            params=params,
            rows=rows,
            rate_limit_hit=self._rl.rate_limit_hit,
            request_id=str(uuid.uuid4()),
        )
