-- mv_margin_detail_v1: 融资融券明细(v0.9 — ADM-653 Batch 3)
-- PK: (trade_date, ts_code). 日频 × 5千只 × 5年 = ~6M 行
SELECT
  trade_date, ts_code,
  rzye, rqye, rzmre, rqyl, rzche, rqchl, rqmcl, rzrqye,
  'tushare' AS provenance
FROM read_parquet(@margin_detail_tushare@)
WHERE trade_date IS NOT NULL AND ts_code IS NOT NULL
