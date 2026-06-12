-- mv_stk_limit_v1: 每日涨跌停价(v0.9 — ADM-653 Batch 1)
-- PK: (ts_code, trade_date). 回测时判定『不可交易』
SELECT
  ts_code, trade_date, up_limit, down_limit,
  'tushare' AS provenance
FROM read_parquet(@stk_limit_tushare@)
WHERE ts_code IS NOT NULL AND trade_date IS NOT NULL
