"""Filter collection for Week4 strategy iteration (ADM-619)."""
from __future__ import annotations

import datetime
from dataclasses import dataclass
from typing import Callable, Iterable

import numpy as np
import pandas as pd


# A filter is a callable taking (ts_codes, close, amount, as_of) and
# returning a set of ts_codes that should be retained.
FilterCallable = Callable[[Iterable[str], pd.DataFrame, pd.DataFrame, datetime.date], set[str]]


def none(
  ts_codes: Iterable[str],
  close: pd.DataFrame,
  amount: pd.DataFrame,
  as_of: datetime.date,
) -> set[str]:
  """Identity filter — retain every input code."""
  return set(ts_codes)


def amount_percentile(
  *,
  top_p: float = 0.5,
  lookback: int = 20,
) -> FilterCallable:
  """Filter that keeps the top ``top_p`` fraction by trailing
  ``lookback``-day average ``amount``.
  """
  if not 0.0 < top_p <= 1.0:
   raise ValueError(f"top_p must be in (0,1]; got {top_p!r}")
  if lookback <1:
   raise ValueError(f"lookback must be >= 1; got {lookback!r}")
 
  def _f(ts_codes, close, amount, as_of):
   codes = list(ts_codes)
   if not codes or amount is None or amount.empty:
    return set(codes)
   sub = amount.reindex(columns=codes)
   pos, ok = _snap_pos(amount, as_of)
   if not ok:
    return set(codes)
   start = max(0, pos - lookback +1)
   window = sub.iloc[start: pos +1]
   if window.empty:
    return set(codes)
   avg = window.mean(axis= 0).dropna()
   if avg.empty:
    return set(codes)
   cutoff = avg.quantile(1.0 - top_p)
   return set(avg[avg >= cutoff].index.astype(str))
 
  _f.__name__ = f"amount_percentile_p{top_p}_L{lookback}"
  return _f


def liquidity_floor(
  *,
  floor_yuan: float = 50_000_000.0,
  lookback: int = 20,
) -> FilterCallable:
  """Filter that retains only stocks whose trailing ``lookback``-day
  average ``amount`` is >= ``floor_yuan`` (in yuan).
  """
  if floor_yuan <= 0:
   raise ValueError(f"floor_yuan must be >0; got {floor_yuan!r}")
  if lookback <1:
   raise ValueError(f"lookback must be >= 1; got {lookback!r}")
 
  def _f(ts_codes, close, amount, as_of):
   codes = list(ts_codes)
   if not codes or amount is None or amount.empty:
    return set(codes)
   sub = amount.reindex(columns=codes)
   pos, ok = _snap_pos(amount, as_of)
   if not ok:
    return set(codes)
   start = max(0, pos - lookback +1)
   window = sub.iloc[start: pos +1]
   if window.empty:
    return set(codes)
   avg = window.mean(axis= 0).dropna()
   return set(avg[avg >= floor_yuan].index.astype(str))
 
  _f.__name__ = f"liquidity_floor_{int(floor_yuan)}_L{lookback}"
  return _f


def amount_percentile_and_liquidity_floor(
  *,
  top_p: float = 0.5,
  floor_yuan: float = 50_000_000.0,
  lookback: int = 20,
) -> FilterCallable:
  """Conjunction of percentile and liquidity-floor filters."""
  a = amount_percentile(top_p=top_p, lookback=lookback)
  b = liquidity_floor(floor_yuan=floor_yuan, lookback=lookback)
 
  def _f(ts_codes, close, amount, as_of):
   codes = list(ts_codes)
   return a(codes, close, amount, as_of) & b(codes, close, amount, as_of)
 
  _f.__name__ = f"amount_percentile_p{top_p}_and_liquidity_floor_{int(floor_yuan)}_L{lookback}"
  return _f


@dataclass(frozen=True)
class FilterSpec:
  """Declarative spec for one filter setting."""
  name: str
  top_p: float = 0.5
  floor_yuan: float = 50_000_000.0
  lookback: int = 20
 
  def build(self) -> FilterCallable:
   if self.name == "none":
    return none
   if self.name == "amount_percentile":
    return amount_percentile(top_p=self.top_p, lookback=self.lookback)
   if self.name == "liquidity_floor":
    return liquidity_floor(floor_yuan=self.floor_yuan, lookback=self.lookback)
   if self.name == "amount_percentile_and_liquidity_floor":
    return amount_percentile_and_liquidity_floor(
     top_p=self.top_p,
     floor_yuan=self.floor_yuan,
     lookback=self.lookback,
    )
   raise ValueError(f"unknown filter name: {self.name!r}")


def _snap_pos(matrix: pd.DataFrame, as_of: datetime.date) -> tuple[int, bool]:
  try:
   loc = matrix.index.get_indexer([pd.Timestamp(as_of)], method="ffill")[0]
  except (KeyError, ValueError):
   return 0, False
  if loc == -1:
   return 0, False
  return int(loc), True


__all__ = [
  "FilterCallable",
  "FilterSpec",
  "none",
  "amount_percentile",
  "liquidity_floor",
  "amount_percentile_and_liquidity_floor",
]
