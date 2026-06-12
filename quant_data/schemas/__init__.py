"""Schema registry (v0.4 §3.2 + §6.1, expanded v0.8 with 5 S-tier topics and v0.9 with 20 A-tier topics)."""
from quant_data.sources.base import TableSchema
from quant_data.schemas.daily_v1 import DAILY_V1
from quant_data.schemas.adj_factor_v1 import ADJ_FACTOR_V1
from quant_data.schemas.daily_basic_v1 import DAILY_BASIC_V1
from quant_data.schemas.trade_cal_v1 import TRADE_CAL_V1
from quant_data.schemas.stock_basic_v1 import STOCK_BASIC_V1
from quant_data.schemas.moneyflow_v1 import MONEYFLOW_V1
from quant_data.schemas.moneyflow_hsgt_v1 import MONEYFLOW_HSGT_V1
from quant_data.schemas.index_weight_v1 import INDEX_WEIGHT_V1
from quant_data.schemas.hsgt_top10_v1 import HSGT_TOP10_V1
from quant_data.schemas.fund_holdings_v1 import FUND_HOLDINGS_V1
# A-tier Batch 1 — 基础 + 事件 (v0.9 — ADM-653)
from quant_data.schemas.index_classify_v1 import INDEX_CLASSIFY_V1
from quant_data.schemas.index_daily_v1 import INDEX_DAILY_V1
from quant_data.schemas.index_member_v1 import INDEX_MEMBER_V1
from quant_data.schemas.sw_index_v1 import SW_INDEX_V1
from quant_data.schemas.stk_limit_v1 import STK_LIMIT_V1
from quant_data.schemas.suspend_v1 import SUSPEND_V1
from quant_data.schemas.dividend_v1 import DIVIDEND_V1
from quant_data.schemas.shares_float_v1 import SHARES_FLOAT_V1
# A-tier Batch 2 — 财务三联表 + 财务指标
from quant_data.schemas.fina_indicator_v1 import FINA_INDICATOR_V1
from quant_data.schemas.income_v1 import INCOME_V1
from quant_data.schemas.balancesheet_v1 import BALANCESHEET_V1
from quant_data.schemas.cashflow_v1 import CASHFLOW_V1
from quant_data.schemas.fina_mainbz_v1 import FINA_MAINBZ_V1
from quant_data.schemas.fina_audit_v1 import FINA_AUDIT_V1
from quant_data.schemas.top10_holders_v1 import TOP10_HOLDERS_V1
# A-tier Batch 3 — 资金流 + 研报 + 股东
from quant_data.schemas.top_list_v1 import TOP_LIST_V1
from quant_data.schemas.margin_detail_v1 import MARGIN_DETAIL_V1
from quant_data.schemas.top10_floatholders_v1 import TOP10_FLOATHOLDERS_V1
from quant_data.schemas.stk_holdertrade_v1 import STK_HOLDERTRADE_V1
from quant_data.schemas.report_rc_v1 import REPORT_RC_V1

__all__ = [
    "DAILY_V1",
    "ADJ_FACTOR_V1",
    "DAILY_BASIC_V1",
    "TRADE_CAL_V1",
    "STOCK_BASIC_V1",
    "MONEYFLOW_V1",
    "MONEYFLOW_HSGT_V1",
    "INDEX_WEIGHT_V1",
    "HSGT_TOP10_V1",
    "FUND_HOLDINGS_V1",
    # A-tier Batch 1
    "INDEX_CLASSIFY_V1",
    "INDEX_DAILY_V1",
    "INDEX_MEMBER_V1",
    "SW_INDEX_V1",
    "STK_LIMIT_V1",
    "SUSPEND_V1",
    "DIVIDEND_V1",
    "SHARES_FLOAT_V1",
    # A-tier Batch 2
    "FINA_INDICATOR_V1",
    "INCOME_V1",
    "BALANCESHEET_V1",
    "CASHFLOW_V1",
    "FINA_MAINBZ_V1",
    "FINA_AUDIT_V1",
    "TOP10_HOLDERS_V1",
    # A-tier Batch 3
    "TOP_LIST_V1",
    "MARGIN_DETAIL_V1",
    "TOP10_FLOATHOLDERS_V1",
    "STK_HOLDERTRADE_V1",
    "REPORT_RC_V1",
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
    # S-tier (v0.8 — ADM-652)
    ("moneyflow", "v1"): MONEYFLOW_V1,
    ("moneyflow_hsgt", "v1"): MONEYFLOW_HSGT_V1,
    ("index_weight", "v1"): INDEX_WEIGHT_V1,
    ("hsgt_top10", "v1"): HSGT_TOP10_V1,
    ("fund_holdings", "v1"): FUND_HOLDINGS_V1,
    # A-tier Batch 1 (v0.9 — ADM-653)
    ("index_classify", "v1"): INDEX_CLASSIFY_V1,
    ("index_daily", "v1"): INDEX_DAILY_V1,
    ("index_member", "v1"): INDEX_MEMBER_V1,
    ("sw_index", "v1"): SW_INDEX_V1,
    ("stk_limit", "v1"): STK_LIMIT_V1,
    ("suspend", "v1"): SUSPEND_V1,
    ("dividend", "v1"): DIVIDEND_V1,
    ("shares_float", "v1"): SHARES_FLOAT_V1,
    # A-tier Batch 2
    ("fina_indicator", "v1"): FINA_INDICATOR_V1,
    ("income", "v1"): INCOME_V1,
    ("balancesheet", "v1"): BALANCESHEET_V1,
    ("cashflow", "v1"): CASHFLOW_V1,
    ("fina_mainbz", "v1"): FINA_MAINBZ_V1,
    ("fina_audit", "v1"): FINA_AUDIT_V1,
    ("top10_holders", "v1"): TOP10_HOLDERS_V1,
    # A-tier Batch 3
    ("top_list", "v1"): TOP_LIST_V1,
    ("margin_detail", "v1"): MARGIN_DETAIL_V1,
    ("top10_floatholders", "v1"): TOP10_FLOATHOLDERS_V1,
    ("stk_holdertrade", "v1"): STK_HOLDERTRADE_V1,
    ("report_rc", "v1"): REPORT_RC_V1,
}


def get_schema(topic: str, version: str = "v1") -> TableSchema:
    key = (topic, version)
    if key not in SCHEMAS:
        raise KeyError(f"no schema registered for {key}; have {list(SCHEMAS)}")
    return SCHEMAS[key]
