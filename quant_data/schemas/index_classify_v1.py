"""Schema for ``index_classify`` (tushare `pro.index_classify`) — v1.

申万 / 中证 / 沪深指数树状分类。Snapshot 接口,单次拉取全市场。PK: ``index_code``。
"""
from quant_data.sources.base import FieldSpec, TableSchema

INDEX_CLASSIFY_V1 = TableSchema(
    table="index_classify",
    version="v1",
    primary_key=["index_code"],
    fields={
        "index_code": FieldSpec("index_code", "string", "code", nullable=False,
                                description="指数代码 (e.g. 801010.SI)"),
        "index_name": FieldSpec("index_name", "string", "name", nullable=True,
                                description="指数名称"),
        "industry_name": FieldSpec("industry_name", "string", "name", nullable=True,
                                   description="行业名称 (申万)"),
        "level": FieldSpec("level", "string", "category", nullable=True,
                           description="L1/L2/L3"),
        "is_published": FieldSpec("is_published", "int32", "flag", nullable=True,
                                  description="是否发布 (1=是)"),
        "src": FieldSpec("src", "string", "category", nullable=True,
                         description="指数源 (SW / CSI / SSE / SZSE)"),
        "weight_rule": FieldSpec("weight_rule", "string", "rule", nullable=True,
                                 description="加权方式"),
        "exchange": FieldSpec("exchange", "string", "exchange", nullable=True,
                              description="交易所"),
        "list_date": FieldSpec("list_date", "date", "calendar", nullable=True,
                               description="发布日期"),
        "exp_date": FieldSpec("exp_date", "date", "calendar", nullable=True,
                              description="停止发布日期"),
    },
    source_mapping={
        "tushare": {f: f for f in [
            "index_code", "index_name", "industry_name", "level",
            "is_published", "src", "weight_rule", "exchange",
            "list_date", "exp_date",
        ]},
    },
)
