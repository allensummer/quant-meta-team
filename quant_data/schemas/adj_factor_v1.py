"""Schema for ``adj_factor`` (tushare `pro.adj_factor`) — v1."""
from quant_data.sources.base import FieldSpec, TableSchema

ADJ_FACTOR_V1 = TableSchema(
    table="adj_factor",
    version="v1",
    primary_key=["ts_code", "trade_date"],
    fields={
        "ts_code": FieldSpec("ts_code", "string", "code", nullable=False),
        "trade_date": FieldSpec("trade_date", "date", "calendar", nullable=False),
        "adj_factor": FieldSpec("adj_factor", "float64", "ratio", nullable=False,
                                description="Per tushare: multiply close by this to get the latest-equivalent price (qfq)."),
    },
    source_mapping={"tushare": {"ts_code": "ts_code", "trade_date": "trade_date", "adj_factor": "adj_factor"}},
)
