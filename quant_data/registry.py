"""Source + Schema registries (v0.4 §6.2, v0.8 +5 S-tier, v0.9 +20 A-tier).

Downstream agents must only import from here. Adding a new data source is
a one-line registration: copy ``sources/_template.py`` and add a key to
``SOURCES``.
"""
from __future__ import annotations

import logging
import os
from typing import Mapping

from quant_data.schemas import (
    DAILY_V1,
    ADJ_FACTOR_V1,
    DAILY_BASIC_V1,
    TRADE_CAL_V1,
    STOCK_BASIC_V1,
    MONEYFLOW_V1,
    MONEYFLOW_HSGT_V1,
    INDEX_WEIGHT_V1,
    HSGT_TOP10_V1,
    FUND_HOLDINGS_V1,
    # A-tier Batch 1 (v0.9 — ADM-653)
    INDEX_CLASSIFY_V1,
    INDEX_DAILY_V1,
    INDEX_MEMBER_V1,
    SW_INDEX_V1,
    STK_LIMIT_V1,
    SUSPEND_V1,
    DIVIDEND_V1,
    SHARES_FLOAT_V1,
    # A-tier Batch 2
    FINA_INDICATOR_V1,
    INCOME_V1,
    BALANCESHEET_V1,
    CASHFLOW_V1,
    FINA_MAINBZ_V1,
    FINA_AUDIT_V1,
    TOP10_HOLDERS_V1,
    # A-tier Batch 3
    TOP_LIST_V1,
    MARGIN_DETAIL_V1,
    TOP10_FLOATHOLDERS_V1,
    STK_HOLDERTRADE_V1,
    REPORT_RC_V1,
    SCHEMAS,
    get_schema,
)
from quant_data.sources.akshare import AkshareAdapter
from quant_data.sources.tushare import TushareAdapter

log = logging.getLogger("quant_data.registry")


def _build_default_sources() -> dict[str, object]:
    """Construct the built-in adapters. Done lazily so tests can monkeypatch env first."""
    token = os.getenv("TUSHARE_TOKEN", "").strip()
    if not token:
        log.warning("TUSHARE_TOKEN is empty — TushareAdapter will fail healthcheck until set")
    return {
        "tushare": TushareAdapter(pro_token=token, tier=2000),
        # akshare is initialized lazily because it has heavy imports; see adapter.
    }


SOURCES: dict[str, object] = _build_default_sources()


def register_source(name: str, adapter: object) -> None:
    """Register a new DataSource. Callable from anywhere (e.g. tests)."""
    SOURCES[name] = adapter
    log.info("registered data source: %s (%s)", name, getattr(adapter, "version", "?"))


def get_source(name: str):
    s = SOURCES.get(name)
    if s is None:
        raise KeyError(f"no source {name!r}; have {list(SOURCES)}")
    if name == "akshare" and s is None:
        # lazy build on first access
        aks = AkshareAdapter()
        SOURCES["akshare"] = aks
        return aks
    return s


def list_sources() -> Mapping[str, object]:
    return SOURCES


__all__ = [
    "SOURCES",
    "SCHEMAS",
    "register_source",
    "get_source",
    "get_schema",
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
]
