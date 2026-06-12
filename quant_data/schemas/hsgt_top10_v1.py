"""Schema for ``hsgt_top10`` (tushare `pro.hsgt_top10`) — v1.

Per-day top-10 northbound (北向) net-buy stocks. PK: ``(trade_date, ts_code)``
— up to 10 rows per trading day, but ``ts_code`` is the natural key for
joining against ``mv_daily_v1``.

Unit semantics (tushare dry-run 2026-06-12, ``trade_date=20240604``):
  - ``close`` : yuan
  - ``change`` : percent
  - ``amount`` / ``net_amount`` / ``buy`` / ``sell`` : yuan (NOT 万元)
  - ``rank`` : int 1-10
  - ``market_type`` : int (1=沪, 3=深) — NOT a string

Actual columns returned by tushare (11):
  trade_date, ts_code, name, close, change, rank, market_type,
  amount, net_amount, buy, sell
"""
from quant_data.sources.base import FieldSpec, TableSchema

HSGT_TOP10_V1 = TableSchema(
    table="hsgt_top10",
    version="v1",
    primary_key=["trade_date", "ts_code"],
    fields={
        "trade_date": FieldSpec("trade_date", "date", "calendar", nullable=False),
        "ts_code": FieldSpec("ts_code", "string", "code", nullable=False),
        "name": FieldSpec("name", "string", "name", nullable=True),
        "close": FieldSpec("close", "float64", "yuan", nullable=True),
        "change": FieldSpec("change", "float64", "percent", nullable=True),
        "rank": FieldSpec("rank", "int32", "rank", nullable=True,
                          description="1-10, 1 = top net buy"),
        "market_type": FieldSpec("market_type", "int32", "code", nullable=True,
                                 description="沪/深 (1=沪, 3=深)"),
        "amount": FieldSpec("amount", "float64", "yuan", nullable=True,
                            description="总成交额 (元)"),
        "net_amount": FieldSpec("net_amount", "float64", "yuan", nullable=True,
                                description="净买额 (元)"),
        "buy": FieldSpec("buy", "float64", "yuan", nullable=True,
                         description="买入额 (元)"),
        "sell": FieldSpec("sell", "float64", "yuan", nullable=True,
                          description="卖出额 (元)"),
    },
    source_mapping={
        "tushare": {f: f for f in [
            "trade_date", "ts_code", "name", "close", "change", "rank", "market_type",
            "amount", "net_amount", "buy", "sell",
        ]},
    },
)
