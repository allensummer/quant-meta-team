"""Tests for the Week4 strategy library (ADM-619)."""
from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest

from quant_risk.strategy.signals import (
 SignalSpec,
 blended,
 momentum,
 reversal,
)

# ----------------- helpers -----------------
def _make_close(prices_dict):
 idx = pd.date_range("2024-01-01", periods=max(len(v) for v in prices_dict.values()))
 df = pd.DataFrame({code: pd.Series(v, index=idx[:len(v)]) for code, v in prices_dict.items()})
 return df


# ============================================================
# Signal tests
# ============================================================
class TestMomentum:
 def test_basic_5d_momentum(self):
  df = _make_close({"000001.SZ": [10,11,12,13,14,15,16,17]})
  sig = momentum(df, df.index[-1].date(), lookback= 5)
  #17/11 -1 ~0.5455 (close[-1]/close[-6] = pos7/pos1)
  assert sig.score["000001.SZ"] == pytest.approx(17/11 -1, rel= 1e-3)
  assert sig.signal_kind == "momentum"
 
 def test_short_history_returns_empty(self):
  df = _make_close({"000001.SZ": [10,11,12,13,14,15]})
  sig = momentum(df, df.index[-1].date(), lookback= 5)
  # Need pos >= lookback+1 = 6; pos= 5 <6 -> empty
  assert sig.score.empty
 
 def test_no_lookahead(self):
  """Signal at pos6 vs pos10 must use only past data."""
  df = _make_close({"000001.SZ": [10,11,12,13,14,15,16,17,18,19,20,21,22]})
  sig_at_6 = momentum(df, df.index[6].date(), lookback= 5)
  sig_at_10 = momentum(df, df.index[10].date(), lookback= 5)
  # pos= 6:16/10 -1 = 0.6
  assert sig_at_6.score["000001.SZ"] == pytest.approx(0.6, rel= 1e-3)
  # pos= 10:20/14 -1 ~0.4286
  assert sig_at_10.score["000001.SZ"] == pytest.approx(20/14 -1, rel= 1e-3)
  # Signal at later pos must give DIFFERENT score (uses more recent data)
  assert sig_at_6.score["000001.SZ"] != sig_at_10.score["000001.SZ"]
 
 def test_signal_spec_build_returns_callable(self):
  spec = SignalSpec(name="momentum", lookback= 10)
  fn = spec.build()
  df = _make_close({"000001.SZ": list(range(10,32))})
  sig = fn(df, df.index[-1].date())
  assert sig.signal_kind == "momentum"
  # Last pos= 21, lookback= 10 -> close[21]/close[10] -1 = 31/20 -1 = 0.55
  assert sig.score["000001.SZ"] == pytest.approx(31/20 -1, rel= 1e-3)


class TestReversal:
 def test_reversal_flips_sign(self):
  """Reversal score = -momentum, so losers become winners."""
  df = _make_close({"A": list(range(10,17)), "B": [16,15,14,13,12,11,10]})
  mom = momentum(df, df.index[-1].date(), lookback= 5)
  rev = reversal(df, df.index[-1].date(), lookback= 5)
  assert rev.score["B"] > rev.score["A"]
  assert rev.score["A"] == pytest.approx(-mom.score["A"])


class TestBlended:
 def test_blend_weights_components(self):
  spec = SignalSpec(name="blended", fast= 5, slow= 20, weight_slow= 0.7)
  fn = spec.build()
  df = _make_close({"000001.SZ": list(range(10,42))})
  sig = fn(df, df.index[-1].date())
  fast_s = momentum(df, df.index[-1].date(), lookback= 5).score["000001.SZ"]
  slow_s = momentum(df, df.index[-1].date(), lookback= 20).score["000001.SZ"]
  expected = 0.3 * fast_s +0.7 * slow_s
  assert sig.score["000001.SZ"] == pytest.approx(expected, rel= 1e-3)
 
 def test_invalid_weight_raises(self):
  df = _make_close({"000001.SZ": list(range(10,40))})
  with pytest.raises(ValueError):
   blended(df, df.index[-1].date(), weight_slow= 2.0)


# ============================================================
# Portfolio rule tests
# ============================================================
from quant_risk.strategy.portfolio import (
 MONTHLY,
 QUARTERLY,
 WEEKLY,
 build_rebalance_schedule,
 events_per_year,
)

