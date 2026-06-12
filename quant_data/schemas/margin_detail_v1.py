"""Schema for ``margin_detail`` (tushare `pro.margin_detail`) — v1.

融资融券明细(按个股)。日频 × 5 千只 = 大数据量。PK: ``(trade_date, ts_code)``。
"""
from quant_data.sources.base import FieldSpec, TableSchema

MARGIN_DETAIL_V1 = TableSchema(
    table="margin_detail",
    version="v1",
    primary_key=["trade_date", "ts_code"],
    fields={
        "trade_date": FieldSpec("trade_date", "date", "calendar", nullable=False),
        "ts_code": FieldSpec("ts_code", "string", "code", nullable=False),
        "rzye": FieldSpec("rzye", "float64", "yuan", nullable=True,
                          description="融资余额"),
        "rqye": FieldSpec("rqye", "float64", "yuan", nullable=True,
                          description="融券余额"),
        "rzmre": FieldSpec("rzmre", "float64", "yuan", nullable=True,
                           description="融资买入额"),
        "rqyl": FieldSpec("rqyl", "float64", "share", nullable=True,
                          description="融券余量 (股)"),
        "rzche": FieldSpec("rzche", "float64", "yuan", nullable=True,
                           description="融资偿还额"),
        "rqchl": FieldSpec("rqchl", "float64", "share", nullable=True,
                           description="融券偿还量 (股)"),
        "rqmcl": FieldSpec("rqmcl", "float64", "share", nullable=True,
                           description="融券卖出量 (股)"),
        "rzrqye": FieldSpec("rzrqye", "float64", "yuan", nullable=True,
                            description="融资融券余额"),
    },
    source_mapping={
        "tushare": {f: f for f in [
            "trade_date", "ts_code", "rzye", "rqye", "rzmre", "rqyl",
            "rzche", "rqchl", "rqmcl", "rzrqye",
        ]},
    },
)
