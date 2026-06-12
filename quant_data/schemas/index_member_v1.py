"""Schema for ``index_member`` (tushare `pro.index_member`) — v1.

指数成分股调整历史。PK: ``(index_code, con_code, in_date)`` —— 同只股票可能
多次调入调出,in_date 区分每次调入。
"""
from quant_data.sources.base import FieldSpec, TableSchema

INDEX_MEMBER_V1 = TableSchema(
    table="index_member",
    version="v1",
    primary_key=["index_code", "con_code", "in_date"],
    fields={
        "index_code": FieldSpec("index_code", "string", "code", nullable=False,
                                description="指数代码"),
        "con_code": FieldSpec("con_code", "string", "code", nullable=False,
                              description="成分股代码"),
        "in_date": FieldSpec("in_date", "date", "calendar", nullable=False,
                             description="调入日期"),
        "out_date": FieldSpec("out_date", "date", "calendar", nullable=True,
                              description="调出日期 (NULL=仍在)"),
        "is_new": FieldSpec("is_new", "string", "flag", nullable=True,
                            description="Y=新调入 N=继续保留"),
    },
    source_mapping={
        "tushare": {f: f for f in [
            "index_code", "con_code", "in_date", "out_date", "is_new",
        ]},
    },
)
