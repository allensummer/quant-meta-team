-- mv_dividend_v1: 分红送股(v0.9 — ADM-653 Batch 1)
-- PK: (ts_code, end_date)
SELECT
  ts_code, end_date, ann_date, div_proc,
  stk_div, stk_bo_rate,
  cash_div, cash_div_tax,
  record_date, ex_date, pay_date,
  'tushare' AS provenance
FROM read_parquet(@dividend_tushare@)
WHERE ts_code IS NOT NULL AND end_date IS NOT NULL
