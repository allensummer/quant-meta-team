-- mv_top10_floatholders_v1: 前十大流通股东(v0.9 — ADM-653 Batch 3)
-- PK: (ts_code, ann_date, end_date, holder_name)
SELECT
  ts_code, ann_date, end_date, holder_name,
  hold_amount, hold_ratio, hold_float_ratio, hold_change, holder_type,
  'tushare' AS provenance
FROM read_parquet(@top10_floatholders_tushare@)
WHERE ts_code IS NOT NULL AND ann_date IS NOT NULL
