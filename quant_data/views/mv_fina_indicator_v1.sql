-- mv_fina_indicator_v1: 财务关键指标(v0.9 — ADM-653 Batch 2)
-- PK: (ts_code, end_date, ann_date). ROE/ROA/ROIC 经典 Barra 因子
SELECT
  ts_code, end_date, ann_date, f_ann_date,
  eps, dt_eps, gross_margin, netprofit_margin, current_ratio,
  quick_ratio, roe, roe_waa, roa, npta, roic,
  roe_yearly, roa2_yearly, debt_to_assets, debt_to_eqt, equity_yoy,
  update_flag,
  'tushare' AS provenance
FROM read_parquet(@fina_indicator_tushare@)
WHERE ts_code IS NOT NULL AND end_date IS NOT NULL AND ann_date IS NOT NULL
