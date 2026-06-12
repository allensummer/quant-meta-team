"""Schema for ``fund_holdings`` (tushare `pro.fund_holdings`) — v1.

Quarterly fund top-10 holdings (公募基金披露的季报前 10 大重仓股).
PK: ``(ts_code, ann_date, end_date, symbol)`` — fully identifies a single
holding row.

Sync is keyed on ``end_date`` (quarterly report period), NOT ``trade_date``.
The cursor therefore tracks the last successfully-synced ``end_date``;
incremental syncs resume from cursor+1.

Unit semantics (tushare doc):
  - ``mkv`` : 持仓市值 (万元) — view layer normalizes to yuan
  - ``amount`` : 持仓股数 (shares)
  - ``stk_mkv_ratio`` : 占基金净值比 (%)
"""
from quant_data.sources.base import FieldSpec, TableSchema

FUND_HOLDINGS_V1 = TableSchema(
    table="fund_holdings",
    version="v1",
    primary_key=["ts_code", "ann_date", "end_date", "symbol"],
    fields={
        "ts_code": FieldSpec("ts_code", "string", "code", nullable=False,
                             description="基金代码, e.g. 519983.OF"),
        "ann_date": FieldSpec("ann_date", "date", "calendar", nullable=False,
                              description="公告日期 (披露日期)"),
        "end_date": FieldSpec("end_date", "date", "calendar", nullable=False,
                              description="季报期 (持仓截止日, e.g. 20250930)"),
        "symbol": FieldSpec("symbol", "string", "code", nullable=False,
                            description="持仓股票代码 (ts_code-style)"),
        "stk_name": FieldSpec("stk_name", "string", "name", nullable=True,
                              description="持仓股票名称"),
        "mkv": FieldSpec("mkv", "float64", "wan_yuan", nullable=True,
                         description="持仓市值 (万元)"),
        "amount": FieldSpec("amount", "float64", "share", nullable=True,
                            description="持仓股数 (shares)"),
        "stk_mkv_ratio": FieldSpec("stk_mkv_ratio", "float64", "percent", nullable=True,
                                   description="占基金净值比 (%)"),
    },
    source_mapping={
        "tushare": {f: f for f in [
            "ts_code", "ann_date", "end_date", "symbol", "stk_name",
            "mkv", "amount", "stk_mkv_ratio",
        ]},
    },
)
