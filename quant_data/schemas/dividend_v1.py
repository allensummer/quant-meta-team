"""Schema for ``dividend`` (tushare `pro.dividend`) — v1.

分红送股。PK: ``(ts_code, end_date)`` —— end_date 是分红对应的会计期末。
"""
from quant_data.sources.base import FieldSpec, TableSchema

DIVIDEND_V1 = TableSchema(
    table="dividend",
    version="v1",
    primary_key=["ts_code", "end_date"],
    fields={
        "ts_code": FieldSpec("ts_code", "string", "code", nullable=False),
        "end_date": FieldSpec("end_date", "date", "calendar", nullable=False,
                              description="分红对应会计期末"),
        "ann_date": FieldSpec("ann_date", "date", "calendar", nullable=True,
                              description="预案公告日"),
        "div_proc": FieldSpec("div_proc", "string", "stage", nullable=True,
                              description="分红进程 (预案/实施/取消)"),
        "stk_div": FieldSpec("stk_div", "float64", "share", nullable=True,
                             description="每股送转 (股)"),
        "stk_bo_rate": FieldSpec("stk_bo_rate", "float64", "share", nullable=True,
                                 description="每股送股"),
        "cash_div": FieldSpec("cash_div", "float64", "yuan", nullable=True,
                              description="每股派现 (税前, 元)"),
        "cash_div_tax": FieldSpec("cash_div_tax", "float64", "yuan", nullable=True,
                                  description="每股派现 (税后, 元)"),
        "record_date": FieldSpec("record_date", "date", "calendar", nullable=True,
                                 description="股权登记日"),
        "ex_date": FieldSpec("ex_date", "date", "calendar", nullable=True,
                             description="除权除息日"),
        "pay_date": FieldSpec("pay_date", "date", "calendar", nullable=True,
                              description="派现日"),
    },
    source_mapping={
        "tushare": {f: f for f in [
            "ts_code", "end_date", "ann_date", "div_proc", "stk_div",
            "stk_bo_rate", "cash_div", "cash_div_tax",
            "record_date", "ex_date", "pay_date",
        ]},
    },
)
