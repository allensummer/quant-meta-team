"""Schema for ``top_list`` (tushare `pro.top_list`) — v1.

龙虎榜日榜。PK: ``(trade_date, ts_code)`` —— 同日同股可多次出现(不同榜单类型),
用 ts_code 单约束即可。
"""
from quant_data.sources.base import FieldSpec, TableSchema

TOP_LIST_V1 = TableSchema(
    table="top_list",
    version="v1",
    primary_key=["trade_date", "ts_code"],
    fields={
        "trade_date": FieldSpec("trade_date", "date", "calendar", nullable=False),
        "ts_code": FieldSpec("ts_code", "string", "code", nullable=False),
        "name": FieldSpec("name", "string", "name", nullable=True),
        "close": FieldSpec("close", "float64", "yuan", nullable=True),
        "pct_change": FieldSpec("pct_change", "float64", "pct", nullable=True),
        "turnover_rate": FieldSpec("turnover_rate", "float64", "pct", nullable=True),
        "amount": FieldSpec("amount", "float64", "yuan", nullable=True),
        "l_sell": FieldSpec("l_sell", "float64", "yuan", nullable=True,
                            description="龙虎榜卖出额"),
        "l_buy": FieldSpec("l_buy", "float64", "yuan", nullable=True,
                           description="龙虎榜买入额"),
        "l_amount": FieldSpec("l_amount", "float64", "yuan", nullable=True,
                              description="龙虎榜成交总额"),
        "net_amount": FieldSpec("net_amount", "float64", "yuan", nullable=True,
                                description="龙虎榜净额"),
        "net_rate": FieldSpec("net_rate", "float64", "pct", nullable=True),
        "amount_rate": FieldSpec("amount_rate", "float64", "pct", nullable=True),
        "float_values": FieldSpec("float_values", "float64", "yuan", nullable=True),
        "reason": FieldSpec("reason", "string", "text", nullable=True,
                            description="上榜原因"),
        "net_buy_amount": FieldSpec("net_buy_amount", "float64", "yuan", nullable=True),
        "sell_amount": FieldSpec("sell_amount", "float64", "yuan", nullable=True),
        "buy_amount": FieldSpec("buy_amount", "float64", "yuan", nullable=True),
    },
    source_mapping={
        "tushare": {f: f for f in [
            "trade_date", "ts_code", "name", "close", "pct_change",
            "turnover_rate", "amount", "l_sell", "l_buy", "l_amount",
            "net_amount", "net_rate", "amount_rate", "float_values",
            "reason", "net_buy_amount", "sell_amount", "buy_amount",
        ]},
    },
)
