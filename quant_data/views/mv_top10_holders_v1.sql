-- mv_top10_holders_v1: 前十大股东(总股本口径)(v0.9 — ADM-653 Batch 2)
-- PK: (ts_code, ann_date, end_date, holder_name)
SELECT
  ts_code, ann_date, end_date, holder_name,
  hold_amount, hold_ratio, hold_float_ratio, hold_change, holder_type,
  'tushare' AS provenance
FROM read_parquet(@top10_holders_tushare@)
WHERE ts_code IS NOT NULL AND ann_date IS NOT NULL
