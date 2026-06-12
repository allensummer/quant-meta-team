-- mv_balancesheet_v1: 资产负债表(v0.9 — ADM-653 Batch 2)
-- PK: (ts_code, end_date, ann_date, f_ann_date)
SELECT
  ts_code, ann_date, f_ann_date, end_date, report_type,
  total_share, cap_rese, undistr_porfit, surplus_rese,
  money_cap, accounts_receiv, inventories,
  total_cur_assets, total_nca, total_assets,
  st_borr, lt_borr, total_cur_liab, total_ncl, total_liab,
  minority_int, total_hldr_eqy_exc_min_int, total_hldr_eqy_inc_min_int,
  total_liab_hldr_eqy,
  update_flag,
  'tushare' AS provenance
FROM read_parquet(@balancesheet_tushare@)
WHERE ts_code IS NOT NULL AND end_date IS NOT NULL
