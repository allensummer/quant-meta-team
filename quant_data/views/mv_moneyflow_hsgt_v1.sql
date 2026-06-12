-- mv_moneyflow_hsgt_v1: per-day cross-border (沪深港通) capital flow (v0.8 — ADM-652).
-- 1 row/trading day. PK: (trade_date). Joins to mv_daily_v1 on trade_date
-- (LEFT JOIN, no ts_code — be careful of cartesian product if joined
-- without filtering).
SELECT
  trade_date,
  ggt_ss, ggt_sz,
  hgt, sgt,
  north_money, south_money,
  'tushare' AS provenance
FROM read_parquet(@moneyflow_hsgt_tushare@)
WHERE trade_date IS NOT NULL
