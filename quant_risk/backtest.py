"""Backtest engine for the risk agent (v0.4 §9.6 + Week 3 deliverable).

Vectorized design:
  1. Pre-fetch a wide close matrix (index=trade_date, columns=ts_code)
     for the whole window + a lookback buffer. One big query, not per-day.
  2. On each rebalance date, score the universe by ``close[-1] / close[-L-1] - 1``
     and pick the top-N.
  3. Trade T+1 at the close, applying 0.15% one-way cost on traded notional.
  4. Daily PnL: ``sum_t (w_t * r_t)`` from the close matrix.

All data access goes through ``quant_risk.data_layer``; no direct
tushare/akshare imports anywhere in this module.
"""
from __future__ import annotations

import datetime
import logging
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Callable

import numpy as np
import pandas as pd

from quant_risk.data_layer import RiskDataLayer

log = logging.getLogger("quant_risk.backtest")


# ---- Trade ledger entry ----
@dataclass
class Trade:
    trade_date: date
    ts_code: str
    side: str            # "buy" or "sell"
    weight_before: float
    weight_after: float
    price: float
    cost_bps: float      # one-way cost in basis points (e.g. 15 bps = 0.15%)

    @property
    def turnover_abs(self) -> float:
        return abs(self.weight_after - self.weight_before)


# ---- Backtest configuration ----
@dataclass
class BacktestConfig:
    start: date
    end: date
    rebalance_freq: str = "monthly"
    lookback_days: int = 5
    top_n: int = 30
    cost_bps: float = 15.0
    rf_annual: float = 0.0
    index: str = "csi300_proxy"
    min_list_date: date | None = date(2010, 1, 1)
    exclude_st: bool = True
    forbid_buy_on_limit_up: bool = True
    forbid_sell_on_limit_down: bool = True
    forbid_buy_on_suspended: bool = True
    forbid_sell_on_suspended: bool = True
    t_plus_one: bool = True


# ---- Result container ----
@dataclass
class BacktestResult:
    nav: pd.Series
    benchmark_nav: pd.Series
    weights: pd.DataFrame
    target_weights: pd.DataFrame
    trades: list[Trade] = field(default_factory=list)
    turnover_per_rebalance: pd.Series = field(default_factory=pd.Series)
    rebalance_dates: list[date] = field(default_factory=list)
    signal_scores: pd.DataFrame = field(default_factory=pd.DataFrame)
    config: BacktestConfig | None = None
    diagnostics: dict = field(default_factory=dict)


# ---- Signal ----
def momentum_score(
    close: pd.DataFrame,
    as_of: date | str,
    *,
    lookback: int = 5,
) -> pd.Series:
    """close-to-close momentum: close[-1] / close[-lookback-1] - 1.

    ``close`` is a wide DataFrame (index=trade_date, columns=ts_code).
    ``as_of`` is the LAST observation date (signal is computed at the close
    of that day, and traded T+1).
    """
    if close is None or close.empty or len(close) < lookback + 2:
        return pd.Series(dtype=float)
    if isinstance(as_of, str):
        as_of = datetime.date.fromisoformat(as_of)
    # pick the last row at-or-before as_of
    if as_of not in close.index:
        # snap to the most recent row <= as_of
        as_of = close.index[close.index.get_indexer([as_of], method="ffill")[0]]
    pos = close.index.get_loc(as_of)
    if pos < lookback + 1:
        return pd.Series(dtype=float)
    last = close.iloc[pos]
    prev = close.iloc[pos - lookback - 1]
    score = last / prev - 1.0
    return score.dropna().astype(float)


