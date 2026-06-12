"""Schema for ``stk_holdertrade`` (tushare `pro.stk_holdertrade`) — v1.

股东增减持(董监高 / 重要股东)。PK: ``(ts_code, ann_date, holder_name, trade_date)``。
"""
from quant_data.sources.base import FieldSpec, TableSchema

STK_HOLDERTRADE_V1 = TableSchema(
    table="stk_holdertrade",
    version="v1",
    primary_key=["ts_code", "ann_date", "holder_name", "trade_date"],
    fields={
        "ts_code": FieldSpec("ts_code", "string", "code", nullable=False),
        "ann_date": FieldSpec("ann_date", "date", "calendar", nullable=False,
                              description="公告日"),
        "holder_name": FieldSpec("holder_name", "string", "name", nullable=False),
        "holder_type": FieldSpec("holder_type", "string", "category", nullable=True,
                                 description="股东类型 (董监高/重要股东)"),
        "in_de": FieldSpec("in_de", "string", "category", nullable=False,
                           description="增减持方向 (IN/DE)"),
        "change_vol": FieldSpec("change_vol", "float64", "share", nullable=True,
                                description="变动数量 (股)"),
        "change_ratio": FieldSpec("change_ratio", "float64", "pct", nullable=True,
                                  description="变动比例"),
        "after_share": FieldSpec("after_share", "float64", "share", nullable=True,
                                 description="变动后持股"),
        "after_ratio": FieldSpec("after_ratio", "float64", "pct", nullable=True,
                                 description="变动后持股比例"),
        "avg_price": FieldSpec("avg_price", "float64", "yuan", nullable=True,
                               description="平均成交价"),
        "total_fee": FieldSpec("total_fee", "float64", "yuan", nullable=True,
                               description="总成交额"),
        "trade_date": FieldSpec("trade_date", "date", "calendar", nullable=False,
                                description="成交日期"),
    },
    source_mapping={
        "tushare": {f: f for f in [
            "ts_code", "ann_date", "holder_name", "holder_type", "in_de",
            "change_vol", "change_ratio", "after_share", "after_ratio",
            "avg_price", "total_fee", "trade_date",
        ]},
    },
)
