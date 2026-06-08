-- mv_daily_v1: multi-source FULL OUTER JOIN skeleton (v0.4 §6.3).
-- Currently only tushare is materialized. Adding a new source means adding
-- a UNION of its @topic_<source>@ glob here. The provenance column records
-- which source supplied the row so Portfolio/Risk can audit.
SELECT
  t.ts_code                                       AS ts_code,
  t.trade_date                                    AS trade_date,
  t.open, t.high, t.low, t.close, t.pre_close,
  t.change, t.pct_chg,
  t.vol,                                          -- 1 lot = 100 shares
  t.amount * 1000.0                               AS amount_yuan,
  'tushare'                                       AS provenance
FROM read_parquet(@daily_tushare@) t
WHERE t.ts_code IS NOT NULL AND t.trade_date IS NOT NULL
