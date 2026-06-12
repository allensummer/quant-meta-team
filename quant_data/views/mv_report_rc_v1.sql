-- mv_report_rc_v1: 研报内容(v0.9 — ADM-653 Batch 3)
-- PK: (ts_code, report_date, org_name, author_name)
SELECT
  ts_code, name, report_date, report_title, report_type,
  org_name, author_name, rating, rating_change,
  target_price, industry_name, title_keyword,
  'tushare' AS provenance
FROM read_parquet(@report_rc_tushare@)
WHERE ts_code IS NOT NULL AND report_date IS NOT NULL
