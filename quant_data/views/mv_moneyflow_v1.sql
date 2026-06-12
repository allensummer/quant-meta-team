-- mv_moneyflow_v1: per-stock-per-day main/small order money flow (v0.8 — ADM-652).
-- PK: (ts_code, trade_date). Joins to mv_daily_v1 on (ts_code, trade_date) for
-- price+flow factor inputs. Amount columns are converted 千元 -> yuan for
-- downstream convenience.
SELECT
  ts_code,
  trade_date,
  buy_sm_vol, sell_sm_vol,
  buy_md_vol, sell_md_vol,
  buy_lg_vol, sell_lg_vol,
  buy_elg_vol, sell_elg_vol,
  net_mf_vol,
  buy_sm_amount  * 1000.0 AS buy_sm_amount_yuan,
  sell_sm_amount * 1000.0 AS sell_sm_amount_yuan,
  buy_md_amount  * 1000.0 AS buy_md_amount_yuan,
  sell_md_amount * 1000.0 AS sell_md_amount_yuan,
  buy_lg_amount  * 1000.0 AS buy_lg_amount_yuan,
  sell_lg_amount * 1000.0 AS sell_lg_amount_yuan,
  buy_elg_amount  * 1000.0 AS buy_elg_amount_yuan,
  sell_elg_amount * 1000.0 AS sell_elg_amount_yuan,
  net_mf_amount   * 1000.0 AS net_mf_amount_yuan,
  'tushare' AS provenance
FROM read_parquet(@moneyflow_tushare@)
WHERE ts_code IS NOT NULL AND trade_date IS NOT NULL
