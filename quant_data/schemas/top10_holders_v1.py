"""Schema for ``top10_holders`` (tushare `pro.top10_holders`) — v1.

前十大股东(总股本口径)。PK: ``(ts_code, ann_date, end_date, holder_name)``。
"""
from quant_data.sources.base import FieldSpec, TableSchema

TOP10_HOLDERS_V1 = TableSchema(
    table="top10_holders",
    version="v1",
    primary_key=["ts_code", "ann_date", "end_date", "holder_name"],
    fields={
        "ts_code": FieldSpec("ts_code", "string", "code", nullable=False),
        "ann_date": FieldSpec("ann_date", "date", "calendar", nullable=False,
                              description="公告日"),
        "end_date": FieldSpec("end_date", "date", "calendar", nullable=False,
                              description="报告期"),
        "holder_name": FieldSpec("holder_name", "string", "name", nullable=False,
                                 description="股东名称"),
        "hold_amount": FieldSpec("hold_amount", "float64", "share", nullable=True,
                                 description="持股数量 (股)"),
        "hold_ratio": FieldSpec("hold_ratio", "float64", "pct", nullable=True,
                                description="占总股本比例"),
        "hold_float_ratio": FieldSpec("hold_float_ratio", "float64", "pct", nullable=True,
                                      description="占流通股本比例"),
        "hold_change": FieldSpec("hold_change", "float64", "share", nullable=True,
                                description="持股变动 (股)"),
        "holder_type": FieldSpec("holder_type", "string", "category", nullable=True,
                                 description="股东类型"),
    },
    source_mapping={
        "tushare": {f: f for f in [
            "ts_code", "ann_date", "end_date", "holder_name",
            "hold_amount", "hold_ratio", "hold_float_ratio",
            "hold_change", "holder_type",
        ]},
    },
)
