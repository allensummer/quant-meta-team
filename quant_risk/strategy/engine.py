"""Strategy-aware backtest engine for Week4 (ADM-619)."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, timedelta

import numpy as np
import pandas as pd

from quant_risk.backtest import BacktestConfig, BacktestResult, Trade, _to_date
from quant_risk.data_layer import RiskDataLayer
from quant_risk.strategy.portfolio import build_rebalance_schedule
from quant_risk.strategy.signals import SignalSpec

log = logging.getLogger("quant_risk.strategy.engine")


@dataclass
class StrategyConfig:
  """Configuration for one strategy in the sweep."""
  start: date
  end: date
  signal: SignalSpec
  rebalance_freq: str
  top_n: int = 30
  cost_bps: float = 15.0
  index: str = "csi300_proxy"
  min_list_date: date | None = date(2010,1,1)
  exclude_st: bool = True
  filter: object | None = None
  name: str = "strategy"
 
  def buffer_days(self, signal_max_lookback: int) -> int:
   """Calendar-day buffer prepended to ``start`` so signals have data."""
   return signal_max_lookback *3 +30


def run_strategy(
  cfg: StrategyConfig,
  *,
  dl: RiskDataLayer | None = None,
) -> BacktestResult:
  """Run a strategy-aware backtest."""
  dl = dl or RiskDataLayer()
 
  signal_fn = cfg.signal.build()
  max_lookback = max(cfg.signal.lookback, cfg.signal.slow, cfg.signal.fast)
  filter_fn = cfg.filter.build() if cfg.filter is not None else None
  # Only load amount when the filter actually needs it (skip for "none").
  needs_amount = (cfg.filter is not None and getattr(cfg.filter, "name", "") != "none")
 
  members = dl.get_index_member(cfg.start, index=cfg.index)
  if members.empty:
   raise ValueError(f"no index members on {cfg.start}")
  universe = members["ts_code"].tolist()
 
  pre_start = cfg.start - timedelta(days=cfg.buffer_days(max_lookback))
  close = dl.get_close_matrix(universe, pre_start, cfg.end, qfq=True)
  if close.empty:
   raise ValueError("close matrix is empty")
  close = close.sort_index()
 
  amount: pd.DataFrame | None = None
  if needs_amount:
   amount = dl.get_amount_matrix(universe, pre_start, cfg.end, qfq=True)
 
  trade_days = list(close.index)
 
  rebal_dates = build_rebalance_schedule(
   trade_days, start=cfg.start, end=cfg.end, freq=cfg.rebalance_freq,
  )
  if not rebal_dates:
   raise ValueError(f"no rebalance dates for freq={cfg.rebalance_freq!r}")
 
  benchmark_nav = _compute_benchmark_nav(close)
  nav = pd.Series(index=close.index, dtype=float, name="nav")
  nav.iloc[0] = 1.0
 
  current_weights: dict[str, float] = {}
  target_history: list[tuple[date, dict[str, float]]] = []
  weights_history: list[tuple[date, dict[str, float]]] = []
  trades: list[Trade] = []
  turnover_list: list[tuple[date, float]] = []
  signal_log: list[dict] = []
  pending_target: dict[str, float] | None = None
  rebal_set = set(_to_date(d) for d in rebal_dates)
 
  for i, td in enumerate(trade_days):
   if i == 0:
    day_ret = 0.0
   else:
    day_ret = _portfolio_day_return(current_weights, close.iloc[i -1], close.iloc[i])
   nav.iloc[i] = (nav.iloc[i -1] if i >0 else 1.0) * (1.0 + day_ret)
   td_d = _to_date(td)
  
   if td_d in rebal_set:
    sig = signal_fn(close, td_d)
    if filter_fn is not None and amount is not None:
     retained = filter_fn(sig.score.index, close, amount, td_d)
     eligible = [c for c in sig.score.index if c in retained]
    else:
     eligible = list(sig.score.index)
    if eligible:
     scored = sig.score.loc[[c for c in eligible if c in sig.score.index]]
     top = scored.sort_values(ascending=False).head(cfg.top_n)
     wgt = 1.0 / len(top) if len(top) >0 else 0.0
     pending_target = {code: wgt for code in top.index}
    else:
     pending_target = dict(current_weights)
    target_history.append((td_d, dict(pending_target)))
    signal_log.append({
     "rebalance_date": td_d,
     "n_scores": int(len(sig.score)),
     "n_eligible_after_filter": int(len(eligible)),
     "n_top": int(len(pending_target)),
     "signal_kind": sig.signal_kind,
    })
  
   prev_d = _to_date(trade_days[i -1]) if i >0 else None
   if pending_target is not None and prev_d is not None and prev_d in rebal_set and i >0:
    new_weights = _filter_tradable(close.iloc[i], pending_target)
    traded = 0.0
    all_codes = set(new_weights) | set(current_weights)
    for code in all_codes:
     w_before = current_weights.get(code,0.0)
     w_after = new_weights.get(code,0.0)
     if abs(w_after - w_before) <1e-12:
      continue
     px = float(close.iloc[i].get(code, np.nan))
     if np.isnan(px):
      new_weights[code] = w_before
      continue
     side = "buy" if w_after > w_before else "sell"
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
     cost_drag = traded * (cfg.cost_bps /10000.0)
     nav.iloc[i] = float(nav.iloc[i]) * (1.0 - cost_drag)
     current_weights = new_weights
     weights_history.append((td_d, dict(current_weights)))
     turnover_list.append((td_d, traded))
     pending_target = None
 
  if turnover_list:
   turnover_s = pd.Series(
    [v for _, v in turnover_list],
    index=pd.to_datetime([d for d, _ in turnover_list]),
    name="turnover",
   )
  else:
   turnover_s = pd.Series(dtype=float, name="turnover")
  if weights_history:
   w_df = pd.DataFrame([{**w, "date": d} for d, w in weights_history]).set_index("date")
  else:
   w_df = pd.DataFrame()
  if target_history:
   tw_df = pd.DataFrame([{**w, "date": d} for d, w in target_history]).set_index("date")
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
   config=BacktestConfig(
    start=cfg.start,
    end=cfg.end,
    rebalance_freq=cfg.rebalance_freq,
    lookback_days=max_lookback,
    top_n=cfg.top_n,
    cost_bps=cfg.cost_bps,
    index=cfg.index,
   ),
   diagnostics={
    "n_rebalances": len(rebal_dates),
    "n_trades": len(trades),
    "n_trade_days": len(nav),
    "strategy_name": cfg.name,
    "signal": cfg.signal.name,
    "filter": getattr(cfg.filter, "name", "none"),
   },
  )


def _portfolio_day_return(
  weights: dict[str, float],
  prev_close: pd.Series,
  today_close: pd.Series,
) -> float:
  if not weights:
   return 0.0
  codes = list(weights)
  p = prev_close.reindex(codes)
  t = today_close.reindex(codes)
  wgt = pd.Series(weights)
  valid = p.notna() & t.notna() & (p >0)
  if not valid.any():
   return 0.0
  w_v = wgt[valid]
  r_v = (t[valid] / p[valid] -1.0)
  total_w = w_v.sum()
  if total_w <= 0:
   return 0.0
  return float((w_v * r_v).sum() / total_w)


def _filter_tradable(
  today_close: pd.Series,
  target: dict[str, float],
) -> dict[str, float]:
  """Trim weights to NaN-free, positive prices today."""
  out: dict[str, float] = {}
  for code, wgt in target.items():
   if wgt <= 0:
    continue
   px = today_close.get(code, np.nan)
   if np.isnan(px) or px <= 0:
    continue
   out[code] = wgt
  s = sum(out.values())
  if s >0:
   out = {k: v / s for k, v in out.items()}
  return out


def _compute_benchmark_nav(close: pd.DataFrame) -> pd.Series:
  if close.empty:
   return pd.Series(dtype=float)
  daily_mean = close.mean(axis= 1, skipna=True)
  return (daily_mean / daily_mean.iloc[0]).rename("benchmark")


__all__ = ["StrategyConfig", "run_strategy"]
