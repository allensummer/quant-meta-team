-- mv_trade_cal: open trading days only (v0.4 §3.3).
SELECT
  exchange,
  cal_date,
  is_open,
  pretrade_date
FROM read_parquet(@trade_cal_tushare@)
WHERE is_open = 1
  AND cal_date IS NOT NULL
