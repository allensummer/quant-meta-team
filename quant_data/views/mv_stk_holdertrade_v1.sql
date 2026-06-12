-- mv_stk_holdertrade_v1: 股东增减持(v0.9 — ADM-653 Batch 3)
-- PK: (ts_code, ann_date, holder_name, trade_date)
SELECT
  ts_code, ann_date, holder_name, holder_type, in_de,
  change_vol, change_ratio, after_share, after_ratio,
  avg_price, total_fee, trade_date,
  'tushare' AS provenance
FROM read_parquet(@stk_holdertrade_tushare@)
WHERE ts_code IS NOT NULL AND ann_date IS NOT NULL
