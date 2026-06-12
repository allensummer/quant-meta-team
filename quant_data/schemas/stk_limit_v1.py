"""Schema for ``stk_limit`` (tushare `pro.stk_limit`) — v1.

每日涨跌停价。PK: ``(ts_code, trade_date)``。
"""
from quant_data.sources.base import FieldSpec, TableSchema

STK_LIMIT_V1 = TableSchema(
    table="stk_limit",
    version="v1",
    primary_key=["ts_code", "trade_date"],
    fields={
        "ts_code": FieldSpec("ts_code", "string", "code", nullable=False),
        "trade_date": FieldSpec("trade_date", "date", "calendar", nullable=False),
        "up_limit": FieldSpec("up_limit", "float64", "yuan", nullable=True,
                              description="涨停价"),
        "down_limit": FieldSpec("down_limit", "float64", "yuan", nullable=True,
                                description="跌停价"),
    },
    source_mapping={
        "tushare": {f: f for f in [
            "ts_code", "trade_date", "up_limit", "down_limit",
        ]},
    },
)
