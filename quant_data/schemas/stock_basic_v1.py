"""Schema for ``stock_basic`` (tushare `pro.stock_basic`) — v1.

The stock universe is snapshot-shaped (not time-series) — we re-sync it on
every daily run to capture IPO / delist events.
"""
from quant_data.sources.base import FieldSpec, TableSchema

STOCK_BASIC_V1 = TableSchema(
    table="stock_basic",
    version="v1",
    primary_key=["ts_code"],
    fields={
        "ts_code": FieldSpec("ts_code", "string", "code", nullable=False),
        "symbol": FieldSpec("symbol", "string", "code", nullable=False),
        "name": FieldSpec("name", "string", "name", nullable=False),
        "industry": FieldSpec("industry", "string", "industry", nullable=True),
        "fullname": FieldSpec("fullname", "string", "name", nullable=True),
        "enname": FieldSpec("enname", "string", "name", nullable=True),
        "cnspell": FieldSpec("cnspell", "string", "code", nullable=True),
        "exchange": FieldSpec("exchange", "string", "code", nullable=False),
        "curr_type": FieldSpec("curr_type", "string", "code", nullable=False),
        "list_status": FieldSpec("list_status", "string", "code", nullable=False, description="L=上市 D=退市 P=暂停"),
        "list_date": FieldSpec("list_date", "date", "calendar", nullable=True),
        "delist_date": FieldSpec("delist_date", "date", "calendar", nullable=True),
        "is_hs": FieldSpec("is_hs", "string", "flag", nullable=True, description="沪深港通 N/H/S"),
    },
    source_mapping={
        "tushare": {
            "ts_code": "ts_code",
            "symbol": "symbol",
            "name": "name",
            "industry": "industry",
            "fullname": "fullname",
            "enname": "enname",
            "cnspell": "cnspell",
            "exchange": "exchange",
            "curr_type": "curr_type",
            "list_status": "list_status",
            "list_date": "list_date",
            "delist_date": "delist_date",
            "is_hs": "is_hs",
        },
    },
)
