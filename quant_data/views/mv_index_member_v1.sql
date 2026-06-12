-- mv_index_member_v1: 指数成分股调整历史(v0.9 — ADM-653 Batch 1)
-- PK: (index_code, con_code, in_date)
SELECT
  index_code, con_code, in_date, out_date, is_new,
  'tushare' AS provenance
FROM read_parquet(@index_member_tushare@)
WHERE index_code IS NOT NULL AND con_code IS NOT NULL AND in_date IS NOT NULL
