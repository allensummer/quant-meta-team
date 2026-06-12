"""Schema for ``fina_mainbz`` (tushare `pro.fina_mainbz`) — v1.

主营业务构成(按产品/区域/行业拆分)。PK: ``(ts_code, end_date, bz_item)``。
"""
from quant_data.sources.base import FieldSpec, TableSchema

FINA_MAINBZ_V1 = TableSchema(
    table="fina_mainbz",
    version="v1",
    primary_key=["ts_code", "end_date", "bz_item"],
    fields={
        "ts_code": FieldSpec("ts_code", "string", "code", nullable=False),
        "end_date": FieldSpec("end_date", "date", "calendar", nullable=False,
                              description="报告期"),
        "bz_item": FieldSpec("bz_item", "string", "name", nullable=False,
                             description="主营业务项目 (产品/行业/地区)"),
        "bz_code": FieldSpec("bz_code", "string", "code", nullable=True,
                             description="业务代码"),
        "bz_sales": FieldSpec("bz_sales", "float64", "yuan", nullable=True,
                              description="营业收入"),
        "bz_profit": FieldSpec("bz_profit", "float64", "yuan", nullable=True,
                               description="营业利润"),
        "bz_cost": FieldSpec("bz_cost", "float64", "yuan", nullable=True,
                             description="营业成本"),
        "curr_type": FieldSpec("curr_type", "string", "currency", nullable=True,
                               description="货币"),
    },
    source_mapping={
        "tushare": {f: f for f in [
            "ts_code", "end_date", "bz_item", "bz_code",
            "bz_sales", "bz_profit", "bz_cost", "curr_type",
        ]},
    },
)
