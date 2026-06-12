"""Schema for ``report_rc`` (tushare `pro.report_rc`) — v1.

研报内容(标题/评级/目标价/机构/作者)。PK: ``(ts_code, report_date, org_name, author_name)``。
"""
from quant_data.sources.base import FieldSpec, TableSchema

REPORT_RC_V1 = TableSchema(
    table="report_rc",
    version="v1",
    primary_key=["ts_code", "report_date", "org_name", "author_name"],
    fields={
        "ts_code": FieldSpec("ts_code", "string", "code", nullable=False),
        "name": FieldSpec("name", "string", "name", nullable=True,
                          description="股票名称"),
        "report_date": FieldSpec("report_date", "date", "calendar", nullable=False,
                                 description="研报发布日期"),
        "report_title": FieldSpec("report_title", "string", "text", nullable=True,
                                  description="研报标题"),
        "report_type": FieldSpec("report_type", "string", "category", nullable=True,
                                 description="研报类型 (深度/点评/事件)"),
        "org_name": FieldSpec("org_name", "string", "name", nullable=False,
                              description="研究机构"),
        "author_name": FieldSpec("author_name", "string", "name", nullable=False,
                                 description="作者"),
        "rating": FieldSpec("rating", "string", "category", nullable=True,
                            description="评级 (买入/增持/中性/减持/卖出)"),
        "rating_change": FieldSpec("rating_change", "string", "category", nullable=True,
                                   description="评级变化 (上调/下调/维持)"),
        "target_price": FieldSpec("target_price", "float64", "yuan", nullable=True,
                                  description="目标价"),
        "industry_name": FieldSpec("industry_name", "string", "name", nullable=True,
                                   description="行业"),
        "title_keyword": FieldSpec("title_keyword", "string", "text", nullable=True,
                                   description="标题关键词 (用于主题分类)"),
    },
    source_mapping={
        "tushare": {f: f for f in [
            "ts_code", "name", "report_date", "report_title", "report_type",
            "org_name", "author_name", "rating", "rating_change",
            "target_price", "industry_name", "title_keyword",
        ]},
    },
)
