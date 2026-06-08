"""Schema registry for the 5 core tables (v0.4 §3.2 + §6.1)."""
from quant_data.sources.base import TableSchema
from quant_data.schemas.daily_v1 import DAILY_V1
from quant_data.schemas.adj_factor_v1 import ADJ_FACTOR_V1
from quant_data.schemas.daily_basic_v1 import DAILY_BASIC_V1
from quant_data.schemas.trade_cal_v1 import TRADE_CAL_V1
from quant_data.schemas.stock_basic_v1 import STOCK_BASIC_V1

__all__ = [
    "DAILY_V1",
    "ADJ_FACTOR_V1",
    "DAILY_BASIC_V1",
    "TRADE_CAL_V1",
    "STOCK_BASIC_V1",
    "SCHEMAS",
    "get_schema",
]

# topic -> (version, schema)
SCHEMAS: dict[tuple[str, str], TableSchema] = {
    ("daily", "v1"): DAILY_V1,
    ("adj_factor", "v1"): ADJ_FACTOR_V1,
    ("daily_basic", "v1"): DAILY_BASIC_V1,
    ("trade_cal", "v1"): TRADE_CAL_V1,
    ("stock_basic", "v1"): STOCK_BASIC_V1,
}


def get_schema(topic: str, version: str = "v1") -> TableSchema:
    key = (topic, version)
    if key not in SCHEMAS:
        raise KeyError(f"no schema registered for {key}; have {list(SCHEMAS)}")
    return SCHEMAS[key]
