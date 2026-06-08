"""DuckDB-backed data access for quant-risk-agent (v0.4 §6 + §9.6).

The risk agent never talks to tushare/akshare directly. It only consumes the
Parquet + DuckDB layer surfaced by ``quant_data``. This module wraps
``DuckDBStore.query()`` with the backtest-friendly queries the rest of the
agent uses:

 - ``get_calendar(start, end)`` trading days
 - ``get_universe(date)`` active, listed long enough, not ST
 - ``get_index_member(date, index)`` A-share CSI300 approx. (see below)
 - ``get_price_series(code, start, end, qfq=True)``
 - ``is_suspended(code, date)``
 - ``is_limit_up(code, date)`` / ``is_limit_down(code, date)``
 - ``get_amount_matrix(codes, start, end, qfq=True)`` (Week4: liquidity filters)

Universe definition (default, see `docs/data-localization.md` v0.4 §6.2):
 - ``list_status='L'`` (active, not delisted)
 - ``list_date <= as_of_date`` (already listed)
 - ``delist_date IS NULL OR delist_date > as_of_date`` (not yet delisted)
 - ``list_date >= 2010-01-01`` (filter micro-caps / IPO-too-recent)
 - ``name NOT LIKE '%ST%'`` (exclude special-treatment / *ST)

Index membership
---------------
Strict CSI300 / CSI500 constituent tracking needs a separate index-membership
table (tushare ``index_weight``). That data isn't synced in Week1. The
default here is **the active large-cap universe as a proxy** — see
``get_index_member``. The deliverable explicitly accepts this as a known
limitation; a separate issue will tighten it once Wind/JoinQuant sync.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterable

import pandas as pd

from quant_data.paths import data_dir as _default_data_dir
from quant_data.registry import SCHEMAS # noqa: F401 (re-exported for tests)
from quant_data.store.duckdb_store import DuckDBStore

log = logging.getLogger("quant_risk.data_layer")

__all__ = [
 "RiskDataLayer",
 "LimitBand",
 "DEFAULT_INDEX_PROXY",
]


# ---- Default index proxy ----
DEFAULT_INDEX_PROXY = "csi300_proxy"


# ---- Limit bands by exchange / board (v0.4 §5) ----
_LIMIT_RULES: list[tuple[re.Pattern, float, str]] = [
 (re.compile(r"^30\d{4}\."),0.20, "chinext"),
 (re.compile(r"^688\d{3}\."),0.20, "star"),
 (re.compile(r"^(8|4)\d{5}\."),0.30, "bse"),
 (re.compile(r"^(60|00)\d{4}\."),0.10, "main"),
]


@dataclass(frozen=True)
class LimitBand:
 """A-share daily price limit configuration for one board."""
 pct: float
 board: str
 
 @classmethod
 def for_code(cls, ts_code: str) -> "LimitBand":
  for pat, pct, board in _LIMIT_RULES:
   if pat.match(ts_code):
    return cls(pct=pct, board=board)
  return cls(pct= 0.10, board="main")


# ---- Main facade ----
class RiskDataLayer:
 """High-level read API for backtests, on top of DuckDBStore."""
 
 def __init__(
  self,
  store: DuckDBStore | None = None,
  *,
  bootstrap: bool =True,
  read_only: bool =True,
 ):
  if store is None:
   store = DuckDBStore(read_only=read_only)
  self.store = store
  if bootstrap and not read_only:
   self.store.bootstrap_views()
 
 # ---------------- calendar ----------------
 def get_calendar(self, start, end):
  s, e = _as_date(start), _as_date(end)
  return self.store.query(
   "SELECT DISTINCT cal_date, exchange FROM mv_trade_cal WHERE cal_date BETWEEN ? AND ? ORDER BY cal_date, exchange",
   [s.isoformat(), e.isoformat()],
  )
 
 def next_trade_day(self, d):
  d = _as_date(d)
  row = self.store.query(
   "SELECT MIN(cal_date) AS nd FROM (SELECT DISTINCT cal_date FROM mv_trade_cal) WHERE cal_date > ?",
   [d.isoformat()],
  )
  if row.empty or pd.isna(row["nd"].iloc[0]):
   return None
  v = row["nd"].iloc[0]
  return v.date() if hasattr(v, "date") else _as_date(v)
 
 def prev_trade_day(self, d):
  d = _as_date(d)
  row = self.store.query(
   "SELECT MAX(cal_date) AS pd FROM (SELECT DISTINCT cal_date FROM mv_trade_cal) WHERE cal_date < ?",
   [d.isoformat()],
  )
  if row.empty or pd.isna(row["pd"].iloc[0]):
   return None
  v = row["pd"].iloc[0]
  return v.date() if hasattr(v, "date") else _as_date(v)
 
 # ---------------- universe ----------------
 def get_universe(self, as_of, *, min_list_date=date(2010,1,1), exclude_st=True):
  """Historical-time-point stock universe."""
  d = _as_date(as_of)
  min_d = _as_date(min_list_date) if min_list_date else None
  cols = self._stock_basic_columns()
  select_cols = ["ts_code", "symbol", "name", "industry", "list_date"]
  for c in ("exchange", "list_status", "delist_date", "curr_type"):
   if c in cols:
    select_cols.append(c)
  clauses = ["list_date IS NOT NULL", "list_date <= ?"]
  params = [d.isoformat()]
  if "list_status" in cols and "delist_date" in cols:
   clauses.append("(list_status IS NULL OR list_status = 'L' OR (list_status = 'D' AND delist_date > ?) OR (list_status = 'P' AND delist_date > ?))")
   params.append(d.isoformat())
   params.append(d.isoformat())
  elif "list_status" in cols:
   clauses.append("(list_status IS NULL OR list_status NOT IN ('D', 'P'))")
  if "delist_date" in cols:
   clauses.append("(delist_date IS NULL OR delist_date > ?)")
   params.append(d.isoformat())
  if min_d is not None:
   clauses.append("list_date <= ?")
   params.append(min_d.isoformat())
  if exclude_st and "name" in cols:
   clauses.append("UPPER(name) NOT LIKE '%ST%'")
  where_sql = " AND ".join(clauses)
  sql = f"""SELECT DISTINCT {", ".join(select_cols)} FROM ({_read_stock_basic_sql()}) sb WHERE {where_sql} ORDER BY ts_code"""
  return self.store.query(sql, params)
 
 def _stock_basic_columns(self):
  try:
   df = self.store.query(f"DESCRIBE ({_read_stock_basic_sql()})")
   return set(df["column_name"].astype(str).tolist())
  except Exception:
   return set()
 
 def get_index_member(self, as_of, index=DEFAULT_INDEX_PROXY):
  """Index constituents (approximated)."""
  if index != DEFAULT_INDEX_PROXY:
   raise NotImplementedError(f"strict index member {index!r} not synced; use {DEFAULT_INDEX_PROXY!r}")
  uni = self.get_universe(as_of)
  if uni.empty:
   return uni
  mask = uni["ts_code"].str.match(r"^(60|00|30|68)\d{4}\.")
  return uni.loc[mask].reset_index(drop=True)
 
 # ---------------- price series ----------------
 def get_price_series(self, ts_code, start, end, *, qfq=True):
  """Daily price series for one stock in [start, end]."""
  view = "mv_daily_qfq" if qfq else "mv_daily_v1"
  s, e = _as_date(start), _as_date(end)
  amount_col = "amount" if qfq else "amount_yuan"
  sql = f"""WITH deduped AS (SELECT *, ROW_NUMBER() OVER (PARTITION BY ts_code, trade_date ORDER BY (SELECT NULL)) AS _rn FROM {view} WHERE ts_code = ? AND trade_date BETWEEN ? AND ?) SELECT trade_date, open, high, low, close, pre_close, vol, {amount_col} AS amount FROM deduped WHERE _rn = 1 ORDER BY trade_date"""
  df = self.store.query(sql, [ts_code, s.isoformat(), e.isoformat()])
  if df.empty:
   return df
  if qfq and "close" in df.columns:
   df = df.copy()
   df["close_qfq"] = df["close"]
  return df
 
 def get_close_matrix(self, codes, start, end, *, qfq=True):
  """Wide close-price matrix (index=trade_date, columns=ts_code)."""
  codes = list(codes)
  if not codes:
   return pd.DataFrame()
  view = "mv_daily_qfq" if qfq else "mv_daily_v1"
  s, e = _as_date(start), _as_date(end)
  placeholders = ",".join(["?"] * len(codes))
  sql = f"""WITH deduped AS (SELECT ts_code, trade_date, close, ROW_NUMBER() OVER (PARTITION BY ts_code, trade_date ORDER BY (SELECT NULL)) AS _rn FROM {view} WHERE ts_code IN ({placeholders}) AND trade_date BETWEEN ? AND ?) SELECT trade_date, ts_code, close FROM deduped WHERE _rn = 1 ORDER BY trade_date"""
  params = list(codes) + [s.isoformat(), e.isoformat()]
  df = self.store.query(sql, params)
  if df.empty:
   return df
  wide = df.pivot(index="trade_date", columns="ts_code", values="close")
  wide = wide.sort_index()
  return wide
 
 def get_amount_matrix(self, codes, start, end, *, qfq=True):
  """Wide amount matrix (index=trade_date, columns=ts_code). Returns yuan."""
  codes = list(codes)
  if not codes:
   return pd.DataFrame()
  view = "mv_daily_qfq" if qfq else "mv_daily_v1"
  amount_col = "amount" if qfq else "amount_yuan"
  s, e = _as_date(start), _as_date(end)
  placeholders = ",".join(["?"] * len(codes))
  sql = f"""WITH deduped AS (SELECT ts_code, trade_date, {amount_col} AS amount, ROW_NUMBER() OVER (PARTITION BY ts_code, trade_date ORDER BY (SELECT NULL)) AS _rn FROM {view} WHERE ts_code IN ({placeholders}) AND trade_date BETWEEN ? AND ?) SELECT trade_date, ts_code, amount FROM deduped WHERE _rn = 1 ORDER BY trade_date"""
  params = list(codes) + [s.isoformat(), e.isoformat()]
  df = self.store.query(sql, params)
  if df.empty:
   return df
  wide = df.pivot(index="trade_date", columns="ts_code", values="amount")
  wide = wide.sort_index()
  if qfq:
   wide = wide *1000.0
  return wide
 
 # ---------------- status checks ----------------
 def is_suspended(self, ts_code, d):
  """Heuristic: high==low AND vol== 0 (and amount== 0)."""
  d_ = _as_date(d)
  row = self.store.query("SELECT high, low, vol, amount_yuan FROM mv_daily_v1 WHERE ts_code = ? AND trade_date = ?", [ts_code, d_.isoformat()])
  if row.empty:
   return False
  r = row.iloc[0]
  h, l, v = r["high"], r["low"], r["vol"]
  if pd.isna(h) or pd.isna(l) or pd.isna(v):
   return False
  try:
   return float(h) == float(l) and float(v) == 0.0
  except (TypeError, ValueError):
   return False
 
 def is_limit_up(self, ts_code, d, *, tol= 1e-6):
  return self._is_limit(ts_code, d, side="up", tol=tol)
 
 def is_limit_down(self, ts_code, d, *, tol= 1e-6):
  return self._is_limit(ts_code, d, side="down", tol=tol)
 
 def _is_limit(self, ts_code, d, *, side, tol):
  d_ = _as_date(d)
  row = self.store.query("SELECT close, pre_close, high, low FROM mv_daily_v1 WHERE ts_code = ? AND trade_date = ?", [ts_code, d_.isoformat()])
  if row.empty:
   return False
  r = row.iloc[0]
  c, pc, h, l = r["close"], r["pre_close"], r["high"], r["low"]
  if pd.isna(c) or pd.isna(pc):
   return False
  try:
   c = float(c); pc = float(pc)
  except (TypeError, ValueError):
   return False
  band = LimitBand.for_code(ts_code)
  if side == "up":
   ref = h; target = round(pc * (1.0 + band.pct),2)
   return bool(abs(ref - target) <= tol)
  else:
   ref = l; target = round(pc * (1.0 - band.pct),2)
   return bool(abs(ref - target) <= tol)
 
 # ---------------- diagnostics ----------------
 def table_row_counts(self):
  """Approximate row counts for the5 canonical raw tables."""
  out = {}
  for topic in ("daily", "adj_factor", "daily_basic", "trade_cal", "stock_basic"):
   glob = _topic_glob(topic)
   try:
    r = self.store.query(f"SELECT count(*) AS n FROM read_parquet('{glob}')")
    out[topic] = int(r["n"].iloc[0])
   except Exception:
    out[topic] = 0
  return out


# ---- helpers ----
def _as_date(v):
 if isinstance(v, date) and not isinstance(v, datetime):
  return v
 if isinstance(v, datetime):
  return v.date()
 if isinstance(v, pd.Timestamp):
  return v.date()
 if isinstance(v, str):
  return datetime.strptime(v, "%Y-%m-%d").date()
 raise TypeError(f"unsupported date value: {v!r}")

def _topic_glob(topic):
 return str(_default_data_dir() / f"raw_tushare_{topic}" / "**" / "*.parquet")

def _stock_basic_glob():
 return str(_default_data_dir() / "raw_tushare_stock_basic" / "**" / "*.parquet")

def _read_stock_basic_sql():
 return f"SELECT * FROM read_parquet('{_stock_basic_glob()}', union_by_name=true)"
