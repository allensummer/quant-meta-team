"""Schema for ``shares_float`` (tushare `pro.share_float`) — v1.

限售解禁事件。PK: ``(ts_code, float_date, holder_name)`` —— 同日同只股票可能
多笔不同持有人的解禁。
"""
from quant_data.sources.base import FieldSpec, TableSchema

SHARES_FLOAT_V1 = TableSchema(
    table="shares_float",
    version="v1",
    primary_key=["ts_code", "float_date", "holder_name"],
    fields={
        "ts_code": FieldSpec("ts_code", "string", "code", nullable=False),
        "float_date": FieldSpec("float_date", "date", "calendar", nullable=False,
                                description="解禁日期"),
        "float_share": FieldSpec("float_share", "float64", "share", nullable=True,
                                 description="解禁数量 (股)"),
        "float_ratio": FieldSpec("float_ratio", "float64", "pct", nullable=True,
                                 description="占总股本比例"),
        "holder_name": FieldSpec("holder_name", "string", "name", nullable=False,
                                 description="持有人名称"),
        "share_type": FieldSpec("share_type", "string", "category", nullable=True,
                                description="股份性质 (IPO 定增 股权激励等)"),
    },
    source_mapping={
        "tushare": {f: f for f in [
            "ts_code", "float_date", "float_share", "float_ratio",
            "holder_name", "share_type",
        ]},
    },
)
