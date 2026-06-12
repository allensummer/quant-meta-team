-- mv_index_daily_v1: 指数日线行情(v0.9 — ADM-653 Batch 1)
-- PK: (ts_code, trade_date). Joins to mv_daily_v1 on trade_date for基准/行业相对强弱
SELECT
  ts_code, trade_date, open, high, low, close,
  pre_close, change, pct_chg, vol, amount,
  'tushare' AS provenance
FROM read_parquet(@index_daily_tushare@)
WHERE ts_code IS NOT NULL AND trade_date IS NOT NULL
