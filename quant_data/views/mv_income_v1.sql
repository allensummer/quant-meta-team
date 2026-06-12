-- mv_income_v1: 利润表(v0.9 — ADM-653 Batch 2)
-- PK: (ts_code, end_date, ann_date, f_ann_date)
SELECT
  ts_code, ann_date, f_ann_date, end_date, report_type,
  basic_eps, diluted_eps, total_revenue, revenue,
  total_cogs, oper_cost, sell_exp, admin_exp, fin_exp,
  operate_profit, total_profit, income_tax, n_income, n_income_attr_p,
  ebit, ebitda, rd_exp,
  update_flag,
  'tushare' AS provenance
FROM read_parquet(@income_tushare@)
WHERE ts_code IS NOT NULL AND end_date IS NOT NULL
