-- mv_index_weight_v1: index constituent weights (v0.8 — ADM-652).
-- PK: (index_code, con_code, trade_date). Joins to mv_daily_v1 on
-- (ts_code = con_code, trade_date) for index-tracking error / smart-beta
-- factor construction.
SELECT
  index_code,
  con_code,
  trade_date,
  weight,
  'tushare' AS provenance
FROM read_parquet(@index_weight_tushare@)
WHERE index_code IS NOT NULL
  AND con_code   IS NOT NULL
  AND trade_date IS NOT NULL
