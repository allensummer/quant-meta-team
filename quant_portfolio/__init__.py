"""quant_portfolio — portfolio / factor / selection layer (Week 3A).

This package is the **consumer** of ``quant_data``. It MUST NOT import
``tushare`` / ``akshare`` or any other data-source SDK. All data access
goes through ``quant_data.store.duckdb_store.DuckDBStore`` (DuckDB views
``mv_daily_qfq`` / ``mv_daily_v1`` / ``mv_daily_hfq`` / ``mv_trade_cal``).

Public surface (Week 3A):
  - ``data_layer.PortfolioDataLayer`` : thin wrapper over DuckDBStore.query()
  - ``data_layer.FactorSpec``          : factor definition dataclass
  - ``examples.factor_momentum_reversal`` : runnable demo
"""
from __future__ import annotations

__version__ = "0.1.0"
__all__ = ["data_layer", "examples"]
