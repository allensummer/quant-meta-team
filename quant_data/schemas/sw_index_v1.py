"""Schema for ``sw_index`` (tushare `pro.sw_index`) — v1.

申万一级 / 二级 / 三级行业指数日线。PK: ``(index_code, trade_date)``。
"""
from quant_data.sources.base import FieldSpec, TableSchema

SW_INDEX_V1 = TableSchema(
    table="sw_index",
    version="v1",
    primary_key=["index_code", "trade_date"],
    fields={
        "index_code": FieldSpec("index_code", "string", "code", nullable=False),
        "index_name": FieldSpec("index_name", "string", "name", nullable=True),
        "level": FieldSpec("level", "int32", "category", nullable=True,
                           description="1=一级 2=二级 3=三级"),
        "trade_date": FieldSpec("trade_date", "date", "calendar", nullable=False),
        "open": FieldSpec("open", "float64", "point", nullable=True),
        "high": FieldSpec("high", "float64", "point", nullable=True),
        "low": FieldSpec("low", "float64", "point", nullable=True),
        "close": FieldSpec("close", "float64", "point", nullable=True),
        "change": FieldSpec("change", "float64", "point", nullable=True),
        "pct_chg": FieldSpec("pct_chg", "float64", "pct", nullable=True),
        "vol": FieldSpec("vol", "float64", "share", nullable=True),
        "amount": FieldSpec("amount", "float64", "yuan", nullable=True),
    },
    source_mapping={
        "tushare": {f: f for f in [
            "index_code", "index_name", "level", "trade_date",
            "open", "high", "low", "close", "change", "pct_chg",
            "vol", "amount",
        ]},
    },
)
