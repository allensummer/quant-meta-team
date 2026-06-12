-- mv_sw_index_v1: 申万行业指数日线(v0.9 — ADM-653 Batch 1)
-- PK: (index_code, trade_date). 行业轮动策略直接数据源
SELECT
  index_code, index_name, level, trade_date,
  open, high, low, close, change, pct_chg, vol, amount,
  'tushare' AS provenance
FROM read_parquet(@sw_index_tushare@)
WHERE index_code IS NOT NULL AND trade_date IS NOT NULL
