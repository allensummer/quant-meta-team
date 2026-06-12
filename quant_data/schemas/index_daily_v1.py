"""Schema for ``index_daily`` (tushare `pro.index_daily`) — v1.

指数日线行情:沪深 300 / 中证 500 / 中证 1000 / 申万一级 30 个 / 全 A 等。
PK: ``(ts_code, trade_date)``。
"""
from quant_data.sources.base import FieldSpec, TableSchema

INDEX_DAILY_V1 = TableSchema(
    table="index_daily",
    version="v1",
    primary_key=["ts_code", "trade_date"],
    fields={
        "ts_code": FieldSpec("ts_code", "string", "code", nullable=False,
                             description="指数代码 (e.g. 000300.SH)"),
        "trade_date": FieldSpec("trade_date", "date", "calendar", nullable=False),
        "open": FieldSpec("open", "float64", "point", nullable=True),
        "high": FieldSpec("high", "float64", "point", nullable=True),
        "low": FieldSpec("low", "float64", "point", nullable=True),
        "close": FieldSpec("close", "float64", "point", nullable=True),
        "pre_close": FieldSpec("pre_close", "float64", "point", nullable=True),
        "change": FieldSpec("change", "float64", "point", nullable=True),
        "pct_chg": FieldSpec("pct_chg", "float64", "pct", nullable=True),
        "vol": FieldSpec("vol", "float64", "share", nullable=True),
        "amount": FieldSpec("amount", "float64", "yuan", nullable=True),
    },
    source_mapping={
        "tushare": {f: f for f in [
            "ts_code", "trade_date", "open", "high", "low", "close",
            "pre_close", "change", "pct_chg", "vol", "amount",
        ]},
    },
)
