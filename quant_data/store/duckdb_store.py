"""DuckDB-backed query engine (v0.4 §2 + §6.3).

Owns the single ``quant.duckdb`` file. Materializes ``mv_*`` views over the
Parquet files written by ``ParquetStore``.
"""
from __future__ import annotations

import logging
from datetime import date
from pathlib import Path
from typing import Any, Mapping

import duckdb
import pandas as pd

from quant_data.paths import duckdb_path
from quant_data.schemas import SCHEMAS, TableSchema
from quant_data.store.parquet_store import ParquetStore

log = logging.getLogger("quant_data.store.duckdb")


def _view_sql_path(view_name: str) -> Path:
    from quant_data.paths import data_dir
    return data_dir() / "views_runtime" / f"{view_name}.sql"


class DuckDBStore:
    """Single-file DuckDB database; idempotent view bootstrap."""

    def __init__(self, path: Path | None = None, *, read_only: bool = False):
        """Open (or create) the DuckDB file.

        Parameters
        ----------
        path : Path, optional
            Defaults to :func:`quant_data.paths.duckdb_path`.
        read_only : bool, default False
            When True, the file is opened in DuckDB's native read-only mode
            (multiple readers can attach the same file concurrently; writes
            raise ``InvalidInputException``). Portfolio / Risk agents should
            pass ``read_only=True`` so they never block the Data agent's
            writes and vice versa. Default behavior (read-write) is
            unchanged for the Data agent.
        """
        self.path = Path(path) if path else duckdb_path()
        self.read_only = read_only
        if not read_only:
            # In RW mode, ensure the parent exists. In read-only mode we must
            # not touch the filesystem — opening an existing file must work
            # even if its parent dir is read-only.
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self.con = duckdb.connect(str(self.path), read_only=read_only)
        if not read_only:
            # threads=4 is a writer-side default; readers inherit whatever
            # the writer set, so don't override.
            self.con.execute("PRAGMA threads=4")
        # All Parquet under data_dir
        self._conventions: dict[str, str] = {}  # topic -> glob

    # ---------------- DataStore protocol ----------------
    def register_schema(self, schema: TableSchema) -> None:
        SCHEMAS[(schema.table, schema.version)] = schema

    def register_parquet(self, topic: str, store: ParquetStore) -> None:
        self._conventions[topic] = store.glob_for_duckdb()

    def query(self, sql: str, params: Mapping[str, Any] | None = None) -> pd.DataFrame:
        if params:
            return self.con.execute(sql, params).df()
        return self.con.execute(sql).df()

    def upsert(self, table: str, df: pd.DataFrame, schema_version: str,
               primary_key: list[str] | None = None) -> int:
        """Materialize a single-batch df into the per-source raw_* table backing the view.

        Parameters
        ----------
        primary_key : list[str], optional
            The table's primary-key columns. When supplied, the delete clause
            is built from this list (covers ``(index_code, con_code, trade_date)``,
            ``(ts_code, ann_date, end_date, symbol)``, etc.). When not supplied,
            the legacy column-name heuristics below are used.
        """
        if df is None or df.empty:
            return 0
        view = f"raw_{table}"  # expected pattern, e.g. raw_tushare_daily
        # ensure underlying table exists (in case open_store wasn't called).
        # Build it from a SELECT * on the batch itself so column count always
        # matches the batch — avoids mismatch with the bootstrap placeholder
        # which may have more columns than the user-supplied df.
        self.con.execute(
            f"CREATE TABLE IF NOT EXISTS {view} AS SELECT * FROM df WHERE 1=0"
        )
        # Use a temp table to make the per-key DELETE robust to type coercions
        # (date <-> string, etc.) which can silently fail in IN-subqueries.
        self.con.execute("CREATE OR REPLACE TEMP TABLE _upsert_batch AS SELECT * FROM df")
        # 1) Explicit PK from caller (preferred — covers all S-tier v0.8 tables)
        if primary_key and all(c in df.columns for c in primary_key):
            key_tuple = ", ".join(primary_key)
            select_tuple = ", ".join(f"{c} AS {c}" for c in primary_key)
            self.con.execute(
                f"DELETE FROM {view} WHERE ({key_tuple}) IN "
                f"(SELECT {select_tuple} FROM _upsert_batch)"
            )
        # 2) Legacy column-name heuristics (kept for backwards compat)
        elif "trade_date" in df.columns and "ts_code" in df.columns:
            self.con.execute(
                f"DELETE FROM {view} WHERE (ts_code, trade_date) IN "
                f"(SELECT ts_code, trade_date FROM _upsert_batch)"
            )
        elif "trade_date" in df.columns and "ts_code" not in df.columns:
            # moneyflow_hsgt — single PK on trade_date
            self.con.execute(
                f"DELETE FROM {view} WHERE trade_date IN "
                f"(SELECT trade_date FROM _upsert_batch)"
            )
        elif "cal_date" in df.columns and "exchange" in df.columns:
            self.con.execute(
                f"DELETE FROM {view} WHERE (exchange, cal_date) IN "
                f"(SELECT exchange, cal_date FROM _upsert_batch)"
            )
        elif "ts_code" in df.columns and "trade_date" not in df.columns:
            # stock_basic snapshot: drop & re-insert the entire universe
            self.con.execute(f"DELETE FROM {view}")
        # Insert by explicit column list to be robust against schema-mismatched
        # placeholders that may have a different column set.
        cols = ", ".join(df.columns)
        self.con.execute(f"INSERT INTO {view} ({cols}) SELECT {cols} FROM _upsert_batch")
        self.con.execute("DROP TABLE _upsert_batch")
        return len(df)

    def get_cursor(self, table: str) -> date | None:
        try:
            row = self.con.execute(
                'SELECT last_trade_date FROM sync_state WHERE "table" = ?', [table]
            ).fetchone()
        except duckdb.CatalogException:
            return None
        if row and row[0] is not None:
            try:
                return date.fromisoformat(str(row[0]))
            except ValueError:
                return None
        return None

    def set_cursor(self, table: str, d: date, status: str = "ok", error: str = "",
                   first_trade_date: date | None = None) -> None:
        self.con.execute(
            '''
            CREATE TABLE IF NOT EXISTS sync_state (
                "table" VARCHAR PRIMARY KEY,
                last_trade_date DATE,
                first_trade_date DATE,
                last_run_at TIMESTAMP,
                status VARCHAR,
                error_msg VARCHAR
            )
            '''
        )
        from datetime import datetime
        # ``first_trade_date`` is sticky: only set it when the column was
        # previously NULL. This preserves the 20y-backfill lower bound across
        # subsequent incremental syncs.
        existing = self.con.execute(
            'SELECT first_trade_date FROM sync_state WHERE "table" = ?', [table]
        ).fetchone()
        existing_first = existing[0] if existing else None
        effective_first = first_trade_date if first_trade_date is not None else existing_first
        self.con.execute(
            '''
            INSERT INTO sync_state ("table", last_trade_date, first_trade_date, last_run_at, status, error_msg)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT ("table") DO UPDATE SET
                last_trade_date = excluded.last_trade_date,
                first_trade_date = COALESCE(sync_state.first_trade_date, excluded.first_trade_date),
                last_run_at = excluded.last_run_at,
                status = excluded.status,
                error_msg = excluded.error_msg
            ''',
            [table, d, effective_first, datetime.now(), status, error],
        )

    # ---------------- views ----------------
    def bootstrap_views(self) -> list[str]:
        """Create the canonical views (mv_daily_v1 / qfq / hfq / trade_cal).

        Reads SQL files from ``quant_data/views/`` (shipped with the package)
        and substitutes the data_dir glob for each source.

        To keep views queryable before any sync has run, we seed an empty
        placeholder Parquet file into each ``raw_tushare_<topic>`` tree. This
        means ``read_parquet(glob)`` always returns 0 rows rather than erroring
        with "No files found", which lets downstream tools (DBeaver, portfolio
        notebooks, the Risk agent) introspect the schema pre-sync.
        """
        from quant_data import views as _views_pkg
        view_dir = Path(_views_pkg.__file__).parent
        self._ensure_placeholder_files()
        created: list[str] = []
        for sql_path in sorted(view_dir.glob("*.sql")):
            sql = sql_path.read_text(encoding="utf-8")
            sql = self._inject_globals(sql)
            view_name = sql_path.stem  # e.g. mv_daily_v1
            self.con.execute(f"CREATE OR REPLACE VIEW {view_name} AS {sql}")
            log.info("duckdb: view %s ready", view_name)
            created.append(view_name)
        return created

    def _ensure_placeholder_files(self) -> None:
        """Write empty Parquet placeholders so read_parquet globs never fail.

        The placeholders use the *real* dtypes so the view SQL can do
        arithmetic (``close * adj_factor``) without binder errors.
        """
        import pyarrow as pa
        import pyarrow.parquet as pq
        from quant_data.paths import data_dir
        # topic -> ordered list of (name, arrow_type)
        topics: dict[str, list[tuple[str, "pa.DataType"]]] = {
            "daily": [
                ("ts_code", pa.string()),
                ("trade_date", pa.date32()),
                ("open", pa.float64()),
                ("high", pa.float64()),
                ("low", pa.float64()),
                ("close", pa.float64()),
                ("pre_close", pa.float64()),
                ("change", pa.float64()),
                ("pct_chg", pa.float64()),
                ("vol", pa.float64()),
                ("amount", pa.float64()),
            ],
            "adj_factor": [
                ("ts_code", pa.string()),
                ("trade_date", pa.date32()),
                ("adj_factor", pa.float64()),
            ],
            "daily_basic": [
                ("ts_code", pa.string()),
                ("trade_date", pa.date32()),
                ("turnover_rate", pa.float64()),
                ("pe", pa.float64()),
                ("pb", pa.float64()),
                ("total_mv", pa.float64()),
                ("circ_mv", pa.float64()),
            ],
            "trade_cal": [
                ("exchange", pa.string()),
                ("cal_date", pa.date32()),
                ("is_open", pa.int8()),
                ("pretrade_date", pa.date32()),
            ],
            "stock_basic": [
                ("ts_code", pa.string()),
                ("symbol", pa.string()),
                ("name", pa.string()),
                ("industry", pa.string()),
                ("exchange", pa.string()),
                ("curr_type", pa.string()),
                ("list_status", pa.string()),
                ("list_date", pa.date32()),
            ],
            # S-tier additions (v0.8 — ADM-652)
            "moneyflow": [
                ("ts_code", pa.string()),
                ("trade_date", pa.date32()),
                ("buy_sm_vol", pa.float64()), ("buy_sm_amount", pa.float64()),
                ("sell_sm_vol", pa.float64()), ("sell_sm_amount", pa.float64()),
                ("buy_md_vol", pa.float64()), ("buy_md_amount", pa.float64()),
                ("sell_md_vol", pa.float64()), ("sell_md_amount", pa.float64()),
                ("buy_lg_vol", pa.float64()), ("buy_lg_amount", pa.float64()),
                ("sell_lg_vol", pa.float64()), ("sell_lg_amount", pa.float64()),
                ("buy_elg_vol", pa.float64()), ("buy_elg_amount", pa.float64()),
                ("sell_elg_vol", pa.float64()), ("sell_elg_amount", pa.float64()),
                ("net_mf_vol", pa.float64()), ("net_mf_amount", pa.float64()),
            ],
            "moneyflow_hsgt": [
                ("trade_date", pa.date32()),
                ("ggt_ss", pa.float64()), ("ggt_sz", pa.float64()),
                ("hgt", pa.float64()), ("sgt", pa.float64()),
                ("north_money", pa.float64()), ("south_money", pa.float64()),
            ],
            "index_weight": [
                ("index_code", pa.string()),
                ("con_code", pa.string()),
                ("trade_date", pa.date32()),
                ("weight", pa.float64()),
            ],
            "hsgt_top10": [
                ("trade_date", pa.date32()),
                ("ts_code", pa.string()),
                ("name", pa.string()),
                ("close", pa.float64()),
                ("change", pa.float64()),
                ("rank", pa.int32()),
                ("market_type", pa.int32()),
                ("amount", pa.float64()),
                ("net_amount", pa.float64()),
                ("buy", pa.float64()),
                ("sell", pa.float64()),
            ],
            "fund_holdings": [
                ("ts_code", pa.string()),
                ("ann_date", pa.date32()),
                ("end_date", pa.date32()),
                ("symbol", pa.string()),
                ("stk_name", pa.string()),
                ("mkv", pa.float64()),
                ("amount", pa.float64()),
                ("stk_mkv_ratio", pa.float64()),
            ],
            # A-tier additions (v0.9 — ADM-653) Batch 1: 基础 + 事件 (8)
            "index_classify": [
                ("index_code", pa.string()),
                ("index_name", pa.string()),
                ("industry_name", pa.string()),
                ("level", pa.string()),
                ("is_published", pa.int32()),
                ("src", pa.string()),
                ("weight_rule", pa.string()),
                ("exchange", pa.string()),
                ("list_date", pa.date32()),
                ("exp_date", pa.date32()),
            ],
            "index_daily": [
                ("ts_code", pa.string()),
                ("trade_date", pa.date32()),
                ("open", pa.float64()),
                ("high", pa.float64()),
                ("low", pa.float64()),
                ("close", pa.float64()),
                ("pre_close", pa.float64()),
                ("change", pa.float64()),
                ("pct_chg", pa.float64()),
                ("vol", pa.float64()),
                ("amount", pa.float64()),
            ],
            "index_member": [
                ("index_code", pa.string()),
                ("con_code", pa.string()),
                ("in_date", pa.date32()),
                ("out_date", pa.date32()),
                ("is_new", pa.string()),
            ],
            "sw_index": [
                ("index_code", pa.string()),
                ("index_name", pa.string()),
                ("level", pa.int32()),
                ("trade_date", pa.date32()),
                ("open", pa.float64()),
                ("high", pa.float64()),
                ("low", pa.float64()),
                ("close", pa.float64()),
                ("change", pa.float64()),
                ("pct_chg", pa.float64()),
                ("vol", pa.float64()),
                ("amount", pa.float64()),
            ],
            "stk_limit": [
                ("ts_code", pa.string()),
                ("trade_date", pa.date32()),
                ("up_limit", pa.float64()),
                ("down_limit", pa.float64()),
            ],
            "suspend": [
                ("ts_code", pa.string()),
                ("suspend_date", pa.date32()),
                ("resume_date", pa.date32()),
                ("suspend_type", pa.string()),
                ("reason", pa.string()),
            ],
            "dividend": [
                ("ts_code", pa.string()),
                ("end_date", pa.date32()),
                ("ann_date", pa.date32()),
                ("div_proc", pa.string()),
                ("stk_div", pa.float64()),
                ("stk_bo_rate", pa.float64()),
                ("cash_div", pa.float64()),
                ("cash_div_tax", pa.float64()),
                ("record_date", pa.date32()),
                ("ex_date", pa.date32()),
                ("pay_date", pa.date32()),
            ],
            "shares_float": [
                ("ts_code", pa.string()),
                ("float_date", pa.date32()),
                ("float_share", pa.float64()),
                ("float_ratio", pa.float64()),
                ("holder_name", pa.string()),
                ("share_type", pa.string()),
            ],
            # A-tier Batch 2: 财务三联表 + 财务指标 (7)
            "fina_indicator": [
                ("ts_code", pa.string()),
                ("end_date", pa.date32()),
                ("ann_date", pa.date32()),
                ("f_ann_date", pa.date32()),
                ("eps", pa.float64()),
                ("dt_eps", pa.float64()),
                ("total_revenue_ps", pa.float64()),
                ("revenue_ps", pa.float64()),
                ("capital_rese_ps", pa.float64()),
                ("surplus_rese_ps", pa.float64()),
                ("undist_profit_ps", pa.float64()),
                ("extra_item", pa.float64()),
                ("profit_dedt", pa.float64()),
                ("gross_margin", pa.float64()),
                ("current_ratio", pa.float64()),
                ("quick_ratio", pa.float64()),
                ("cash_ratio", pa.float64()),
                ("ar_turn", pa.float64()),
                ("ca_turn", pa.float64()),
                ("fa_turn", pa.float64()),
                ("assets_turn", pa.float64()),
                ("op_income", pa.float64()),
                ("ebit", pa.float64()),
                ("ebitda", pa.float64()),
                ("netprofit_margin", pa.float64()),
                ("grossprofit_margin", pa.float64()),
                ("cogs_of_sales", pa.float64()),
                ("expense_of_sales", pa.float64()),
                ("profit_to_gr", pa.float64()),
                ("saleexp_to_gr", pa.float64()),
                ("adminexp_to_gr", pa.float64()),
                ("finaexp_to_gr", pa.float64()),
                ("impai_ttm", pa.float64()),
                ("gc_of_gr", pa.float64()),
                ("op_of_gr", pa.float64()),
                ("ebit_of_gr", pa.float64()),
                ("roe", pa.float64()),
                ("roe_waa", pa.float64()),
                ("roe_dt", pa.float64()),
                ("roa", pa.float64()),
                ("npta", pa.float64()),
                ("roic", pa.float64()),
                ("roe_yearly", pa.float64()),
                ("roa2_yearly", pa.float64()),
                ("debt_to_assets", pa.float64()),
                ("assets_to_eqt", pa.float64()),
                ("dp_assets_to_eqt", pa.float64()),
                ("ca_to_assets", pa.float64()),
                ("nca_to_assets", pa.float64()),
                ("tbassets_to_totalassets", pa.float64()),
                ("int_to_talcap", pa.float64()),
                ("eqt_to_talcap", pa.float64()),
                ("currentdebt_to_debt", pa.float64()),
                ("longdeb_to_debt", pa.float64()),
                ("ocf_to_shortdebt", pa.float64()),
                ("debt_to_eqt", pa.float64()),
                ("eqt_to_debt", pa.float64()),
                ("equity_yoy", pa.float64()),
                ("rd_exp", pa.float64()),
                ("update_flag", pa.string()),
            ],
            "income": [
                ("ts_code", pa.string()),
                ("ann_date", pa.date32()),
                ("f_ann_date", pa.date32()),
                ("end_date", pa.date32()),
                ("report_type", pa.string()),
                ("basic_eps", pa.float64()),
                ("diluted_eps", pa.float64()),
                ("total_revenue", pa.float64()),
                ("revenue", pa.float64()),
                ("int_income", pa.float64()),
                ("prem_earned", pa.float64()),
                ("comm_income", pa.float64()),
                ("n_commis_income", pa.float64()),
                ("n_oth_income", pa.float64()),
                ("n_oth_b_income", pa.float64()),
                ("prem_income", pa.float64()),
                ("out_prem", pa.float64()),
                ("une_prem_reser", pa.float64()),
                ("reins_income", pa.float64()),
                ("n_sec_tb_income", pa.float64()),
                ("n_sec_uw_income", pa.float64()),
                ("n_asset_mg_income", pa.float64()),
                ("oth_b_income", pa.float64()),
                ("fv_value_chg_gain", pa.float64()),
                ("invest_income", pa.float64()),
                ("ass_invest_income", pa.float64()),
                ("forex_gain", pa.float64()),
                ("total_cogs", pa.float64()),
                ("oper_cost", pa.float64()),
                ("int_exp", pa.float64()),
                ("comm_exp", pa.float64()),
                ("biz_tax_surchg", pa.float64()),
                ("sell_exp", pa.float64()),
                ("admin_exp", pa.float64()),
                ("fin_exp", pa.float64()),
                ("assets_impair_loss", pa.float64()),
                ("prem_refund", pa.float64()),
                ("compens_payout", pa.float64()),
                ("reser_insur_liab", pa.float64()),
                ("div_pmt", pa.float64()),
                ("reins_exp", pa.float64()),
                ("oper_exp", pa.float64()),
                ("compens_payout_refu", pa.float64()),
                ("insur_reser_refu", pa.float64()),
                ("reins_cost_refund", pa.float64()),
                ("other_costs", pa.float64()),
                ("operate_profit", pa.float64()),
                ("non_oper_income", pa.float64()),
                ("non_oper_exp", pa.float64()),
                ("nca_disploss", pa.float64()),
                ("total_profit", pa.float64()),
                ("income_tax", pa.float64()),
                ("n_income", pa.float64()),
                ("n_income_attr_p", pa.float64()),
                ("minority_gain", pa.float64()),
                ("oth_compr_income", pa.float64()),
                ("t_compr_income", pa.float64()),
                ("compr_inc_attr_p", pa.float64()),
                ("compr_inc_attr_m", pa.float64()),
                ("ebit", pa.float64()),
                ("ebitda", pa.float64()),
                ("insurance_exp", pa.float64()),
                ("undist_profit", pa.float64()),
                ("distable_profit", pa.float64()),
                ("rd_exp", pa.float64()),
                ("fin_exp_int_exp", pa.float64()),
                ("fin_exp_int_inc", pa.float64()),
                ("transfer_surplus_rese", pa.float64()),
                ("transfer_housing_imprest", pa.float64()),
                ("transfer_oth", pa.float64()),
                ("adj_lossgain", pa.float64()),
                ("withdra_legal_surplus", pa.float64()),
                ("withdra_legal_publicfund", pa.float64()),
                ("withdra_biz_devfund", pa.float64()),
                ("withdra_rese_fund", pa.float64()),
                ("withdra_oth_ersu", pa.float64()),
                ("workers_welfare", pa.float64()),
                ("distr_profit_shrhder", pa.float64()),
                ("prfshare_payable_dvd", pa.float64()),
                ("comshare_payable_dvd", pa.float64()),
                ("capit_comstock_div", pa.float64()),
                ("update_flag", pa.string()),
            ],
            "balancesheet": [
                ("ts_code", pa.string()),
                ("ann_date", pa.date32()),
                ("f_ann_date", pa.date32()),
                ("end_date", pa.date32()),
                ("report_type", pa.string()),
                ("total_share", pa.float64()),
                ("cap_rese", pa.float64()),
                ("undistr_porfit", pa.float64()),
                ("surplus_rese", pa.float64()),
                ("special_rese", pa.float64()),
                ("money_cap", pa.float64()),
                ("trad_asset", pa.float64()),
                ("notes_receiv", pa.float64()),
                ("accounts_receiv", pa.float64()),
                ("oth_receiv", pa.float64()),
                ("prepayment", pa.float64()),
                ("div_receiv", pa.float64()),
                ("int_receiv", pa.float64()),
                ("inventories", pa.float64()),
                ("amor_exp", pa.float64()),
                ("nca_within_1y", pa.float64()),
                ("sett_rsrv", pa.float64()),
                ("loanto_oth_bank_fi", pa.float64()),
                ("premium_receiv", pa.float64()),
                ("reinsur_receiv", pa.float64()),
                ("reinsur_res_receiv", pa.float64()),
                ("pur_resale_fa", pa.float64()),
                ("oth_cur_assets", pa.float64()),
                ("total_cur_assets", pa.float64()),
                ("fa_avail_for_sale", pa.float64()),
                ("htm_invest", pa.float64()),
                ("lt_eqt_invest", pa.float64()),
                ("investment_real_estate", pa.float64()),
                ("time_deposits", pa.float64()),
                ("oth_assets", pa.float64()),
                ("lt_rec", pa.float64()),
                ("fix_assets", pa.float64()),
                ("cip", pa.float64()),
                ("const_materials", pa.float64()),
                ("fixed_assets_disp", pa.float64()),
                ("produc_bio_assets", pa.float64()),
                ("oil_and_gas_assets", pa.float64()),
                ("intan_assets", pa.float64()),
                ("r_and_d", pa.float64()),
                ("goodwill", pa.float64()),
                ("lt_amor_exp", pa.float64()),
                ("defer_tax_assets", pa.float64()),
                ("decr_in_disbur", pa.float64()),
                ("oth_nca", pa.float64()),
                ("total_nca", pa.float64()),
                ("cash_reser_cb", pa.float64()),
                ("depos_in_oth_fi", pa.float64()),
                ("prec_metals", pa.float64()),
                ("deriv_assets", pa.float64()),
                ("rr_reins_une_prem", pa.float64()),
                ("rr_reins_outstd_cla", pa.float64()),
                ("rr_reins_lins_liab", pa.float64()),
                ("rr_reins_lthins_liab", pa.float64()),
                ("refund_depos", pa.float64()),
                ("ph_pledge_loans", pa.float64()),
                ("refund_cap_depos", pa.float64()),
                ("indep_acct_assets", pa.float64()),
                ("client_depos", pa.float64()),
                ("client_prov", pa.float64()),
                ("transac_seat_fee", pa.float64()),
                ("invest_as_receiv", pa.float64()),
                ("total_assets", pa.float64()),
                ("lt_borr", pa.float64()),
                ("st_borr", pa.float64()),
                ("cb_borr", pa.float64()),
                ("depos_ib_deposits", pa.float64()),
                ("loan_oth_bank", pa.float64()),
                ("trading_fl", pa.float64()),
                ("notes_payable", pa.float64()),
                ("acct_payable", pa.float64()),
                ("adv_receipts", pa.float64()),
                ("sold_for_repur_fa", pa.float64()),
                ("comm_payable", pa.float64()),
                ("payroll_payable", pa.float64()),
                ("taxes_payable", pa.float64()),
                ("int_payable", pa.float64()),
                ("div_payable", pa.float64()),
                ("oth_payable", pa.float64()),
                ("acc_exp", pa.float64()),
                ("deferred_inc", pa.float64()),
                ("st_bonds_payable", pa.float64()),
                ("payable_to_reinsurer", pa.float64()),
                ("rsrv_insur_cont", pa.float64()),
                ("acting_trading_sec", pa.float64()),
                ("acting_uw_sec", pa.float64()),
                ("non_cur_liab_due_1y", pa.float64()),
                ("oth_cur_liab", pa.float64()),
                ("total_cur_liab", pa.float64()),
                ("bond_payable", pa.float64()),
                ("lt_payable", pa.float64()),
                ("specific_payables", pa.float64()),
                ("estimated_liab", pa.float64()),
                ("defer_tax_liab", pa.float64()),
                ("defer_inc_non_cur_liab", pa.float64()),
                ("oth_ncl", pa.float64()),
                ("total_ncl", pa.float64()),
                ("depos_oth_bfi", pa.float64()),
                ("deriv_liab", pa.float64()),
                ("depos", pa.float64()),
                ("agency_business_liab", pa.float64()),
                ("oth_liab", pa.float64()),
                ("prem_receiv_adva", pa.float64()),
                ("depos_received", pa.float64()),
                ("ph_invest", pa.float64()),
                ("reser_une_prem", pa.float64()),
                ("reser_outstd_claims", pa.float64()),
                ("reser_lins_liab", pa.float64()),
                ("reser_lthins_liab", pa.float64()),
                ("indept_acc_liab", pa.float64()),
                ("pledge_borr", pa.float64()),
                ("indem_payable", pa.float64()),
                ("total_liab", pa.float64()),
                ("treasury_share", pa.float64()),
                ("ordin_risk_reser", pa.float64()),
                ("forex_differ", pa.float64()),
                ("invest_loss_unconf", pa.float64()),
                ("minority_int", pa.float64()),
                ("total_hldr_eqy_exc_min_int", pa.float64()),
                ("total_hldr_eqy_inc_min_int", pa.float64()),
                ("total_liab_hldr_eqy", pa.float64()),
                ("lt_payroll_payable", pa.float64()),
                ("oth_comp_income", pa.float64()),
                ("oth_eqt_tools", pa.float64()),
                ("oth_eqt_tools_p_shr", pa.float64()),
                ("lending_funds", pa.float64()),
                ("acc_receivable", pa.float64()),
                ("st_fin_payable", pa.float64()),
                ("payables", pa.float64()),
                ("hfs_assets", pa.float64()),
                ("hfs_sales", pa.float64()),
                ("update_flag", pa.string()),
            ],
            "cashflow": [
                ("ts_code", pa.string()),
                ("ann_date", pa.date32()),
                ("f_ann_date", pa.date32()),
                ("end_date", pa.date32()),
                ("report_type", pa.string()),
                ("net_profit", pa.float64()),
                ("finan_exp", pa.float64()),
                ("c_fr_sale_sg", pa.float64()),
                ("recp_tax_rdfs", pa.float64()),
                ("n_depos_incr_fi", pa.float64()),
                ("n_incr_loans_cb", pa.float64()),
                ("n_inc_borr_oth_fi", pa.float64()),
                ("prem_fr_orig_contr", pa.float64()),
                ("n_incr_insured_dep", pa.float64()),
                ("n_reinsur_prem", pa.float64()),
                ("n_incr_disp_tfa", pa.float64()),
                ("ifc_cash_incr", pa.float64()),
                ("oth_cash_in_oper", pa.float64()),
                ("st_cash_out_oper", pa.float64()),
                ("c_paid_goods_s", pa.float64()),
                ("c_paid_to_for_empl", pa.float64()),
                ("c_paid_for_taxes", pa.float64()),
                ("n_incr_clt_loan_adv", pa.float64()),
                ("n_incr_dep_cbob", pa.float64()),
                ("c_pay_claims_orig_inco", pa.float64()),
                ("pay_handling_chrg", pa.float64()),
                ("pay_comm_insur_plcy", pa.float64()),
                ("oth_cash_pay_oper", pa.float64()),
                ("st_cash_in_oper", pa.float64()),
                ("net_cash_flows_oper", pa.float64()),
                ("cash_recp_disp_withdrwl_invest", pa.float64()),
                ("cash_recp_return_invest", pa.float64()),
                ("n_cash_flows_fund", pa.float64()),
                ("cash_recp_cap_contrib", pa.float64()),
                ("cash_inflow_oth_invest", pa.float64()),
                ("cash_recp_disp_filt_assets", pa.float64()),
                ("cash_recp_disp_subs", pa.float64()),
                ("cash_recp_disp_oth_op", pa.float64()),
                ("stot_inflows_inv_act", pa.float64()),
                ("c_pay_acq_const_filt_assets", pa.float64()),
                ("c_paid_invest", pa.float64()),
                ("c_paid_disp_filt_assets", pa.float64()),
                ("c_pay_subs", pa.float64()),
                ("c_pay_oth_invest", pa.float64()),
                ("stot_out_inv_act", pa.float64()),
                ("net_cash_flows_inv_act", pa.float64()),
                ("cash_recp_borrow", pa.float64()),
                ("cash_recp_issue_bonds", pa.float64()),
                ("cash_recp_oth_fin_act", pa.float64()),
                ("stot_cash_in_fin_act", pa.float64()),
                ("c_prepay_amt_borr", pa.float64()),
                ("c_pay_dist_dpcp_int_exp", pa.float64()),
                ("c_pay_dividend", pa.float64()),
                ("oth_cash_pay_fin_act", pa.float64()),
                ("stot_cashout_fin_act", pa.float64()),
                ("net_cash_flows_fin_act", pa.float64()),
                ("eff_fx_flu_cash", pa.float64()),
                ("n_incr_cash_cash_equ", pa.float64()),
                ("c_cash_equ_beg_period", pa.float64()),
                ("c_cash_equ_end_period", pa.float64()),
                ("free_cashflow", pa.float64()),
                ("im_net_cashflow_oper_act", pa.float64()),
                ("net_dism_capital_add", pa.float64()),
                ("net_cash_rece_sec", pa.float64()),
                ("credit_impa_loss", pa.float64()),
                ("use_right_asset_dep", pa.float64()),
                ("oth_loss_asset", pa.float64()),
                ("end_bal_cash", pa.float64()),
                ("beg_bal_cash", pa.float64()),
                ("end_bal_cash_equ", pa.float64()),
                ("begin_bal_cash_equ", pa.float64()),
                ("update_flag", pa.string()),
            ],
            "fina_mainbz": [
                ("ts_code", pa.string()),
                ("end_date", pa.date32()),
                ("bz_item", pa.string()),
                ("bz_code", pa.string()),
                ("bz_sales", pa.float64()),
                ("bz_profit", pa.float64()),
                ("bz_cost", pa.float64()),
                ("curr_type", pa.string()),
            ],
            "fina_audit": [
                ("ts_code", pa.string()),
                ("end_date", pa.date32()),
                ("ann_date", pa.date32()),
                ("audit_result", pa.string()),
                ("audit_firm", pa.string()),
                ("audit_sign", pa.string()),
                ("audit_date", pa.date32()),
            ],
            "top10_holders": [
                ("ts_code", pa.string()),
                ("ann_date", pa.date32()),
                ("end_date", pa.date32()),
                ("holder_name", pa.string()),
                ("hold_amount", pa.float64()),
                ("hold_ratio", pa.float64()),
                ("hold_float_ratio", pa.float64()),
                ("hold_change", pa.float64()),
                ("holder_type", pa.string()),
            ],
            # A-tier Batch 3: 资金流 + 研报 + 股东 (5)
            "top_list": [
                ("trade_date", pa.date32()),
                ("ts_code", pa.string()),
                ("name", pa.string()),
                ("close", pa.float64()),
                ("pct_change", pa.float64()),
                ("turnover_rate", pa.float64()),
                ("amount", pa.float64()),
                ("l_sell", pa.float64()),
                ("l_buy", pa.float64()),
                ("l_amount", pa.float64()),
                ("net_amount", pa.float64()),
                ("net_rate", pa.float64()),
                ("amount_rate", pa.float64()),
                ("float_values", pa.float64()),
                ("reason", pa.string()),
                ("net_buy_amount", pa.float64()),
                ("sell_amount", pa.float64()),
                ("buy_amount", pa.float64()),
            ],
            "margin_detail": [
                ("trade_date", pa.date32()),
                ("ts_code", pa.string()),
                ("rzye", pa.float64()),
                ("rqye", pa.float64()),
                ("rzmre", pa.float64()),
                ("rqyl", pa.float64()),
                ("rzche", pa.float64()),
                ("rqchl", pa.float64()),
                ("rqmcl", pa.float64()),
                ("rzrqye", pa.float64()),
            ],
            "top10_floatholders": [
                ("ts_code", pa.string()),
                ("ann_date", pa.date32()),
                ("end_date", pa.date32()),
                ("holder_name", pa.string()),
                ("hold_amount", pa.float64()),
                ("hold_ratio", pa.float64()),
                ("hold_float_ratio", pa.float64()),
                ("hold_change", pa.float64()),
                ("holder_type", pa.string()),
            ],
            "stk_holdertrade": [
                ("ts_code", pa.string()),
                ("ann_date", pa.date32()),
                ("holder_name", pa.string()),
                ("holder_type", pa.string()),
                ("in_de", pa.string()),
                ("change_vol", pa.float64()),
                ("change_ratio", pa.float64()),
                ("after_share", pa.float64()),
                ("after_ratio", pa.float64()),
                ("avg_price", pa.float64()),
                ("total_fee", pa.float64()),
                ("trade_date", pa.date32()),
            ],
            "report_rc": [
                ("ts_code", pa.string()),
                ("name", pa.string()),
                ("report_date", pa.date32()),
                ("report_title", pa.string()),
                ("report_type", pa.string()),
                ("org_name", pa.string()),
                ("author_name", pa.string()),
                ("rating", pa.string()),
                ("rating_change", pa.string()),
                ("target_price", pa.float64()),
                ("industry_name", pa.string()),
                ("title_keyword", pa.string()),
            ],
        }
        for topic, cols in topics.items():
            d = data_dir() / f"raw_tushare_{topic}" / "_schema"
            d.mkdir(parents=True, exist_ok=True)
            p = d / "schema_marker.parquet"
            if not p.exists():
                tbl = pa.table({n: pa.array([], type=t) for n, t in cols})
                pq.write_table(tbl, str(p))

    def _inject_globals(self, sql: str) -> str:
        # Allow views to reference @daily_tushare@ / @adj_factor_tushare@ placeholders.
        import re
        from quant_data.paths import data_dir
        for topic in (
            "daily", "adj_factor", "daily_basic", "trade_cal", "stock_basic",
            # S-tier additions (v0.8 — ADM-652)
            "moneyflow", "moneyflow_hsgt", "index_weight", "hsgt_top10", "fund_holdings",
            # A-tier additions (v0.9 — ADM-653) Batch 1
            "index_classify", "index_daily", "index_member", "sw_index",
            "stk_limit", "suspend", "dividend", "shares_float",
            # A-tier Batch 2
            "fina_indicator", "income", "balancesheet", "cashflow",
            "fina_mainbz", "fina_audit", "top10_holders",
            # A-tier Batch 3
            "top_list", "margin_detail", "top10_floatholders",
            "stk_holdertrade", "report_rc",
        ):
            placeholder = f"@{topic}_tushare@"
            if placeholder in sql:
                glob = f"'{data_dir()}/raw_tushare_{topic}/**/*.parquet'"
                sql = sql.replace(placeholder, glob)
        return sql
