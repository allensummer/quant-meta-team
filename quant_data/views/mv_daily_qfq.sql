-- mv_daily_qfq: forward-adjusted close (v0.4 §3.3 + tushare doc_id=28).
--   close_qfq = close * adj_factor / latest_adj_factor (per ts_code).
-- We compute the latest adj_factor per ts_code by taking the max trade_date
-- partition in the adj_factor parquet tree, then join.
WITH latest AS (
  SELECT ts_code, adj_factor AS latest_adj_factor
  FROM read_parquet(@adj_factor_tushare@)
  QUALIFY ROW_NUMBER() OVER (PARTITION BY ts_code ORDER BY trade_date DESC) = 1
)
SELECT
  d.ts_code,
  d.trade_date,
  d.open, d.high, d.low, d.close, d.pre_close, d.change, d.pct_chg,
  d.vol, d.amount,
  d.close * a.adj_factor / NULLIF(l.latest_adj_factor, 0) AS close_qfq,
  a.adj_factor                                       AS adj_factor
FROM read_parquet(@daily_tushare@)        d
JOIN read_parquet(@adj_factor_tushare@)   a
  ON d.ts_code = a.ts_code AND d.trade_date = a.trade_date
JOIN latest l
  ON d.ts_code = l.ts_code
