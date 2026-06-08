-- mv_daily_hfq: backward-adjusted close (v0.4 §3.3).
--   close_hfq = close * adj_factor / first_adj_factor (per ts_code).
WITH first AS (
  SELECT ts_code, adj_factor AS first_adj_factor
  FROM read_parquet(@adj_factor_tushare@)
  QUALIFY ROW_NUMBER() OVER (PARTITION BY ts_code ORDER BY trade_date ASC) = 1
)
SELECT
  d.ts_code,
  d.trade_date,
  d.open, d.high, d.low, d.close, d.pre_close, d.change, d.pct_chg,
  d.vol, d.amount,
  d.close * a.adj_factor / NULLIF(f.first_adj_factor, 0) AS close_hfq,
  a.adj_factor                                       AS adj_factor
FROM read_parquet(@daily_tushare@)        d
JOIN read_parquet(@adj_factor_tushare@)   a
  ON d.ts_code = a.ts_code AND d.trade_date = a.trade_date
JOIN first f
  ON d.ts_code = f.ts_code
