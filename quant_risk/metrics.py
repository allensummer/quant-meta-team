"""Performance + risk metrics for backtest reports (v0.4 §9.6).

Pure functions; no I/O. All metrics accept a daily NAV / return series
(``pd.Series`` with a DatetimeIndex) and return ``float`` (or ``nan`` if
the input is degenerate). Conventions:

  - ``returns``: simple daily returns, ``r_t = nav_t / nav_{t-1} - 1``
  - Annualization: 252 trading days
  - Risk-free rate: 0 by default (the deliverable spec does not require
    a risk-free series; we keep the input pluggable).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

import numpy as np
import pandas as pd


ANNUAL_TRADING_DAYS = 252
DEFAULT_RF = 0.0  # annualized risk-free rate


@dataclass
class MetricsResult:
    """Container for the 7 key metrics a backtest report must surface."""
    annual_return: float
    volatility: float
    sharpe: float
    max_drawdown: float
    var_95: float          # 1-day Value-at-Risk at 95% confidence (positive number = loss)
    cvar_95: float         # 1-day Conditional VaR (Expected Shortfall) at 95%
    turnover: float        # annualized one-way turnover (0..N)

    def as_dict(self) -> dict[str, float]:
        return {
            "annual_return": self.annual_return,
            "volatility": self.volatility,
            "sharpe": self.sharpe,
            "max_drawdown": self.max_drawdown,
            "var_95": self.var_95,
            "cvar_95": self.cvar_95,
            "turnover": self.turnover,
        }


def daily_returns(nav: pd.Series) -> pd.Series:
    """Simple daily returns from a NAV series. NaN-safe on the first row."""
    if nav is None or len(nav) < 2:
        return pd.Series(dtype=float)
    return nav.pct_change().fillna(0.0)


def annual_return(nav: pd.Series) -> float:
    if nav is None or len(nav) < 2:
        return float("nan")
    total = float(nav.iloc[-1] / nav.iloc[0])
    days = (nav.index[-1] - nav.index[0]).days
    if days <= 0 or total <= 0:
        return float("nan")
    years = days / 365.25
    return total ** (1.0 / years) - 1.0


def volatility(returns: pd.Series) -> float:
    if returns is None or len(returns) < 2:
        return float("nan")
    return float(returns.std(ddof=1) * np.sqrt(ANNUAL_TRADING_DAYS))


def sharpe(returns: pd.Series, rf: float = DEFAULT_RF) -> float:
    """Annualized Sharpe with constant risk-free rate."""
    if returns is None or len(returns) < 2:
        return float("nan")
    excess = returns - rf / ANNUAL_TRADING_DAYS
    sd = float(excess.std(ddof=1))
    if sd == 0 or np.isnan(sd):
        return float("nan")
    return float(excess.mean() / sd * np.sqrt(ANNUAL_TRADING_DAYS))


def max_drawdown(nav: pd.Series) -> float:
    """Maximum drawdown as a positive fraction (e.g. 0.15 = 15%)."""
    if nav is None or len(nav) < 2:
        return float("nan")
    running_max = nav.cummax()
    dd = (nav - running_max) / running_max
    return float(-dd.min())


def var_cvar(returns: pd.Series, alpha: float = 0.05) -> tuple[float, float]:
    """Historical 1-day VaR / CVaR at the (1-alpha) confidence level.

    Returns a positive number that represents the loss: i.e. VaR(95) = 0.02
    means "on the worst 5% of days we lost at least 2%".
    """
    if returns is None or len(returns) < 5:
        return float("nan"), float("nan")
    sorted_r = np.sort(returns.values)
    cutoff = int(np.floor(alpha * len(sorted_r)))
    if cutoff <= 0:
        cutoff = 1
    var = -float(sorted_r[cutoff - 1])
    cvar = -float(sorted_r[:cutoff].mean())
    return var, cvar


def annualized_turnover(turnover_series: pd.Series) -> float:
    """Annualize a per-rebalance turnover series.

    ``turnover_series`` is one value per rebalance event (the fraction of
    the portfolio that changed), indexed by the rebalance trade date.

    Annualized turnover = mean(per-rebalance turnover) ×
                         (#rebalances in a 1-year window, derived from
                          the median gap between events).
    """
    if turnover_series is None or turnover_series.empty:
        return 0.0
    n = len(turnover_series)
    if n == 0:
        return 0.0
    if n == 1:
        return float(turnover_series.iloc[0])
    # Average gap in calendar days between rebalances
    diffs = np.diff(turnover_series.index.values).astype("timedelta64[D]").astype(int)
    avg_gap_days = float(np.median(diffs))
    if avg_gap_days <= 0:
        return 0.0
    rebal_per_year = 365.25 / avg_gap_days
    return float(turnover_series.mean() * rebal_per_year)


def compute_metrics(
    nav: pd.Series,
    turnover_series: pd.Series | None = None,
    rf: float = DEFAULT_RF,
) -> MetricsResult:
    """One-shot computation of the 7 mandatory backtest metrics."""
    r = daily_returns(nav)
    var, cvar = var_cvar(r, alpha=0.05)
    if turnover_series is None:
        turnover = float("nan")
    else:
        turnover = annualized_turnover(turnover_series)
    return MetricsResult(
        annual_return=annual_return(nav),
        volatility=volatility(r),
        sharpe=sharpe(r, rf=rf),
        max_drawdown=max_drawdown(nav),
        var_95=var,
        cvar_95=cvar,
        turnover=turnover,
    )


def equity_drawdown_series(nav: pd.Series) -> pd.DataFrame:
    """Nav / running-max / drawdown as a 3-column frame (for plotting)."""
    if nav is None or nav.empty:
        return pd.DataFrame(columns=["nav", "running_max", "drawdown"])
    running_max = nav.cummax()
    dd = (nav - running_max) / running_max
    return pd.DataFrame({"nav": nav, "running_max": running_max, "drawdown": dd})


__all__ = [
    "ANNUAL_TRADING_DAYS",
    "MetricsResult",
    "daily_returns",
    "annual_return",
    "volatility",
    "sharpe",
    "max_drawdown",
    "var_cvar",
    "annualized_turnover",
    "compute_metrics",
    "equity_drawdown_series",
]
