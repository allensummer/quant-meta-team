"""Schema for ``suspend`` (tushare `pro.suspend`) — v1.

停复牌事件。PK: ``(ts_code, suspend_date)``。
"""
from quant_data.sources.base import FieldSpec, TableSchema

SUSPEND_V1 = TableSchema(
    table="suspend",
    version="v1",
    primary_key=["ts_code", "suspend_date"],
    fields={
        "ts_code": FieldSpec("ts_code", "string", "code", nullable=False),
        "suspend_date": FieldSpec("suspend_date", "date", "calendar", nullable=False,
                                  description="停牌日期"),
        "resume_date": FieldSpec("resume_date", "date", "calendar", nullable=True,
                                 description="复牌日期 (NULL=尚未复牌)"),
        "suspend_type": FieldSpec("suspend_type", "string", "category", nullable=True,
                                  description="停牌类型"),
        "reason": FieldSpec("reason", "string", "text", nullable=True,
                            description="停牌原因"),
    },
    source_mapping={
        "tushare": {f: f for f in [
            "ts_code", "suspend_date", "resume_date", "suspend_type", "reason",
        ]},
    },
)
