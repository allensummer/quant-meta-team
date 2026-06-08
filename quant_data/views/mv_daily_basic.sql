-- mv_daily_basic: per-stock-per-date fundamental snapshot (v0.4 §3.3).
--   Picked up by DuckDBStore.bootstrap_views() — same machinery as
--   mv_daily_v1 / mv_daily_qfq / mv_daily_hfq / mv_trade_cal.
-- Columns come straight from raw_tushare_daily_basic (Parquet).
SELECT
  ts_code,
  trade_date,
  turnover_rate,
  pe,
  pb,
  total_mv,
  circ_mv
FROM read_parquet(@daily_basic_tushare@)
WHERE ts_code IS NOT NULL AND trade_date IS NOT NULL