# ---- Core backtest loop ----
def run_backtest(
    cfg: BacktestConfig,
    *,
    dl: RiskDataLayer | None = None,
) -> BacktestResult:
    """Run the backtest and return a ``BacktestResult``."""
    dl = dl or RiskDataLayer()

    # 1) Pre-fetch close matrix.
    #    We need a buffer BEFORE start so momentum has data; extend the
    #    window by 2 * lookback calendar days.
    buffer_days = cfg.lookback_days * 3 + 30
    pre_start = cfg.start - timedelta(days=buffer_days)
    members = dl.get_index_member(cfg.start, index=cfg.index)
    if members.empty:
        raise ValueError(f"no index members on {cfg.start}")
    universe = members["ts_code"].tolist()
    log.info("backtest: pre-fetching close matrix for %d members in [%s, %s]", len(universe), pre_start, cfg.end)
    close = dl.get_close_matrix(universe, pre_start, cfg.end, qfq=True)
    if close.empty:
        raise ValueError("close matrix is empty — no data for the chosen window")
    close = close.sort_index()
    trade_days = list(close.index)
    log.info("backtest: %d trade days in close matrix", len(trade_days))

    # 2) Build rebalance schedule.
    rebal_dates = _build_rebalance_schedule(cfg, dl, trade_days)
    log.info("backtest: %d rebalance dates", len(rebal_dates))
    if not rebal_dates:
        raise ValueError("no rebalance dates")

    # 3) Walk forward.
    benchmark_nav = _compute_benchmark_nav(close)
    nav = pd.Series(index=close.index, dtype=float, name="nav")
    nav.iloc[0] = 1.0

    current_weights: dict[str, float] = {}
    weights_history: list[tuple[date, dict[str, float]]] = []
    target_history: list[tuple[date, dict[str, float]]] = []
    trades: list[Trade] = []
    turnover_list: list[tuple[date, float]] = []
    signal_log: list[dict] = []
    pending_target: dict[str, float] | None = None

    rebal_set = set(_to_date(d) for d in rebal_dates)

    for i, td in enumerate(trade_days):
        # Mark-to-market from prior day's close to today's close
        if i == 0:
            day_ret = 0.0
        else:
            day_ret = _portfolio_day_return_vectorized(
                current_weights, close.iloc[i - 1], close.iloc[i]
            )
        # Apply cost drag from any trade done at today's close
        # (we apply it as a separate entry; the trade logic is below)
        new_nav = (nav.iloc[i - 1] if i > 0 else 1.0) * (1.0 + day_ret)
        nav.iloc[i] = new_nav

        td_d = _to_date(td)
        # 3a) On rebalance date, compute target; trade is done T+1.
        if td_d in rebal_set:
            scores = momentum_score(close, td_d, lookback=cfg.lookback_days)
            if not scores.empty:
                top = scores.sort_values(ascending=False).head(cfg.top_n)
                w = 1.0 / len(top) if len(top) > 0 else 0.0
                pending_target = {code: w for code in top.index}
            else:
                pending_target = dict(current_weights)
            target_history.append((td_d, dict(pending_target)))
            signal_log.append({
                "rebalance_date": td_d,
                "n_scores": int(len(scores)),
                "n_top": int(len(pending_target)),
            })

        # 3b) On the day AFTER a rebalance, apply target with cost.
        prev_d = _to_date(trade_days[i - 1]) if i > 0 else None
        if pending_target is not None and prev_d is not None and prev_d in rebal_set and i > 0:
            new_weights = _filter_tradable_matrix(close.iloc[i], pending_target, cfg)
            # Determine traded notional & emit Trade rows
            traded = 0.0
            all_codes = set(new_weights) | set(current_weights)
            for code in all_codes:
                w_before = current_weights.get(code, 0.0)
                w_after = new_weights.get(code, 0.0)
                if abs(w_after - w_before) < 1e-12:
                    continue
                side = "buy" if w_after > w_before else "sell"
                px = float(close.iloc[i].get(code, np.nan))
                if np.isnan(px):
                    new_weights[code] = w_before
                    continue
                trades.append(Trade(
                    trade_date=td_d,
                    ts_code=code,
                    side=side,
                    weight_before=w_before,
                    weight_after=w_after,
                    price=px,
                    cost_bps=cfg.cost_bps,
                ))
                traded += abs(w_after - w_before)
            # Cost drag applied to NAV
            cost_drag = traded * (cfg.cost_bps / 10000.0)
            nav.iloc[i] = float(nav.iloc[i]) * (1.0 - cost_drag)
            current_weights = new_weights
            weights_history.append((td_d, dict(current_weights)))
            turnover_list.append((td_d, traded))
            pending_target = None

    # 4) Build result series
    if turnover_list:
        turnover_s = pd.Series(
            [v for _, v in turnover_list],
            index=pd.to_datetime([d for d, _ in turnover_list]),
            name="turnover",
        )
    else:
        turnover_s = pd.Series(dtype=float, name="turnover")
    if weights_history:
        w_df = pd.DataFrame(
            [{**w, "date": d} for d, w in weights_history]
        ).set_index("date")
    else:
        w_df = pd.DataFrame()
    if target_history:
        tw_df = pd.DataFrame(
            [{**w, "date": d} for d, w in target_history]
        ).set_index("date")
    else:
        tw_df = pd.DataFrame()
    s_df = pd.DataFrame(signal_log) if signal_log else pd.DataFrame()

    return BacktestResult(
        nav=nav,
        benchmark_nav=benchmark_nav,
        weights=w_df,
        target_weights=tw_df,
        trades=trades,
        turnover_per_rebalance=turnover_s,
        rebalance_dates=rebal_dates,
        signal_scores=s_df,
        config=cfg,
        diagnostics={
            "n_rebalances": len(rebal_dates),
            "n_trades": len(trades),
            "n_trade_days": len(nav),
        },
    )


