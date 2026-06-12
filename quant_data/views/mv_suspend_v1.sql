-- mv_suspend_v1: 停复牌事件(v0.9 — ADM-653 Batch 1)
-- PK: (ts_code, suspend_date)
SELECT
  ts_code, suspend_date, resume_date, suspend_type, reason,
  'tushare' AS provenance
FROM read_parquet(@suspend_tushare@)
WHERE ts_code IS NOT NULL AND suspend_date IS NOT NULL
