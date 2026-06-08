"""Integration test for the strategy sweep driver (ADM-619)."""
from __future__ import annotations

import sys
sys.path.insert(0, "/Users/allenwang/Code/quant-meta-team")

from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from quant_risk.examples import strategy_sweep
from quant_risk.strategy.engine import StrategyConfig
from quant_risk.strategy.filters import FilterSpec
from quant_risk.strategy.signals import SignalSpec


class TestStrategySweep:
 def test_lookup_tables(self):
  assert len(strategy_sweep.LOOKBACKS) == 3
  assert len(strategy_sweep.FILTERS) == 4
  assert len(strategy_sweep.LOOKBACKS) * len(strategy_sweep.FILTERS) == 12
 
 def test_build_configs_produces_12(self):
  args = strategy_sweep.parse_args(["--start", "2024-01-01", "--end", "2024-06-30"])
  cfgs = strategy_sweep.build_configs(args)
  assert len(cfgs) == 12
  names = [c.name for c in cfgs]
  assert len(set(names)) == 12
  for c in cfgs:
   parts = c.name.split("_")
   assert parts[0].startswith("L")
   assert parts[1] in ("weekly", "monthly", "quarterly")
   suffix = "_".join(parts[2:])
   assert suffix in ("none", "amount_percentile", "liquidity_floor", "amount_percentile_and_liquidity_floor")
 
 def test_run_one_returns_required_keys(self):
  cfg = StrategyConfig(
   start=date(2024,1,1),
   end=date(2024,2,15),
   signal=SignalSpec(name="momentum", lookback= 5),
   rebalance_freq="monthly",
   top_n= 10,
   cost_bps= 15.0,
   filter=FilterSpec(name="none"),
   name="dry_run",
  )
  class FakeDL:
   def get_index_member(self, *a, **kw):
    return pd.DataFrame({"ts_code": ["A.SH", "B.SZ"]})
   def get_close_matrix(self, codes, start, end, *, qfq=True):
    idx = pd.date_range(start, end)
    return pd.DataFrame(1.0, index=idx, columns=codes)
   def get_amount_matrix(self, codes, start, end, *, qfq=True):
    return pd.DataFrame(0.0, index=pd.date_range(start, end), columns=codes)
  row, res = strategy_sweep.run_one(cfg, FakeDL())
  required_keys = {"strategy", "lookback", "rebalance_freq", "filter",
   "top_p", "floor_yuan", "n_rebalances", "n_trades", "elapsed_s",
   "annual_return", "volatility", "sharpe", "max_drawdown",
   "var_95", "cvar_95", "turnover"}
  assert set(row.keys()) >= required_keys
 
 def test_write_csv_and_report_create_files(self, tmp_path):
  rows = [{
   "strategy": "S1", "lookback":5, "rebalance_freq":"weekly", "filter":"none",
   "top_p":0.5, "floor_yuan":50_000_000.0, "n_rebalances":2, "n_trades":2,
   "elapsed_s":0.5, "annual_return":0.1, "volatility":0.2, "sharpe":0.5,
   "max_drawdown":0.10, "var_95":0.02, "cvar_95":0.03, "turnover":1.0,
  }]
  csv_path = tmp_path / "risk_metrics.csv"
  strategy_sweep.write_csv(rows, csv_path)
  assert csv_path.exists()
  df = pd.read_csv(csv_path)
  assert len(df) == 1
  assert df.iloc[0]["strategy"] == "S1"
  rec = rows[0]
  md_path = tmp_path / "report.md"
  strategy_sweep.write_report(rows, md_path, date(2024,1,1), date(2024,12,31), rec)
  text = md_path.read_text()
  assert "Recommended" in text
  assert "S1" in text
 
 def test_write_report_honest_conclusion_when_no_recommended(self, tmp_path):
  rows = [{
   "strategy": "S1", "lookback":5, "rebalance_freq":"weekly", "filter":"none",
   "top_p":0.5, "floor_yuan":50_000_000.0, "n_rebalances":2, "n_trades":2,
   "elapsed_s":0.5, "annual_return":-0.5, "volatility":0.3, "sharpe":-1.0,
   "max_drawdown":0.6, "var_95":0.05, "cvar_95":0.07, "turnover":1.0,
  }]
  md_path = tmp_path / "report.md"
  strategy_sweep.write_report(rows, md_path, date(2024,1,1), date(2024,12,31), None)
  text = md_path.read_text()
  assert "Honest Conclusion" in text
  assert "failed the risk gates" in text
