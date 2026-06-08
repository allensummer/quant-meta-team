"""Schema for ``daily`` (tushare `pro.daily`) — v1.

PK: ``(ts_code, trade_date)``.

Unit semantics (v0.4 §5):
  - ``vol``     : 1 手 = 100 股 (tushare native)
  - ``amount``  : 千元 (tushare native) — view layer normalizes to yuan

Tushare native column names are preserved in ``source_mapping['tushare']``.
"""
from quant_data.sources.base import FieldSpec, TableSchema

DAILY_V1 = TableSchema(
    table="daily",
    version="v1",
    primary_key=["ts_code", "trade_date"],
    fields={
        "ts_code": FieldSpec("ts_code", "string", "code", nullable=False, description="Tushare-style code, e.g. 000001.SZ"),
        "trade_date": FieldSpec("trade_date", "date", "calendar", nullable=False, description="Trading day"),
        "open": FieldSpec("open", "float64", "yuan", nullable=False),
        "high": FieldSpec("high", "float64", "yuan", nullable=False),
        "low": FieldSpec("low", "float64", "yuan", nullable=False),
        "close": FieldSpec("close", "float64", "yuan", nullable=False),
        "pre_close": FieldSpec("pre_close", "float64", "yuan", nullable=False),
        "change": FieldSpec("change", "float64", "yuan", nullable=False),
        "pct_chg": FieldSpec("pct_chg", "float64", "percent", nullable=False),
        "vol": FieldSpec("vol", "float64", "lot", nullable=False, description="1 lot = 100 shares"),
        "amount": FieldSpec("amount", "float64", "kilo_yuan", nullable=False, description="tushare native = 千元"),
    },
    source_mapping={
        "tushare": {
            "ts_code": "ts_code",
            "trade_date": "trade_date",
            "open": "open",
            "high": "high",
            "low": "low",
            "close": "close",
            "pre_close": "pre_close",
            "change": "change",
            "pct_chg": "pct_chg",
            "vol": "vol",
            "amount": "amount",
        },
    },
)