# ---- Helpers ----
def _portfolio_day_return_vectorized(
    weights: dict[str, float],
    prev_close: pd.Series,
    today_close: pd.Series,
) -> float:
    """Vectorized daily return for a long-only portfolio."""
    if not weights:
        return 0.0
    codes = list(weights)
    p = prev_close.reindex(codes)
    t = today_close.reindex(codes)
    w = pd.Series(weights)
    valid = p.notna() & t.notna() & (p > 0)
    if not valid.any():
        return 0.0
    w_v = w[valid]
    r_v = (t[valid] / p[valid] - 1.0)
    total_w = w_v.sum()
    if total_w <= 0:
        return 0.0
    return float((w_v * r_v).sum() / total_w)


def _filter_tradable_matrix(
    today_close: pd.Series,
    target: dict[str, float],
    cfg: BacktestConfig,
) -> dict[str, float]:
    """Trim a target weight dict by a quick NaN/0 filter.

    The full suspended/limit checks require per-stock queries; we leave
    those to a future optimization (see note in the report). The
    pre-filter is just ``close is NaN today → drop``.
    """
    out: dict[str, float] = {}
    for code, w in target.items():
        if w <= 0:
            continue
        px = today_close.get(code, np.nan)
        if np.isnan(px) or px <= 0:
            continue
        out[code] = w
    s = sum(out.values())
    if s > 0:
        out = {k: v / s for k, v in out.items()}
    return out


def _build_rebalance_schedule(
    cfg: BacktestConfig,
    dl: RiskDataLayer,
    trade_days: list,
) -> list[date]:
    """First trading day of each month in [start, end]."""
    if isinstance(cfg.rebalance_freq, list):
        s = {_to_date(d) for d in cfg.rebalance_freq if cfg.start <= _to_date(d) <= cfg.end}
        return sorted(s)
    if cfg.rebalance_freq == "monthly":
        from calendar import monthrange
        out: list[date] = []
        seen_months: set = set()
        for td in trade_days:
            td_d = _to_date(td)
            if td_d < cfg.start or td_d > cfg.end:
                continue
            key = (td_d.year, td_d.month)
            if key in seen_months:
                continue
            seen_months.add(key)
            out.append(td_d)
        return out
    if cfg.rebalance_freq == "weekly":
        out: list[date] = []
        seen: set = set()
        for td in trade_days:
            td_d = _to_date(td)
            if td_d < cfg.start or td_d > cfg.end:
                continue
            wk = td_d.isocalendar()[:2]
            if wk in seen:
                continue
            seen.add(wk)
            out.append(td_d)
        return out
    raise ValueError(f"unknown rebalance_freq: {cfg.rebalance_freq!r}")


def _compute_benchmark_nav(close: pd.DataFrame) -> pd.Series:
    """Equal-weight NAV across whatever stocks have a close on a given day."""
    if close.empty:
        return pd.Series(dtype=float)
    daily_mean = close.mean(axis=1, skipna=True)
    nav = (daily_mean / daily_mean.iloc[0]).rename("benchmark")
    return nav


def _to_date(v) -> date:
    if isinstance(v, pd.Timestamp):
        return v.date()
    if isinstance(v, datetime.datetime):
        return v.date()
    if isinstance(v, date):
        return v
    return datetime.datetime.strptime(str(v), "%Y-%m-%d").date()


__all__ = [
    "BacktestConfig",
    "BacktestResult",
    "Trade",
    "momentum_score",
    "run_backtest",
]
