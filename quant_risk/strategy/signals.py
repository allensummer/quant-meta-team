"""Signal factory for Week4 strategy iteration (ADM-619).

A *signal* is a function that maps a wide ``close`` matrix
(``index=trade_date``, ``columns=ts_code``) plus an ``as_of`` date to a
``pd.Series`` of cross-sectional scores (one value per ``ts_code``). The
strategy engine :func:`quant_risk.backtest.run_backtest` only ever calls
the signal on the **as_of** date with information that was available at
the close of that day — no future columns are referenced.

Conventions:

* Positive scores → "long" candidates; the engine takes the **top-N** by
 descending score.
* Scores are returned as :class:`pandas.Series` with the ``ts_code`` as
 index; ``NaN`` / ``inf`` rows are expected and downstream code drops
 them.
* All functions are pure: no I/O, no clock, no random state. They take
 wide matrices as input so the caller controls the data fetch.

This module deliberately does **not** import ``quant_risk.backtest`` — the
research layer should be testable without the engine in scope, and
importing ``backtest`` here would create a cycle when ``backtest``
grows a ``signal=`` parameter later.
"""
from __future__ import annotations

import datetime
from dataclasses import dataclass
from typing import Callable

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# Signal output container
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class SignalOutput:
  """Standardized signal result.
 
  Attributes
  ----------
  score : pd.Series
  Cross-sectional score, indexed by ``ts_code``. ``NaN`` is allowed.
  strength : pd.Series
  Absolute-value companion to ``score`` (used by some weighting
  schemes). Same index as ``score``.
  signal_kind : str
  One of ``"momentum"``, ``"reversal"``, ``"blended"``. The sweep
  driver uses this for labeling only; the engine does not branch
  on it.
  """
 
  score: pd.Series
  strength: pd.Series
  signal_kind: str


# ---------------------------------------------------------------------------
# Primitive signals
# ---------------------------------------------------------------------------
def momentum(
  close: pd.DataFrame,
  as_of: datetime.date | str,
  *,
  lookback: int = 20,
) -> SignalOutput:
  """Close-to-close momentum: ``close[-1] / close[-lookback-1] -1``.
 
  Parameters
  ----------
  close : pd.DataFrame
  Wide close matrix, ``index=trade_date``, ``columns=ts_code``.
  as_of : date | str
  Last observation date. The signal is computed at the close
  of this day; traded T+1.
  lookback : int, default20
  Lookback window in **trading days**. Must be >= 2 to allow
  ``pos - lookback -1`` indexing.
 
  Notes
  -----
  The function performs a single ``iloc`` lookup against the matrix
  and therefore never references data after ``as_of``. This is the
  look-ahead-bias guard (v0.4 §9.6 #8).
  """
  if close is None or close.empty:
   return _empty("momentum")
  if isinstance(as_of, str):
   as_of = datetime.date.fromisoformat(as_of)
  pos, ok = _snap_pos(close, as_of)
  if not ok or pos < lookback +1:
   return _empty("momentum")
  last = close.iloc[pos]
  prev = close.iloc[pos - lookback -1]
  raw = (last / prev -1.0).replace([np.inf, -np.inf], np.nan).dropna().astype(float)
  return SignalOutput(
   score=raw,
   strength=raw.abs(),
   signal_kind="momentum",
  )


def reversal(
  close: pd.DataFrame,
  as_of: datetime.date | str,
  *,
  lookback: int = 5,
) -> SignalOutput:
  """Short-term reversal: *negative* of ``lookback``-day return.
 
  Stocks with the **worst** ``lookback``-day return become the most
  attractive reversal candidates (score is flipped, so the top-N
  selection still picks the top-N by descending score).
  """
  if close is None or close.empty:
   return _empty("reversal")
  if isinstance(as_of, str):
   as_of = datetime.date.fromisoformat(as_of)
  pos, ok = _snap_pos(close, as_of)
  if not ok or pos < lookback +1:
   return _empty("reversal")
  last = close.iloc[pos]
  prev = close.iloc[pos - lookback -1]
  raw = (last / prev -1.0).replace([np.inf, -np.inf], np.nan).dropna().astype(float)
  flipped = -raw
  return SignalOutput(
   score=flipped,
   strength=flipped.abs(),
   signal_kind="reversal",
  )


