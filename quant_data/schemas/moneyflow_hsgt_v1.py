"""Schema for ``moneyflow_hsgt`` (tushare `pro.moneyflow_hsgt`) — v1.

Per-day cross-border (沪深港通) capital flow snapshot. PK: ``(trade_date)``
(single-row per trading day; no per-stock breakdown at this granularity —
that's what ``hsgt_top10`` is for).

Unit semantics (tushare doc):
  - ``ggt_ss`` / ``ggt_sz`` / ``hgt`` / ``sgt`` / ``north_money`` / ``south_money`` : 亿元
"""
from quant_data.sources.base import FieldSpec, TableSchema

MONEYFLOW_HSGT_V1 = TableSchema(
    table="moneyflow_hsgt",
    version="v1",
    primary_key=["trade_date"],
    fields={
        "trade_date": FieldSpec("trade_date", "date", "calendar", nullable=False),
        "ggt_ss": FieldSpec("ggt_ss", "float64", "yi_yuan", nullable=True,
                            description="港股通(沪) 成交净买额 (亿元)"),
        "ggt_sz": FieldSpec("ggt_sz", "float64", "yi_yuan", nullable=True,
                            description="港股通(深) 成交净买额 (亿元)"),
        "hgt": FieldSpec("hgt", "float64", "yi_yuan", nullable=True,
                         description="沪股通 成交净买额 (亿元)"),
        "sgt": FieldSpec("sgt", "float64", "yi_yuan", nullable=True,
                         description="深股通 成交净买额 (亿元)"),
        "north_money": FieldSpec("north_money", "float64", "yi_yuan", nullable=True,
                                 description="北向资金 净买额 (亿元) = hgt + sgt"),
        "south_money": FieldSpec("south_money", "float64", "yi_yuan", nullable=True,
                                 description="南向资金 净买额 (亿元) = ggt_ss + ggt_sz"),
    },
    source_mapping={
        "tushare": {f: f for f in [
            "trade_date", "ggt_ss", "ggt_sz", "hgt", "sgt", "north_money", "south_money",
        ]},
    },
)
