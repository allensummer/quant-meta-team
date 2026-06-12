"""Schema for ``index_weight`` (tushare `pro.index_weight`) — v1.

Per-index constituent weights. PK: ``(index_code, con_code, trade_date)``.

The tushare API requires ``index_code``; we iterate over a fixed index pool
(see ``sync_index_weight``) × trade_date. 中证/沪深 indices update monthly,
申万 updates daily — both are handled by the same daily loop (empty days
are no-ops).

Unit semantics:
  - ``weight`` : percent (%)
"""
from quant_data.sources.base import FieldSpec, TableSchema

INDEX_WEIGHT_V1 = TableSchema(
    table="index_weight",
    version="v1",
    primary_key=["index_code", "con_code", "trade_date"],
    fields={
        "index_code": FieldSpec("index_code", "string", "code", nullable=False,
                                description="指数代码, e.g. 000300.SH (沪深300)"),
        "con_code": FieldSpec("con_code", "string", "code", nullable=False,
                              description="成分股代码, ts_code-style (e.g. 600519.SH)"),
        "trade_date": FieldSpec("trade_date", "date", "calendar", nullable=False),
        "weight": FieldSpec("weight", "float64", "percent", nullable=True,
                            description="成分股权重 (%)"),
    },
    source_mapping={
        "tushare": {f: f for f in ["index_code", "con_code", "trade_date", "weight"]},
    },
)
