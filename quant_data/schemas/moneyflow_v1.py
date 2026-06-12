"""Schema for ``moneyflow`` (tushare `pro.moneyflow`) — v1.

Per-stock-per-day main/small order money flow. PK: ``(ts_code, trade_date)``.

Unit semantics (v0.4 §5 + tushare doc):
  - ``buy_*_vol`` / ``sell_*_vol`` : 1 手 = 100 股 (tushare native)
  - ``buy_*_amount`` / ``sell_*_amount`` : 千元 (tushare native) — view layer normalizes to yuan
  - ``net_mf_vol`` / ``net_mf_amount`` : 净流入 (千元 for amount)
  - size buckets: sm (<4万) / md (4-20万) / lg (20-100万) / elg (>100万) per tushare 文档
"""
from quant_data.sources.base import FieldSpec, TableSchema

MONEYFLOW_V1 = TableSchema(
    table="moneyflow",
    version="v1",
    primary_key=["ts_code", "trade_date"],
    fields={
        "ts_code": FieldSpec("ts_code", "string", "code", nullable=False),
        "trade_date": FieldSpec("trade_date", "date", "calendar", nullable=False),
        # 小单 (small)
        "buy_sm_vol": FieldSpec("buy_sm_vol", "float64", "lot", nullable=True,
                                description="小单买入量 (手)"),
        "buy_sm_amount": FieldSpec("buy_sm_amount", "float64", "kilo_yuan", nullable=True,
                                   description="小单买入金额 (千元)"),
        "sell_sm_vol": FieldSpec("sell_sm_vol", "float64", "lot", nullable=True),
        "sell_sm_amount": FieldSpec("sell_sm_amount", "float64", "kilo_yuan", nullable=True),
        # 中单 (medium)
        "buy_md_vol": FieldSpec("buy_md_vol", "float64", "lot", nullable=True),
        "buy_md_amount": FieldSpec("buy_md_amount", "float64", "kilo_yuan", nullable=True),
        "sell_md_vol": FieldSpec("sell_md_vol", "float64", "lot", nullable=True),
        "sell_md_amount": FieldSpec("sell_md_amount", "float64", "kilo_yuan", nullable=True),
        # 大单 (large)
        "buy_lg_vol": FieldSpec("buy_lg_vol", "float64", "lot", nullable=True),
        "buy_lg_amount": FieldSpec("buy_lg_amount", "float64", "kilo_yuan", nullable=True),
        "sell_lg_vol": FieldSpec("sell_lg_vol", "float64", "lot", nullable=True),
        "sell_lg_amount": FieldSpec("sell_lg_amount", "float64", "kilo_yuan", nullable=True),
        # 超大单 (extra large)
        "buy_elg_vol": FieldSpec("buy_elg_vol", "float64", "lot", nullable=True),
        "buy_elg_amount": FieldSpec("buy_elg_amount", "float64", "kilo_yuan", nullable=True),
        "sell_elg_vol": FieldSpec("sell_elg_vol", "float64", "lot", nullable=True),
        "sell_elg_amount": FieldSpec("sell_elg_amount", "float64", "kilo_yuan", nullable=True),
        # 净流入
        "net_mf_vol": FieldSpec("net_mf_vol", "float64", "lot", nullable=True,
                                description="净流入量 (手)"),
        "net_mf_amount": FieldSpec("net_mf_amount", "float64", "kilo_yuan", nullable=True,
                                   description="净流入金额 (千元)"),
    },
    source_mapping={
        "tushare": {f: f for f in [
            "ts_code", "trade_date",
            "buy_sm_vol", "buy_sm_amount", "sell_sm_vol", "sell_sm_amount",
            "buy_md_vol", "buy_md_amount", "sell_md_vol", "sell_md_amount",
            "buy_lg_vol", "buy_lg_amount", "sell_lg_vol", "sell_lg_amount",
            "buy_elg_vol", "buy_elg_amount", "sell_elg_vol", "sell_elg_amount",
            "net_mf_vol", "net_mf_amount",
        ]},
    },
)