def blended(
  close: pd.DataFrame,
  as_of: datetime.date | str,
  *,
  fast: int = 5,
  slow: int = 20,
  weight_slow: float = 0.5,
) -> SignalOutput:
  """Linear blend of fast and slow momentum.
 
  Score = (1 - weight_slow) * momo(fast) + weight_slow * momo(slow).
  """
  if not 0.0 <= weight_slow <= 1.0:
   raise ValueError(f"weight_slow must be in [0,1]; got {weight_slow!r}")
  fast_sig = momentum(close, as_of, lookback=fast)
  slow_sig = momentum(close, as_of, lookback=slow)
  if fast_sig.score.empty and slow_sig.score.empty:
   return _empty("blended")
  df = pd.concat(
   [fast_sig.score.rename("fast"), slow_sig.score.rename("slow")],
   axis= 1,
  ).dropna()
  if df.empty:
   return _empty("blended")
  score = (1.0 - weight_slow) * df["fast"] + weight_slow * df["slow"]
  return SignalOutput(
   score=score.rename(None),
   strength=score.abs(),
   signal_kind="blended",
  )


# ---------------------------------------------------------------------------
# Signal spec / factory
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class SignalSpec:
  """Declarative spec for one signal in the sweep.
 
  Parameters
  ----------
  name : {"momentum", "reversal", "blended"}
  lookback : int, optional
  Lookback in trading days. Ignored by ``blended`` which uses
  ``fast`` / ``slow`` instead.
  fast : int, default5
  Fast component for ``blended``.
  slow : int, default20
  Slow component for ``blended``.
  weight_slow : float, default0.5
  Weight of the slow component in ``blended``.
  """
 
  name: str
  lookback: int = 20
  fast: int = 5
  slow: int = 20
  weight_slow: float = 0.5
 
  def build(self) -> Callable[..., SignalOutput]:
   """Materialize the spec into a callable."""
   if self.name == "momentum":
    lookback = self.lookback
   
    def _call(close: pd.DataFrame, as_of) -> SignalOutput:
     return momentum(close, as_of, lookback=lookback)
   
    _call.__name__ = f"momentum_L{lookback}"
    return _call
   if self.name == "reversal":
    lookback = self.lookback
   
    def _call(close: pd.DataFrame, as_of) -> SignalOutput:
     return reversal(close, as_of, lookback=lookback)
   
    _call.__name__ = f"reversal_L{lookback}"
    return _call
   if self.name == "blended":
    fast, slow, wgt = self.fast, self.slow, self.weight_slow
   
    def _call(close: pd.DataFrame, as_of) -> SignalOutput:
     return blended(close, as_of, fast=fast, slow=slow, weight_slow=wgt)
   
    _call.__name__ = f"blended_f{fast}_s{slow}_w{wgt}"
    return _call
   raise ValueError(f"unknown signal name: {self.name!r}")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _snap_pos(close: pd.DataFrame, as_of: datetime.date) -> tuple[int, bool]:
  """Return (position, ok) of the last row <= as_of in close.index."""
  try:
   loc = close.index.get_indexer([pd.Timestamp(as_of)], method="ffill")[0]
  except (KeyError, ValueError):
   return 0, False
  if loc == -1:
   return 0, False
  return int(loc), True


def _empty(kind: str) -> SignalOutput:
  return SignalOutput(
   score=pd.Series(dtype=float),
   strength=pd.Series(dtype=float),
   signal_kind=kind,
  )


__all__ = [
  "SignalOutput",
  "SignalSpec",
  "momentum",
  "reversal",
  "blended",
]
