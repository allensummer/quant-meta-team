-- mv_fund_holdings_v1: quarterly fund top-10 holdings (v0.8 — ADM-652).
-- PK: (ts_code, ann_date, end_date, symbol). Quarterly snapshots — no
-- direct join to mv_daily_v1 (fund vs stock), but the ``symbol`` column
-- joins to ``ts_code`` of mv_daily_v1 for institutional-follow signals.
-- Amount columns converted 万元 -> yuan for consistency.
SELECT
  ts_code,
  ann_date,
  end_date,
  symbol,
  stk_name,
  mkv * 10000.0 AS mkv_yuan,
  amount        AS amount_share,
  stk_mkv_ratio,
  'tushare' AS provenance
FROM read_parquet(@fund_holdings_tushare@)
WHERE ts_code  IS NOT NULL
  AND end_date IS NOT NULL
  AND symbol   IS NOT NULL
