"""Rebalance-schedule rules for Week4 strategy iteration (ADM-619)."""
from __future__ import annotations

import datetime
from typing import Iterable

from quant_risk.backtest import _to_date


MONTHLY = "monthly"
QUARTERLY = "quarterly"
WEEKLY = "weekly"
SUPPORTED_FREQS = (MONTHLY, QUARTERLY, WEEKLY)


def build_rebalance_schedule(
  trade_days: Iterable[datetime.date],
  *,
  start: datetime.date,
  end: datetime.date,
  freq: str,
) -> list[datetime.date]:
  """Return rebalance date list for the given frequency."""
  if freq not in SUPPORTED_FREQS:
   raise ValueError(f"unsupported freq {freq!r}; expected one of {SUPPORTED_FREQS!r}")
  td = sorted({_to_date(d) for d in trade_days})
  if not td:
   return []
  in_window = [d for d in td if start <= d <= end]
  if not in_window:
   return []
  if freq == MONTHLY:
   return _first_of_month(in_window)
  if freq == QUARTERLY:
   return _first_of_quarter(in_window)
  # WEEKLY
  return _first_of_iso_week(in_window)


def _first_of_month(dates: list[datetime.date]) -> list[datetime.date]:
  seen: set[tuple[int, int]] = set()
  out: list[datetime.date] = []
  for d in dates:
   key = (d.year, d.month)
   if key in seen:
    continue
   seen.add(key)
   out.append(d)
  return out


def _first_of_quarter(dates: list[datetime.date]) -> list[datetime.date]:
  seen: set[tuple[int, int]] = set()
  out: list[datetime.date] = []
  for d in dates:
   q = (d.month -1) //3
   key = (d.year, q)
   if key in seen:
    continue
   seen.add(key)
   out.append(d)
  return out


def _first_of_iso_week(dates: list[datetime.date]) -> list[datetime.date]:
  seen: set[tuple[int, int, int]] = set()
  out: list[datetime.date] = []
  for d in dates:
   iso_year, iso_week, _ = d.isocalendar()
   key = (iso_year, iso_week,0)
   if key in seen:
    continue
   seen.add(key)
   out.append(d)
  return out


def events_per_year(freq: str) -> float:
  """Approximate number of rebalance events per calendar year."""
  if freq == MONTHLY:
   return 12.0
  if freq == QUARTERLY:
   return 4.0
  if freq == WEEKLY:
   return 48.0
  raise ValueError(f"unsupported freq {freq!r}")


__all__ = [
  "MONTHLY",
  "QUARTERLY",
  "WEEKLY",
  "SUPPORTED_FREQS",
  "build_rebalance_schedule",
  "events_per_year",
]
