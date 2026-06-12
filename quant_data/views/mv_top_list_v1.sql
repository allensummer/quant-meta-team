-- mv_top_list_v1: 龙虎榜日榜(v0.9 — ADM-653 Batch 3)
-- PK: (trade_date, ts_code)
SELECT
  trade_date, ts_code, name, close, pct_change, turnover_rate, amount,
  l_sell, l_buy, l_amount, net_amount, net_rate, amount_rate,
  float_values, reason, net_buy_amount, sell_amount, buy_amount,
  'tushare' AS provenance
FROM read_parquet(@top_list_tushare@)
WHERE trade_date IS NOT NULL AND ts_code IS NOT NULL
