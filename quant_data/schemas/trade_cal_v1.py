"""Schema for ``trade_cal`` (tushare `pro.trade_cal`) — v1."""
from quant_data.sources.base import FieldSpec, TableSchema

TRADE_CAL_V1 = TableSchema(
    table="trade_cal",
    version="v1",
    primary_key=["exchange", "cal_date"],
    fields={
        "exchange": FieldSpec("exchange", "string", "code", nullable=False, description="SSE / SZSE / BSE"),
        "cal_date": FieldSpec("cal_date", "date", "calendar", nullable=False),
        "is_open": FieldSpec("is_open", "int8", "flag", nullable=False, description="1=open, 0=closed"),
        "pretrade_date": FieldSpec("pretrade_date", "date", "calendar", nullable=True),
    },
    source_mapping={
        "tushare": {
            "exchange": "exchange",
            "cal_date": "cal_date",
            "is_open": "is_open",
            "pretrade_date": "pretrade_date",
        },
    },
)
