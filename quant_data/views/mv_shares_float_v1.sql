-- mv_shares_float_v1: 限售解禁事件(v0.9 — ADM-653 Batch 1)
-- PK: (ts_code, float_date, holder_name)
SELECT
  ts_code, float_date, float_share, float_ratio,
  holder_name, share_type,
  'tushare' AS provenance
FROM read_parquet(@shares_float_tushare@)
WHERE ts_code IS NOT NULL AND float_date IS NOT NULL AND holder_name IS NOT NULL
