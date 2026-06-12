-- mv_cashflow_v1: 现金流量表(v0.9 — ADM-653 Batch 2)
-- PK: (ts_code, end_date, ann_date, f_ann_date). Sloan 因子输入
SELECT
  ts_code, ann_date, f_ann_date, end_date, report_type,
  net_profit,
  c_fr_sale_sg, c_paid_goods_s, c_paid_to_for_empl, c_paid_for_taxes,
  net_cash_flows_oper,
  net_cash_flows_inv_act,
  net_cash_flows_fin_act,
  n_incr_cash_cash_equ, c_cash_equ_beg_period, c_cash_equ_end_period,
  free_cashflow,
  update_flag,
  'tushare' AS provenance
FROM read_parquet(@cashflow_tushare@)
WHERE ts_code IS NOT NULL AND end_date IS NOT NULL
