-- mv_hsgt_top10_v1: per-day top-10 northbound net-buy stocks (v0.8 — ADM-652).
-- PK: (trade_date, ts_code). Up to 10 rows/day. Joins to mv_daily_v1 on
-- (ts_code, trade_date) for "smart money follow" factor construction.
-- Columns reflect tushare dry-run 2026-06-12 (11 columns, amount in yuan).
SELECT
  trade_date,
  ts_code,
  name,
  close,
  change,
  rank,
  market_type,
  amount,
  net_amount,
  buy,
  sell,
  'tushare' AS provenance
FROM read_parquet(@hsgt_top10_tushare@)
WHERE trade_date IS NOT NULL AND ts_code IS NOT NULL
