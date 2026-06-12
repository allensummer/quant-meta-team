-- mv_fina_audit_v1: 审计意见(v0.9 — ADM-653 Batch 2)
-- PK: (ts_code, end_date)
SELECT
  ts_code, end_date, ann_date, audit_result,
  audit_firm, audit_sign, audit_date,
  'tushare' AS provenance
FROM read_parquet(@fina_audit_tushare@)
WHERE ts_code IS NOT NULL AND end_date IS NOT NULL