class TestRebalanceSchedule:
 def test_monthly_first_of_month(self):
  days = [date(2024,1,2), date(2024,1,15), date(2024,2,1), date(2024,2,20), date(2024,3,1)]
  out = build_rebalance_schedule(days, start=date(2024,1,1), end=date(2024,12,31), freq=MONTHLY)
  assert out == [date(2024,1,2), date(2024,2,1), date(2024,3,1)]
 
 def test_quarterly_first_of_quarter(self):
  days = [date(2024,1,2), date(2024,2,1), date(2024,3,1), date(2024,4,1), date(2024,5,1), date(2024,6,3), date(2024,7,1)]
  out = build_rebalance_schedule(days, start=date(2024,1,1), end=date(2024,12,31), freq=QUARTERLY)
  assert out == [date(2024,1,2), date(2024,4,1), date(2024,7,1)]
 
 def test_weekly_first_of_iso_week(self):
  days = [date(2024,1,1), date(2024,1,2), date(2024,1,8), date(2024,1,9), date(2024,1,15), date(2024,1,16)]
  out = build_rebalance_schedule(days, start=date(2024,1,1), end=date(2024,1,31), freq=WEEKLY)
  assert len(out) >= 3
  assert out[0] == days[0]
  assert len(set(out)) == len(out)
 
 def test_window_filter(self):
  days = [date(2023,12,1), date(2024,1,2), date(2024,2,1), date(2025,1,2)]
  out = build_rebalance_schedule(days, start=date(2024,1,1), end=date(2024,12,31), freq=MONTHLY)
  assert date(2023,12,1) not in out
  assert date(2025,1,2) not in out
  assert date(2024,1,2) in out
  assert date(2024,2,1) in out
 
 def test_invalid_freq_raises(self):
  days = [date(2024,1,2)]
  with pytest.raises(ValueError):
   build_rebalance_schedule(days, start=date(2024,1,1), end=date(2024,12,31), freq="daily")
 
 def test_empty_returns_empty(self):
  assert build_rebalance_schedule([], start=date(2024,1,1), end=date(2024,12,31), freq=MONTHLY) == []
  assert build_rebalance_schedule([date(2023,12,1)], start=date(2024,1,1), end=date(2024,12,31), freq=MONTHLY) == []
 
 def test_events_per_year(self):
  assert events_per_year(MONTHLY) == 12.0
  assert events_per_year(QUARTERLY) == 4.0
  assert events_per_year(WEEKLY) == 48.0


# ============================================================
# Filter tests
# ============================================================
from quant_risk.strategy.filters import (
 FilterSpec,
 amount_percentile,
 liquidity_floor,
 amount_percentile_and_liquidity_floor,
 none,
)

class TestFilters:
 def _mk_amount(self, values):
  idx = pd.date_range("2024-01-01", periods=len(values["A"]))
  return pd.DataFrame(values, index=idx)
 
 def test_none_passes_all_through(self):
  codes = ["A", "B", "C"]
  out = none(codes, pd.DataFrame(), pd.DataFrame(), date(2024,1,1))
  assert out == set(codes)
 
 def test_amount_percentile_top_half(self):
  amount = self._mk_amount({
   "A": [10_000_000]*20,
   "B": [50_000_000]*20,
   "C": [100_000_000]*20,
   "D": [200_000_000]*20,
  })
  f = amount_percentile(top_p= 0.5, lookback= 20)
  out = f(["A","B","C","D"], pd.DataFrame(), amount, date(2024,1,20))
  assert "D" in out
  assert "C" in out
  assert "A" not in out
 
 def test_liquidity_floor(self):
  amount = self._mk_amount({
   "A": [10_000_000]*20,
   "B": [60_000_000]*20,
   "C": [200_000_000]*20,
  })
  f = liquidity_floor(floor_yuan= 50_000_000, lookback= 20)
  out = f(["A","B","C"], pd.DataFrame(), amount, date(2024,1,20))
  assert out == {"B", "C"}
 
 def test_combined_filter_is_conjunction(self):
  amount = self._mk_amount({
   "A": [10_000_000]*20,
   "B": [60_000_000]*20,
   "C": [80_000_000]*20,
   "D": [200_000_000]*20,
  })
  f = amount_percentile_and_liquidity_floor(top_p= 0.5, floor_yuan= 50_000_000, lookback= 20)
  out = f(["A","B","C","D"], pd.DataFrame(), amount, date(2024,1,20))
  assert out == {"C", "D"}
 
 def test_invalid_top_p_raises(self):
  with pytest.raises(ValueError):
   amount_percentile(top_p= 0)
  with pytest.raises(ValueError):
   amount_percentile(top_p= 1.5)
 
 def test_filter_spec_factory(self):
  for spec in [FilterSpec("none"), FilterSpec("amount_percentile"), FilterSpec("liquidity_floor"), FilterSpec("amount_percentile_and_liquidity_floor")]:
   f = spec.build()
   assert callable(f)
 
 def test_unknown_filter_name_raises(self):
  with pytest.raises(ValueError):
   FilterSpec("nonexistent").build()


# ============================================================
# CI gate: zero tushare/akshare imports
# ============================================================
def test_strategy_module_has_no_tushare_or_akshare_imports():
 import re
 from pathlib import Path
 p = Path("/Users/allenwang/Code/quant-meta-team/quant_risk/strategy")
 pat = re.compile(r"^\s*(import|from)\s+(tushare|akshare)(?:\s|\.|$)", re.M)
 hits = []
 for f in p.glob("*.py"):
  text = f.read_text()
  if pat.search(text):
   hits.append(str(f))
 assert not hits, f"tushare/akshare imports found in: {hits}"
