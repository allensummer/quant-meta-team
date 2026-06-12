-- mv_index_classify_v1: 指数树状分类(申万/中证/沪深)(v0.9 — ADM-653 Batch 1)
-- PK: index_code
SELECT
  index_code, index_name, industry_name, level,
  is_published, src, weight_rule, exchange,
  list_date, exp_date,
  'tushare' AS provenance
FROM read_parquet(@index_classify_tushare@)
WHERE index_code IS NOT NULL
