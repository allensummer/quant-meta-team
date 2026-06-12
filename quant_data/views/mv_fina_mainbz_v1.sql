-- mv_fina_mainbz_v1: 主营业务构成(v0.9 — ADM-653 Batch 2)
-- PK: (ts_code, end_date, bz_item)
SELECT
  ts_code, end_date, bz_item, bz_code,
  bz_sales, bz_profit, bz_cost, curr_type,
  'tushare' AS provenance
FROM read_parquet(@fina_mainbz_tushare@)
WHERE ts_code IS NOT NULL AND end_date IS NOT NULL AND bz_item IS NOT NULL
