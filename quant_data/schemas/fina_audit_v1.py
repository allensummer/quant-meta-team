"""Schema for ``fina_audit`` (tushare `pro.fina_audit`) — v1.

审计意见。PK: ``(ts_code, end_date)`` —— end_date = 审计对应报告期。
"""
from quant_data.sources.base import FieldSpec, TableSchema

FINA_AUDIT_V1 = TableSchema(
    table="fina_audit",
    version="v1",
    primary_key=["ts_code", "end_date"],
    fields={
        "ts_code": FieldSpec("ts_code", "string", "code", nullable=False),
        "end_date": FieldSpec("end_date", "date", "calendar", nullable=False,
                              description="报告期"),
        "ann_date": FieldSpec("ann_date", "date", "calendar", nullable=True,
                              description="公告日"),
        "audit_result": FieldSpec("audit_result", "string", "category", nullable=True,
                                  description="审计意见 (标准/带强调/保留/无法表示)"),
        "audit_firm": FieldSpec("audit_firm", "string", "name", nullable=True,
                                description="审计机构"),
        "audit_sign": FieldSpec("audit_sign", "string", "text", nullable=True,
                                description="审计签字人"),
        "audit_date": FieldSpec("audit_date", "date", "calendar", nullable=True,
                                description="审计报告日期"),
    },
    source_mapping={
        "tushare": {f: f for f in [
            "ts_code", "end_date", "ann_date", "audit_result",
            "audit_firm", "audit_sign", "audit_date",
        ]},
    },
)
